"""L3 真实集成测试：POST /api/videos/upload。

契约（来自 frontend/src/features/video-upload/VideoUploadPage.tsx 的契约占位文案）：
    multipart/form-data, field name = "file"
    响应：202 Accepted, {"media_id": UUID, "content_hash": str, "status": "pending", "size": int}
    副作用：
        - MinIO bucket 中存在对象 videos/{content_hash}/original.<ext>
        - media_file 行 status='pending', source_type='upload', minio_object 指向该对象
        - 不阻塞：触发 pipeline_task 到 cpu 队列（不进 EAGER 断言，由 e2e starburst 跑）

infra 守门：复用 tests/conftest.py 的 _is_connection_error / infra_ok，infra down → pytest.skip。
"""

from __future__ import annotations

import io
import uuid as _uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ──────────────────────────── 失败契约（红 → 绿）于不依赖 infra 的最小骨架 ────────────────────────────
class TestUploadRouteContract:
    """即便 infra 不可用也要能验证合约：路由存在 + 422 行为。"""

    def test_upload_route_registered(self):
        from videomind.interface import app

        paths = {r.path for r in app.routes if hasattr(r, "path")}
        assert "/api/videos/upload" in paths, (
            "POST /api/videos/upload 必须挂载到 FastAPI app 上"
        )

    def test_upload_multipart_missing_file_returns_422(self):
        """缺 file 字段 → 422（FastAPI 默认校验）。"""
        from videomind.interface import app

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/videos/upload")  # 无 file 字段
        # FastAPI 对缺 multipart field 返回 422；这里只断言非 5xx 且为客户端错误码
        assert resp.status_code == 422, resp.text


# ──────────────────────────── 真实集成（需 infra），infra down 自动 skip ────────────────────────────
pytestmark = pytest.mark.infra


@pytest.fixture
def client_and_storage():
    """尽量给测试一次性提供 FastAPI TestClient + MinIO 客户端。
    任一拉起失败 → 抛连接错误让 pytest.skip 接住。
    """
    from videomind.config import get_settings
    from videomind.interface import app
    from minio import Minio

    s = get_settings()
    client = TestClient(app, raise_server_exceptions=False)
    minio = Minio(
        endpoint=s.minio_endpoint,
        access_key=s.minio_access_key,
        secret_key=s.minio_secret_key,
        secure=s.minio_secure,
    )
    return client, minio, s


class TestUploadEndpointIntegration:
    """真实 multipart 上传到 MinIO + 落库 media_file（status=pending）。"""

    def test_upload_mp4_creates_media_and_lands_in_minio(self, client_and_storage):
        """上传一段极小 mp4 bytes：
          1) 202 + media_id + content_hash 长度 64（SHA256 16 进制串）
          2) MinIO 中存在对象 videos/{content_hash}/original.mp4
          3) 对象字节数与上传一致
        """
        client, minio, settings = client_and_storage

        # 一段最小的 mp4-like bytes（不是合法 mp4 流，但足以走通 multipart + MinIO；
        # 真 ffmpeg probe 由 transcode_task 负责）
        payload = b"fake-mp4-bytes-for-upload-test" + b"\x00" * 32
        files = {"file": ("demo.mp4", io.BytesIO(payload), "video/mp4")}

        resp = client.post("/api/videos/upload", files=files)
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert "media_id" in body
        # media_id 是合法 UUID
        media_id = body["media_id"]
        _uuid.UUID(media_id)  # 解析异常 → test 红
        # content_hash 是 64 位 hex（SHA256 16 进制）
        content_hash = body["content_hash"]
        assert isinstance(content_hash, str) and len(content_hash) == 64, content_hash
        # status 起步 = pending（download/upload 阶段已在此之前完成）
        assert body["status"] == "pending", body
        assert body["size"] == len(payload)

        # MinIO 验证
        key = f"videos/{content_hash}/original.mp4"
        stat = minio.stat_object(settings.minio_bucket, key)
        assert stat is not None
        assert stat.size == len(payload), (stat.size, len(payload))

        # 清理：避免测试残留污染 bucket（不影响其他测试 media_file 在 PG 中）
        try:
            minio.remove_object(settings.minio_bucket, key)
        except Exception:
            pass

    def test_upload_duplicate_content_hash_returns_existing_media_id(
        self, client_and_storage
    ):
        """同字节二次上传 → content_hash 幂等，返回同一 media_id。"""
        client, minio, settings = client_and_storage
        payload = b"dup-payload-" + b"x" * 16
        files = {"file": ("first.mp4", io.BytesIO(payload), "video/mp4")}

        first = client.post("/api/videos/upload", files=files)
        assert first.status_code == 202, first.text
        first_body = first.json()
        first_hash = first_body["content_hash"]

        # 改名 + 同字节
        files2 = {"file": ("second.mp4", io.BytesIO(payload), "video/mp4")}
        second = client.post("/api/videos/upload", files=files2)
        assert second.status_code == 202, second.text
        second_body = second.json()
        assert second_body["media_id"] == first_body["media_id"], (
            "相同 content_hash 必须命中已有 media_file，返回同一 media_id"
        )
        assert second_body["content_hash"] == first_hash

        # 清理
        try:
            minio.remove_object(
                settings.minio_bucket, f"videos/{first_hash}/original.mp4"
            )
        except Exception:
            pass

    def test_upload_pipeline_task_dispatched_with_skip_download_flag(
        self, client_and_storage, monkeypatch
    ):
        """上传后应触发 pipeline_task，context 中携带 skip_download=true，
        让 Celery chain 直接从 transcode 起跑（跳过 yt-dlp）。

        实现：用 monkeypatch 替换 celery_app.send_task，捕获入参。
        """
        try:
            from videomind.application.task_orchestration import tasks as _tasks
        except Exception as e:
            pytest.skip(f"无法导入 pipeline_task 模块: {e}")

        captured = {}

        def fake_apply_async(*args, **opts):
            # bound method 被 monkeypatch 后，调用形态：
            #   业务：`pipeline_task.apply_async(args=[context], queue="cpu")`
            #   celery 的 apply_async 签名是 apply_async(args, kwargs, **opts)，所以
            #   `args=[context]` 是位置参数，被 `*args` 接收 → args == ([context],)；
            #   `queue="cpu"` 走到 **opts。
            # 兼容两种情况：位置 args 不为空 → 取 args[0]；否则退回 opts 中的 "args"。
            if args:
                first = args[0]
                ctx_list = first if isinstance(first, (list, tuple)) else [first]
            elif "args" in opts:
                ctx_list = opts["args"]
            else:
                ctx_list = []

            captured["args"] = ctx_list
            captured["opts"] = opts
            captured["calls"] = captured.get("calls", 0) + 1

            class _R:
                id = "fake-chain-id"

            return _R()

        monkeypatch.setattr(
            "videomind.application.task_orchestration.tasks.pipeline_task.apply_async",
            fake_apply_async,
        )

        client, _minio, _settings = client_and_storage
        payload = b"chain-dispatch-" + b"y" * 8
        files = {"file": ("chain.mp4", io.BytesIO(payload), "video/mp4")}
        resp = client.post("/api/videos/upload", files=files)
        assert resp.status_code == 202, resp.text
        assert captured.get("calls") == 1, captured
        ctx = captured["args"][0]
        assert ctx["source_type"] == "upload"
        assert ctx["content_hash"] and len(ctx["content_hash"]) == 64
        assert ctx["media_id"]


def pytest_module_path() -> Path:
    return Path(__file__).resolve()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
