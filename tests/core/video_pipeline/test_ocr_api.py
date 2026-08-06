""":ocr.space API 路径单元测试（OCR_PROVIDER=api）—— mock httpx + mock MinIO，零基础设施。

覆盖 ``OCREngine._recognize_api`` 对 ocr.space /parse/image 的契约：
- 成功响应解析（OCRExitCode==1 + ParsedResults[].ParsedText）
- 顶层处理错误降级（IsErroredOnProcessing=true → None，不抛）
- HTTP 5xx 降级单帧（raise_for_status 抛 → 该帧 None，其余不炸）
- phash 去重：同 key 重复帧只调一次 API、标 ``[duplicate frame]``
- 空帧列表直接返回空
- 缺 API key 配置 → NotImplementedError（保留可观测的"跳过"语义，不被 generic except 静默吞）

不走网络：请求经 ``httpx.MockTransport``；MinIO 下载经假 client 写假 JPEG 字节。
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from videomind.core.video_pipeline import ocr as ocr_mod
from videomind.core.video_pipeline.ocr import OCREngine, OCRResult

# 假帧字节前缀：内容无所谓（HTTP mock 不解析图；_compute_phash 走 sha256 兜底对任意字节成立）。
# download 时拼接 key → 不同 key 产出不同字节 → 不同 phash，避免误去重（真实转码每帧内容不同）。
_FAKE_JPEG_PREFIX = b"\xff\xd8\xff\xe0\x00\x10JFSG_fake_ocr_unit_test_"

_FRAME_KEY_0 = "deadbeef/frames/frame_00000000.jpg"
_FRAME_KEY_1 = "deadbeef/frames/frame_00000001.jpg"


def _settings_stub(*, api_key: str = "K85842449788957", language: str = "auto", engine: int = 2):
    """构造仅含 OCR 相关字段的 settings 替身（api 路径 needed attrs）。"""
    return SimpleNamespace(
        ocr_provider="api",
        ocr_use_gpu=False,
        ocr_lang="ch",
        ocr_api_base_url="https://api.ocr.space",
        ocr_api_key=api_key,
        ocr_api_engine=engine,
        ocr_api_language=language,
        ocr_timeout_s=60.0,
    )


class _FakeMinio:
    """假 MinIO：download_file 把假字节写到目标路径，不开网络。"""

    def __init__(self) -> None:
        self.downloaded_keys: list[str] = []

    async def download_file(self, key: str, target) -> None:
        self.downloaded_keys.append(key)
        # key 拼入字节 → 不同 key 不同 phash（同 key 两次相同 → 仍触发去重，dedup 测试依赖此点）
        target.write_bytes(_FAKE_JPEG_PREFIX + key.encode())


def _mock_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=60.0)


def _install(monkeypatch, settings, fake_minio: _FakeMinio) -> None:
    monkeypatch.setattr(ocr_mod, "get_settings", lambda: settings)
    monkeypatch.setattr(ocr_mod, "get_minio_client", lambda: fake_minio)


# ──────────────────────────── 用例 ────────────────────────────


async def test_api_single_frame_success(monkeypatch):
    """成功响应：OCRExitCode=1 + ParsedText → OCRResult.ocr_text 取 ParsedText。"""
    seen_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        return httpx.Response(
            200,
            json={
                "OCRExitCode": 1,
                "IsErroredOnProcessing": False,
                "ParsedResults": [
                    {"FileParseExitCode": 1, "ParsedText": "Sales 2026 销售额 +58%\n"}
                ],
            },
        )

    fake_minio = _FakeMinio()
    _install(monkeypatch, _settings_stub(), fake_minio)
    client = _mock_client(handler)
    try:
        engine = OCREngine(http_client=client)
        results = await engine.recognize_frames(_uuid(), [_FRAME_KEY_0])
    finally:
        await client.aclose()

    assert len(results) == 1
    r = results[0]
    assert isinstance(r, OCRResult)
    assert r.model_name == "ocr.space"
    assert r.ocr_text == "Sales 2026 销售额 +58%"  # 已 strip
    assert r.minio_object == _FRAME_KEY_0
    assert r.frame_ms == 0  # frame_00000000 * 1000ms
    assert r.phash is not None

    # 契约校验：打到了 /parse/image，表单含 base64Image + apikey + language + OCREngine
    assert seen_requests and seen_requests[0].url.path == "/parse/image"
    body = seen_requests[0].content.decode()
    assert "base64Image=" in body
    assert "apikey=K85842449788957" in body
    assert "language=auto" in body
    assert "OCREngine=2" in body
    assert "isOverlayRequired=false" in body


async def test_api_processing_error_degrades_to_none(monkeypatch):
    """ocr.space 顶层 IsErroredOnProcessing=true → 该帧 ocr_text=None，不抛。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "IsErroredOnProcessing": True,
                "ErrorMessage": "Could not analyze the image",
                "ParsedResults": [{"FileParseExitCode": -1, "ParsedText": ""}],
            },
        )

    fake_minio = _FakeMinio()
    _install(monkeypatch, _settings_stub(), fake_minio)
    client = _mock_client(handler)
    try:
        engine = OCREngine(http_client=client)
        results = await engine.recognize_frames(_uuid(), [_FRAME_KEY_0])
    finally:
        await client.aclose()

    assert len(results) == 1
    assert results[0].ocr_text is None
    assert results[0].model_name == "ocr.space"


