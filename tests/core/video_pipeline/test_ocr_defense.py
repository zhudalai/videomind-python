"""OCR 阶段防御性测试 —— 帧去重、单帧降级、_extract_texts 双格式、落库去重。

守护契约（对应 core/video_pipeline/ocr.py）：
- phash 去重：相同帧只识别一次（省 paddle 算力 / ocr.space 配额），重复帧标
  "[duplicate frame]" 不调识别。
- 单帧失败降级空（不炸整批）：API 单帧异常 → 该帧 ocr_text=None，其余帧继续。
- _extract_texts 兼容 PaddleOCR 新旧两版输出（<3.x 嵌套 list / >=3.x rec_texts）。
- save_frame_ocr 批内 frame_ms 去重 + 既有行更新不重复 insert。
- 注入的 http_client 不被引擎关闭（owns_client 语义）。
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from videomind.config import get_settings
from videomind.core.video_pipeline import ocr as ocr_mod
from videomind.core.video_pipeline.ocr import (
    OCREngine,
    OCRResult,
    _compute_phash,
    _download_and_dedup,
    _extract_texts,
    _frame_ms_from_key,
    _FrameFile,
    _unlink_quiet,
    save_frame_ocr,
)


@pytest.fixture(autouse=True)
def _fresh_settings():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ──────────────────────────── _extract_texts 双格式 ────────────────────────────


def test_extract_texts_new_format_rec_texts():
    """PaddleOCR >=3.x：Result.rec_texts 逐页展开；rec_texts=None 安全跳过。"""
    raw = [
        SimpleNamespace(rec_texts=["标题", "正文第一行"]),
        SimpleNamespace(rec_texts=None),  # 该页无文字
        SimpleNamespace(rec_texts=["末行"]),
    ]
    assert _extract_texts(raw) == ["标题", "正文第一行", "末行"]


def test_extract_texts_old_format_nested_list():
    """PaddleOCR <3.x：page=[line]，line=[bbox,(text,score)]；残缺行跳过。"""
    raw = [
        [
            [[[0, 0], [1, 1]], ("识别文本", 0.99)],
            [[[0, 2], [1, 3]], None],          # 无 (text, score) → 跳过
            [[[0, 4]]],                        # 长度不足 → 跳过
        ],
        [
            [[[2, 0], [3, 1]], ("第二页", 0.88)],
        ],
    ]
    assert _extract_texts(raw) == ["识别文本", "第二页"]


def test_extract_texts_empty_and_none():
    assert _extract_texts([]) == []
    assert _extract_texts(None) == []


# ──────────────────────────── 帧键 / 清理 / phash 兜底 ────────────────────────────


def test_frame_ms_from_key_parsing():
    """1fps 下第 N 帧 = N*1000ms；非 frame_ 前缀/非数字 → 0（防御）。"""
    assert _frame_ms_from_key("hash/frames/frame_00000005.jpg") == 5000
    assert _frame_ms_from_key("frame_00000000.jpg") == 0
    assert _frame_ms_from_key("frame_abc.jpg") == 0
    assert _frame_ms_from_key("random.jpg") == 0


def test_unlink_quiet_removes_and_tolerates_missing(tmp_path):
    f = tmp_path / "x.jpg"
    f.write_bytes(b"1")
    _unlink_quiet(f)
    assert not f.exists()
    _unlink_quiet(f)  # 不存在也不抛


async def test_compute_phash_falls_back_to_sha256(monkeypatch, tmp_path):
    """imagehash 缺失 → 内容 SHA256 前 16 字符兜底（去重仍可用）。"""
    monkeypatch.setitem(sys.modules, "imagehash", None)
    f = tmp_path / "f.jpg"
    f.write_bytes(b"frame-content")

    import hashlib

    assert await _compute_phash(f) == hashlib.sha256(b"frame-content").hexdigest()[:16]


# ──────────────────────────── 下载去重 ────────────────────────────


async def test_download_and_dedup_marks_duplicate_frames(monkeypatch, tmp_path):
    """同 phash 帧标 is_duplicate：第一帧保留，后续重复帧跳过识别。"""
    downloads: list[str] = []

    class _FakeMinio:
        async def download_file(self, key, local):
            downloads.append(key)
            Path(local).write_bytes(b"x")

    monkeypatch.setattr(ocr_mod, "get_minio_client", lambda: _FakeMinio())
    # 控制 phash：帧1/帧2 相同，帧3 不同
    phashes = iter(["p1", "p1", "p2"])
    monkeypatch.setattr(
        ocr_mod, "_compute_phash", AsyncMock(side_effect=lambda p: next(phashes))
    )

    mid = uuid.uuid4()
    frames = await _download_and_dedup(mid, ["k/f1.jpg", "k/f2.jpg", "k/f3.jpg"])

    assert [f.is_duplicate for f in frames] == [False, True, False]
    assert [f.key for f in frames] == ["k/f1.jpg", "k/f2.jpg", "k/f3.jpg"]
    assert downloads == ["k/f1.jpg", "k/f2.jpg", "k/f3.jpg"]  # 都下载，识别才省


# ──────────────────────────── API 路径 ────────────────────────────


def _api_engine(monkeypatch, **env):
    defaults = {
        "OCR_API_BASE_URL": "https://api.ocr.space",
        "OCR_API_KEY": "k",
        "OCR_API_LANGUAGE": "auto",
        "OCR_API_ENGINE": "2",
    }
    defaults.update(env)
    for k, v in defaults.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()


def _frame(key: str, tmp_path: Path, dup: bool = False) -> _FrameFile:
    local = tmp_path / Path(key).name
    local.write_bytes(b"jpg")
    return _FrameFile(key=key, local=local, phash="p", is_duplicate=dup)


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.posts: list[dict] = []
        self.closed = False

    async def post(self, url, data=None, **kw):
        self.posts.append({"url": url, "data": data})
        if self._error is not None:
            raise self._error
        return self._response

    async def aclose(self):
        self.closed = True


async def test_recognize_frames_empty_keys_noop():
    """空帧列表直接返回，不触碰任何依赖。"""
    assert await OCREngine().recognize_frames(uuid.uuid4(), []) == []


async def test_recognize_api_missing_config_raises(monkeypatch):
    """provider=api 但缺 base_url/key → NotImplementedError（带修复指引，非裸异常）。"""
    _api_engine(monkeypatch, OCR_API_BASE_URL="", OCR_API_KEY="")
    engine = OCREngine()
    engine._provider = "api"
    with pytest.raises(NotImplementedError, match="OCR_API"):
        await engine.recognize_frames(uuid.uuid4(), ["k/f1.jpg"])


async def test_recognize_api_full_flow_and_duplicate_skip(monkeypatch, tmp_path):
    """正常帧调 API 得文本；重复帧标 [duplicate frame] 且零 API 调用。"""
    _api_engine(monkeypatch)
    client = _FakeClient(response=_FakeResponse({
        "IsErroredOnProcessing": False,
        "ParsedResults": [{"FileParseExitCode": 1, "ParsedText": " 帧内文字 "}],
    }))
    frames = [
        _frame("h/f1.jpg", tmp_path),
        _frame("h/f2.jpg", tmp_path, dup=True),
    ]
    monkeypatch.setattr(
        ocr_mod, "_download_and_dedup", AsyncMock(return_value=frames)
    )

    engine = OCREngine(http_client=client)
    engine._provider = "api"
    results = await engine.recognize_frames(uuid.uuid4(), ["h/f1.jpg", "h/f2.jpg"])

    assert results[0].ocr_text == "帧内文字" and results[0].model_name == "ocr.space"
    assert results[1].ocr_text == "[duplicate frame]"
    assert len(client.posts) == 1  # 重复帧不调 API
    # 注入的 client 由调用方持有，引擎不得关闭
    assert client.closed is False
    # 请求参数契约：apikey/language/engine 透传 + base64 图像
    data = client.posts[0]["data"]
    assert data["apikey"] == "k" and data["language"] == "auto"
    assert data["OCREngine"] == "2"
    assert data["base64Image"].startswith("data:image/jpeg;base64,")
    # 临时帧文件已清理
    assert not frames[0].local.exists() and not frames[1].local.exists()


async def test_recognize_api_single_frame_failure_degrades(monkeypatch, tmp_path):
    """单帧 API 异常 → 该帧 ocr_text=None，其余帧照常（不炸整批）。"""
    _api_engine(monkeypatch)
    responses = iter([
        httpx.ConnectError("boom"),
        _FakeResponse({
            "IsErroredOnProcessing": False,
            "ParsedResults": [{"FileParseExitCode": 1, "ParsedText": "OK"}],
        }),
    ])

    class _SeqClient(_FakeClient):
        async def post(self, url, data=None, **kw):
            self.posts.append({"url": url, "data": data})
            item = next(responses)
            if isinstance(item, Exception):
                raise item
            return item

    client = _SeqClient()
    frames = [_frame("h/a.jpg", tmp_path), _frame("h/b.jpg", tmp_path)]
    monkeypatch.setattr(
        ocr_mod, "_download_and_dedup", AsyncMock(return_value=frames)
    )

    engine = OCREngine(http_client=client)
    engine._provider = "api"
    results = await engine.recognize_frames(uuid.uuid4(), ["h/a.jpg", "h/b.jpg"])

    assert results[0].ocr_text is None  # 失败帧降级空
    assert results[1].ocr_text == "OK"  # 其余帧照常


def test_call_ocr_space_error_semantics():
    """ocr.space 错误语义：IsErroredOnProcessing / FileParseExitCode!=1 → None。"""
    # 顶层处理错误
    r = OCREngine()
    resp_err = _FakeResponse({"IsErroredOnProcessing": True, "ErrorMessage": "bad"})
    assert asyncio_run_parse(r, resp_err) is None
    # 页级解析失败被过滤
    resp_page = _FakeResponse({
        "IsErroredOnProcessing": False,
        "ParsedResults": [{"FileParseExitCode": 0, "ParsedText": "x"}],
    })
    assert asyncio_run_parse(r, resp_page) is None
    # 空白文本 → None（不产空串）
    resp_blank = _FakeResponse({
        "IsErroredOnProcessing": False,
        "ParsedResults": [{"FileParseExitCode": 1, "ParsedText": "   "}],
    })
    assert asyncio_run_parse(r, resp_blank) is None


def asyncio_run_parse(engine, response):
    """同步驱动 _call_ocr_space：fake client.post 直接返回给定响应。"""
    import tempfile

    import anyio

    async def _go():
        client = _FakeClient(response=response)
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"jpg")
            local = Path(f.name)
        try:
            return await engine._call_ocr_space(
                client, "https://api.ocr.space/parse/image", local, get_settings()
            )
        finally:
            local.unlink(missing_ok=True)

    return anyio.run(_go)


# ──────────────────────────── save_frame_ocr 落库 ────────────────────────────


class _ScalarsResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self._rows


class _FakeSession:
    def __init__(self, existing):
        self._existing = existing
        self.added: list = []
        self.flush_count = 0

    async def execute(self, stmt):
        return _ScalarsResult(self._existing)

    def add(self, orm):
        self.added.append(orm)

    async def flush(self):
        self.flush_count += 1


async def test_save_frame_ocr_insert_update_and_batch_dedup():
    """既有行更新不重复 insert；批内同 frame_ms 只留第一个（唯一约束防线）。"""
    from videomind.infrastructure.storage import models as m

    mid = uuid.uuid4()
    existing = SimpleNamespace(
        frame_ms=1000, ocr_text="旧", phash="p0", model_name="old", status="completed"
    )
    session = _FakeSession([existing])

    results = [
        OCRResult(frame_ms=1000, minio_object="k/f1.jpg", ocr_text="新文本",
                  phash="p1", model_name="ocr.space"),
        OCRResult(frame_ms=2000, minio_object="k/f2.jpg", ocr_text="第二帧",
                  phash="p2", model_name="ocr.space"),
        # 批内重复 frame_ms：只处理第一个（防唯一约束冲突）
        OCRResult(frame_ms=2000, minio_object="k/f2b.jpg", ocr_text="重复帧",
                  phash="p3", model_name="ocr.space"),
    ]
    orms = await save_frame_ocr(session, mid, results)

    assert len(orms) == 2  # 批内重复的第三条被丢弃
    # 既有行原地更新
    assert orms[0] is existing and existing.ocr_text == "新文本" and existing.phash == "p1"
    # 新行 insert
    assert len(session.added) == 1
    assert isinstance(session.added[0], m.FrameOCR)
    assert session.added[0].frame_ms == 2000 and session.added[0].status == "completed"
