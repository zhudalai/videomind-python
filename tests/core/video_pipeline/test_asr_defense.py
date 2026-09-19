"""ASR 防御性双闸测试 —— 超限预检、运行时降级、错误分类、Groq 请求/响应契约。

守护契约（对应 core/video_pipeline/asr.py 方向 3.7）：
- **超限预检**：音频 > asr_api_max_audio_mb → 直接本地转写（Groq 25MB 硬限，
  不浪费注定 413 的调用）；API 一次都不调。
- **运行时降级**：API 暂时性失败（超时/传输/429/5xx）+ fallback 开 → 本地兜底；
  fallback 关 → 原样上抛。
- **确定性失败不降级**：缺配置/4xx 被拒 → NonRetryableError 原样穿透（降级救不了
  根因，静默降级反而掩盖配置错误）。
- 模型加载失败（权重下载中断）→ RetryableError（退避可恢复）。
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import httpx
import pytest

from videomind.config import get_settings
from videomind.core.errors import NonRetryableError, RetryableError
from videomind.core.video_pipeline import asr as asr_mod
from videomind.core.video_pipeline.asr import (
    ASREngine,
    ASRResult,
    _build_groq_request,
    _parse_groq_response,
    save_transcription,
)


@pytest.fixture(autouse=True)
def _reset_singletons():
    get_settings.cache_clear()
    asr_mod._asr_engine = None
    yield
    asr_mod._asr_engine = None
    get_settings.cache_clear()


def _api_engine(**overrides) -> ASREngine:
    """构造一个 provider=api 的引擎（属性直改，绕开宿主 .env 的 provider 值）。"""
    engine = ASREngine()
    engine._provider = "api"
    engine._api_base_url = "https://api.groq.com/openai/v1"
    engine._api_key = "test-key"
    engine._api_model = "whisper-large-v3-turbo"
    engine._api_max_audio_mb = 20.0
    engine._fallback_local = True
    for k, v in overrides.items():
        setattr(engine, k, v)
    return engine


def _sentinel_result() -> ASRResult:
    return ASRResult(full_text="local fallback", language="zh",
                     model_name="small", duration_sec=1.0,
                     chunks=[{"index": 0, "start_ms": 0, "end_ms": 1000, "text": "t"}])


# ──────────────────────────── 双闸之一：超限预检 ────────────────────────────


async def test_oversize_audio_skips_api_goes_local(tmp_path):
    """音频超限 → 直接本地转写，API 零调用（不烧注定 413 的请求）。"""
    engine = _api_engine(_api_max_audio_mb=0.0001)  # ~100 bytes
    engine._transcribe_local = AsyncMock(return_value=_sentinel_result())
    engine._transcribe_api = AsyncMock(side_effect=AssertionError("不应调 API"))

    audio = tmp_path / "big.wav"
    audio.write_bytes(b"x" * 1024)

    result = await engine.transcribe(audio, uuid.uuid4())

    assert result.full_text == "local fallback"
    engine._transcribe_local.assert_awaited_once()
    engine._transcribe_api.assert_not_called()


# ──────────────────────────── 双闸之二：运行时降级 ────────────────────────────


async def test_api_transient_failure_degrades_to_local(tmp_path):
    """API 暂时性失败 + fallback 开 → 本地兜底出结果（兑现 API 主力+本地兜底承诺）。"""
    engine = _api_engine()
    engine._transcribe_api = AsyncMock(side_effect=RetryableError("429 限流"))
    engine._transcribe_local = AsyncMock(return_value=_sentinel_result())

    audio = tmp_path / "a.wav"
    audio.write_bytes(b"small")

    result = await engine.transcribe(audio, uuid.uuid4())

    assert result.full_text == "local fallback"
    engine._transcribe_local.assert_awaited_once()


async def test_api_failure_raises_when_fallback_off(tmp_path):
    """fallback 关 → 暂时性失败原样上抛（交 Celery autoretry 退避）。"""
    engine = _api_engine(_fallback_local=False)
    engine._transcribe_api = AsyncMock(side_effect=RetryableError("超时"))

    audio = tmp_path / "a.wav"
    audio.write_bytes(b"small")

    with pytest.raises(RetryableError):
        await engine.transcribe(audio, uuid.uuid4())


async def test_api_nonretryable_never_degrades(tmp_path):
    """确定性失败（缺配置/4xx）不降级：降级救不了根因，静默兜底反而掩盖问题。"""
    engine = _api_engine(_api_key="")  # 缺 key → _transcribe_api 抛 NonRetryableError
    engine._transcribe_local = AsyncMock(return_value=_sentinel_result())

    audio = tmp_path / "a.wav"
    audio.write_bytes(b"small")

    with pytest.raises(NonRetryableError):
        await engine.transcribe(audio, uuid.uuid4())
    engine._transcribe_local.assert_not_called()


# ──────────────────────────── 本地模型加载失败分类 ────────────────────────────


async def test_local_model_load_failure_is_retryable(monkeypatch, tmp_path):
    """Whisper 模型加载失败（权重下载中断/CUDA OOM）→ RetryableError 而非裸异常。

    裸 RuntimeError 穿透后 autoretry 无法分类，重试耗尽也不落终态原因。
    """
    pytest.importorskip("faster_whisper")
    import faster_whisper

    def _boom(*a, **kw):
        raise RuntimeError("model weights download interrupted")

    monkeypatch.setattr(faster_whisper, "WhisperModel", _boom)
    engine = ASREngine()
    engine._provider = "local"

    with pytest.raises(RetryableError, match="模型加载失败"):
        await engine.transcribe(tmp_path / "a.wav", uuid.uuid4())


# ──────────────────────────── Groq 请求构建 ────────────────────────────


def test_build_groq_request_url_and_payload():
    """url 去尾斜杠拼接；prompt 非空才带；files 三元组（扩展名给 Groq 判格式）。"""
    url, data, files = _build_groq_request(
        "https://api.groq.com/openai/v1/", b"audio-bytes", "a.wav",
        "whisper-large-v3-turbo", "热词偏置",
    )
    assert url == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert data["model"] == "whisper-large-v3-turbo"
    assert data["response_format"] == "verbose_json"
    assert data["prompt"] == "热词偏置"
    assert files["file"] == ("a.wav", b"audio-bytes", "audio/wav")


def test_build_groq_request_omits_empty_prompt():
    """prompt 空/None → 不传（多语言中立，空串留微妙偏置）。"""
    _, data1, _ = _build_groq_request("https://x", b"b", "a.ogg", "m", None)
    _, data2, _ = _build_groq_request("https://x", b"b", "a.ogg", "m", "")
    assert "prompt" not in data1 and "prompt" not in data2


# ──────────────────────────── Groq 响应解析 ────────────────────────────


def test_parse_groq_response_full():
    """segments 秒→毫秒换算 + 文本 strip + duration 透传。"""
    result = _parse_groq_response({
        "text": "你好世界", "language": "chinese", "duration": 2.5,
        "segments": [
            {"start": 0.0, "end": 1.234, "text": " 你好 "},
            {"start": 1.234, "end": 2.5, "text": "世界"},
        ],
    }, model_name="whisper-large-v3-turbo")

    assert result.full_text == "你好世界"
    assert result.language == "chinese"
    assert result.duration_sec == 2.5
    assert result.chunks[0] == {"index": 0, "start_ms": 0, "end_ms": 1234, "text": "你好"}
    assert result.chunks[1]["end_ms"] == 2500


def test_parse_groq_response_duration_falls_back_to_last_segment():
    """duration 缺失 → 末段 end 兜底；空 segments → 0.0。"""
    r = _parse_groq_response(
        {"text": "x", "segments": [{"start": 0, "end": 1.5, "text": "x"}]},
        model_name="m",
    )
    assert r.duration_sec == 1.5

    empty = _parse_groq_response({"text": ""}, model_name="m")
    assert empty.duration_sec == 0.0 and empty.chunks == []


# ──────────────────────────── API 完整 HTTP 路径（假 httpx）────────────────────────────


class _FakeResponse:
    def __init__(self, status_code: int = 200, payload: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            req = httpx.Request("POST", "https://api.groq.com/openai/v1/audio/transcriptions")
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=req, response=self
            )

    def json(self):
        return self._payload


def _install_fake_httpx(monkeypatch, captured: dict, response=None, error=None):
    """装假 httpx.AsyncClient：post 返回固定响应或抛错，captured 记录入参。"""

    class _FakeClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None, files=None):
            captured["url"] = url
            captured["data"] = data
            captured["files"] = files
            if error is not None:
                raise error
            return response

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)


async def test_transcribe_api_success_full_http_flow(monkeypatch, tmp_path):
    """成功路径：Authorization 头 + multipart 参数 + 响应解析回 ASRResult。"""
    captured: dict = {}
    _install_fake_httpx(monkeypatch, captured, response=_FakeResponse(200, {
        "text": "hello", "language": "english", "duration": 1.0,
        "segments": [{"start": 0, "end": 1.0, "text": "hello"}],
    }))
    engine = _api_engine()
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"audio bytes")

    result = await engine._transcribe_api(audio, uuid.uuid4())

    assert result.full_text == "hello" and result.model_name == "whisper-large-v3-turbo"
    headers = captured["client_kwargs"]["headers"]
    assert headers["Authorization"] == "Bearer test-key"
    assert captured["url"].endswith("/audio/transcriptions")
    assert captured["data"]["model"] == "whisper-large-v3-turbo"
    assert captured["files"]["file"][0] == "a.wav"


@pytest.mark.parametrize("code,expect", [
    (429, RetryableError),   # 限流：退避可恢复
    (500, RetryableError),   # 服务端故障：退避可恢复
    (503, RetryableError),
    (400, NonRetryableError),  # 请求被拒：重试必然同结果
    (413, NonRetryableError),  # 超 Groq 体积限：重试必然同结果
])
async def test_transcribe_api_http_error_classification(
    monkeypatch, tmp_path, code, expect
):
    """HTTP 状态码二分：429/5xx → Retryable；4xx → NonRetryable。"""
    _install_fake_httpx(monkeypatch, {}, response=_FakeResponse(code))
    engine = _api_engine()
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")

    with pytest.raises(expect):
        await engine._transcribe_api(audio, uuid.uuid4())


async def test_transcribe_api_timeout_and_transport_are_retryable(monkeypatch, tmp_path):
    """超时 / 传输中断 → RetryableError（网络抖动语义）。"""
    engine = _api_engine()
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")

    _install_fake_httpx(monkeypatch, {}, error=httpx.TimeoutException("slow"))
    with pytest.raises(RetryableError, match="超时"):
        await engine._transcribe_api(audio, uuid.uuid4())

    _install_fake_httpx(monkeypatch, {}, error=httpx.ConnectError("refused"))
    with pytest.raises(RetryableError, match="传输失败"):
        await engine._transcribe_api(audio, uuid.uuid4())


# ──────────────────────────── save_transcription 落库编排 ────────────────────────────


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _FakeSession:
    """按序吐出 execute 结果的假 session（None=无既有行 → 走 insert 分支）。"""

    def __init__(self, results):
        self._results = list(results)
        self.added: list = []
        self.flush_count = 0

    async def execute(self, stmt):
        return _FakeResult(self._results.pop(0))

    def add(self, orm):
        self.added.append(orm)

    async def flush(self):
        self.flush_count += 1


async def test_save_transcription_inserts_new_rows():
    """全新 media：transcription + 逐 chunk 都 insert，flush 两段（先主后从）。"""
    from videomind.infrastructure.storage import models as m

    mid = uuid.uuid4()
    result = ASRResult(
        full_text="全量文本", language="zh", model_name="small", duration_sec=2.0,
        chunks=[
            {"index": 0, "start_ms": 0, "end_ms": 1000, "text": "第一段"},
            {"index": 1, "start_ms": 1000, "end_ms": 2000, "text": "第二段"},
        ],
    )
    session = _FakeSession([None, None, None])  # transcription + 2 chunks 均无既有行

    tr, chunks = await save_transcription(session, mid, result)

    assert isinstance(tr, m.Transcription)
    assert tr.full_text == "全量文本" and tr.chunk_count == 2
    assert len(chunks) == 2
    assert all(isinstance(c, m.TranscriptionChunk) for c in chunks)
    assert chunks[0].status == "completed" and chunks[0].start_ms == 0
    assert chunks[1].text == "第二段"
    assert len(session.added) == 3
    assert session.flush_count == 2


async def test_save_transcription_updates_existing_rows():
    """重跑（断点续传）：既有行原地更新而非重复 insert。"""
    from videomind.infrastructure.storage import models as m

    mid = uuid.uuid4()
    existing_tr = m.Transcription(
        media_id=mid, full_text="旧文本", language="zh",
        model_name="tiny", duration_sec=1.0, chunk_count=1,
    )
    existing_chunk = m.TranscriptionChunk(
        media_id=mid, chunk_index=0, start_ms=0, end_ms=500,
        text="旧片段", status="failed", model_name="tiny",
    )
    session = _FakeSession([existing_tr, existing_chunk])

    result = ASRResult(
        full_text="新文本", language="zh", model_name="small", duration_sec=1.0,
        chunks=[{"index": 0, "start_ms": 0, "end_ms": 1000, "text": "新片段"}],
    )
    tr, chunks = await save_transcription(session, mid, result)

    assert tr is existing_tr and tr.full_text == "新文本" and tr.model_name == "small"
    assert chunks[0] is existing_chunk
    assert chunks[0].text == "新片段" and chunks[0].status == "completed"
    assert session.added == []  # 更新路径不产生新行