async def test_api_http_500_degrades_single_frame(monkeypatch):
    """HTTP 5xx → raise_for_status 抛 → 被捕 → 该帧 None，不抛到外层。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    fake_minio = _FakeMinio()
    _install(monkeypatch, _settings_stub(), fake_minio)
    client = _mock_client(handler)
    try:
        engine = OCREngine(http_client=client)
        results = await engine.recognize_frames(_uuid(), [_FRAME_KEY_0, _FRAME_KEY_1])
    finally:
        await client.aclose()

    assert len(results) == 2
    # 两帧均降级为空（每帧独立降级，不互相牵连、不炸整批）
    assert all(r.ocr_text is None for r in results)
    assert all(r.model_name == "ocr.space" for r in results)


async def test_api_duplicate_frame_skips_call(monkeypatch):
    """同 key 重复帧：第二帧标 [duplicate frame]，API 只调一次。"""
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        return httpx.Response(200, json={
            "OCRExitCode": 1, "IsErroredOnProcessing": False,
            "ParsedResults": [{"FileParseExitCode": 1, "ParsedText": "hello"}],
        })

    fake_minio = _FakeMinio()
    _install(monkeypatch, _settings_stub(), fake_minio)
    client = _mock_client(handler)
    try:
        engine = OCREngine(http_client=client)
        # 同一帧 key 传两次
        results = await engine.recognize_frames(_uuid(), [_FRAME_KEY_0, _FRAME_KEY_0])
    finally:
        await client.aclose()

    assert len(results) == 2
    assert results[0].ocr_text == "hello"  # 第一帧真识别
    assert results[1].ocr_text == "[duplicate frame]"  # 第二帧去重
    assert results[1].phash == results[0].phash
    assert results[1].model_name == "ocr.space"
    # 关键：重复帧没打 API
    assert call_count["n"] == 1


async def test_api_empty_frame_keys_returns_empty(monkeypatch):
    """空帧列表 → 直接返回空，不碰网络/MinIO。"""
    fake_minio = _FakeMinio()
    _install(monkeypatch, _settings_stub(), fake_minio)
    client = _mock_client(lambda req: httpx.Response(200, json={}))
    try:
        engine = OCREngine(http_client=client)
        results = await engine.recognize_frames(_uuid(), [])
    finally:
        await client.aclose()
    assert results == []
    assert fake_minio.downloaded_keys == []


async def test_api_missing_key_raises_not_implemented(monkeypatch):
    """缺 OCR_API_KEY → NotImplementedError（保留"跳过"语义，不被 generic except 静默吞）。"""
    fake_minio = _FakeMinio()
    _install(monkeypatch, _settings_stub(api_key=""), fake_minio)
    # 注意：即便缺 key 也不该建连接/发请求；client 用会断言不被调的 handler
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("缺 key 不应发请求")
    client = _mock_client(handler)
    try:
        engine = OCREngine(http_client=client)
        with pytest.raises(NotImplementedError):
            await engine.recognize_frames(_uuid(), [_FRAME_KEY_0])
    finally:
        await client.aclose()


async def test_api_multi_frame_success(monkeypatch):
    """多帧成功：frame_ms 按 key 解析、顺序与输入一致。"""
    responses = iter([
        httpx.Response(200, json={
            "OCRExitCode": 1, "IsErroredOnProcessing": False,
            "ParsedResults": [{"FileParseExitCode": 1, "ParsedText": "frame0 text"}],
        }),
        httpx.Response(200, json={
            "OCRExitCode": 1, "IsErroredOnProcessing": False,
            "ParsedResults": [{"FileParseExitCode": 1, "ParsedText": "frame1 text"}],
        }),
    ])

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses)

    fake_minio = _FakeMinio()
    _install(monkeypatch, _settings_stub(), fake_minio)
    client = _mock_client(handler)
    try:
        engine = OCREngine(http_client=client)
        results = await engine.recognize_frames(_uuid(), [_FRAME_KEY_0, _FRAME_KEY_1])
    finally:
        await client.aclose()

    assert [r.frame_ms for r in results] == [0, 1000]
    assert [r.ocr_text for r in results] == ["frame0 text", "frame1 text"]
    assert all(r.model_name == "ocr.space" for r in results)


def _uuid():
    import uuid
    return uuid.uuid4()
