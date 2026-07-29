"""ASREngine 纯单元测试 —— 验证 transcribe 调用参数（质量改造的行为契约）。

不真跑 faster-whisper：monkeypatch 替换 WhisperModel，捕获 transcribe 实际拿到的
kwargs，断言质量相关的关键参数被正确设置。这是 L1 零外部依赖测试。

守护的契约（对应 .env tiny→large-v3-turbo + asr.py 反幻觉改造）：
- condition_on_previous_text=False：faster-whisper 默认 True 会用上一段文本当下文，
  跨段幻觉传染（小模型 / 多语言场景上乱字串主因之一），显式覆盖为 False。
- vad_filter=True：保留 VAD 过滤（静音段不喂模型，减幻觉 + 加速）。
- language=None：保持自动检测（多语言契约，禁止硬编码单一语言）。
- initial_prompt 从 config.asr_initial_prompt 透传：默认空 → 传 None（保持多语言
  中立，空串会留下微妙偏置）；配了热词 → 原样透传做领域词偏置。
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from videomind.config import get_settings
from videomind.core.video_pipeline import asr as asr_mod


@pytest.fixture(autouse=True)
def _reset_singletons():
    """每个测试用全新 Settings + ASREngine 单例，避免缓存串台。"""
    get_settings.cache_clear()
    asr_mod._asr_engine = None
    yield
    asr_mod._asr_engine = None
    get_settings.cache_clear()


def _install_fake_whisper(monkeypatch, captured: dict) -> None:
    """装一个假 WhisperModel：__init__ 记录、transcribe 返回假 segments + 捕获 kwargs。"""
    import faster_whisper  # 装了才跑；没装这条 import 会 raise，由 importorskip 兜底

    class _Seg:
        text = "hello world"
        start = 0.0
        end = 1.5

    class _Info:
        language = "en"

    class _FakeModel:
        def transcribe(self, audio_path, **kwargs):
            captured["audio_path"] = audio_path
            captured["kwargs"] = kwargs
            return iter([_Seg()]), _Info()

    def _factory(model_name, **kwargs):
        captured["init_model"] = model_name
        captured["init_kwargs"] = kwargs
        return _FakeModel()

    monkeypatch.setattr(faster_whisper, "WhisperModel", _factory)


async def _transcribe(engine: asr_mod.ASREngine, audio_path: Path) -> asr_mod.ASRResult:
    """run_sync 包一层 await：anyio.to_thread.run_sync 在事件循环里跑。

    强制 _provider='local'：契约测试验的是 local 路径 transcribe 参数，
    独立于 .env 的 ASR_PROVIDER（避免运行时切 api 让 local 契约测试误红）。
    """
    engine._provider = "local"
    return await engine.transcribe(audio_path, uuid.uuid4())


# ── 守护契约 1：反幻觉（最核心的质量修复）──
async def test_transcribe_disables_cross_segment_hallucination(monkeypatch, tmp_path):
    """condition_on_previous_text 必须为 False。

    失败场景：asr.py 漏传该参数 → faster-whisper 走默认 True → 跨段幻觉传染。
    """
    pytest.importorskip("faster_whisper")
    captured: dict = {}
    _install_fake_whisper(monkeypatch, captured)

    engine = asr_mod.ASREngine()
    await _transcribe(engine, tmp_path / "dummy.wav")

    assert captured["kwargs"].get("condition_on_previous_text") is False


# ── 守护契约 2：VAD 保留（防误关）──
async def test_transcribe_keeps_vad_filter_on(monkeypatch, tmp_path):
    """vad_filter 必须为 True：静音段不喂模型，减幻觉 + 加速。"""
    pytest.importorskip("faster_whisper")
    captured: dict = {}
    _install_fake_whisper(monkeypatch, captured)

    engine = asr_mod.ASREngine()
    await _transcribe(engine, tmp_path / "dummy.wav")

    assert captured["kwargs"].get("vad_filter") is True


# ── 守护契约 3：多语言中立（禁止硬编码 language）──
async def test_transcribe_keeps_automatic_language_detection(monkeypatch, tmp_path):
    """language 必须为 None（自动检测）。多语言契约：不能硬编码单一语言。"""
    pytest.importorskip("faster_whisper")
    captured: dict = {}
    _install_fake_whisper(monkeypatch, captured)

    engine = asr_mod.ASREngine()
    await _transcribe(engine, tmp_path / "dummy.wav")

    # 没传 / 传 None 都算自动检测
    assert captured["kwargs"].get("language") is None


# ── 守护契约 4：initial_prompt 默认空 → None（多语言中立，不传空串偏置）──
async def test_transcribe_empty_initial_prompt_passes_none(monkeypatch, tmp_path):
    """ASR_INITIAL_PROMPT 未设（默认空）→ initial_prompt 传 None，不传空串。"""
    pytest.importorskip("faster_whisper")
    captured: dict = {}
    _install_fake_whisper(monkeypatch, captured)

    engine = asr_mod.ASREngine()
    await _transcribe(engine, tmp_path / "dummy.wav")

    assert captured["kwargs"].get("initial_prompt") is None


# ── 守护契约 5：配了热词 → 原样透传（领域词偏置）──
async def test_transcribe_forwards_initial_prompt_from_config(monkeypatch, tmp_path):
    """ASR_INITIAL_PROMPT 设了热词 → transcribe 收到的 initial_prompt 原样等于该值。"""
    pytest.importorskip("faster_whisper")
    monkeypatch.setenv("ASR_INITIAL_PROMPT", "VideoMind, RAG, Whisper, 语音识别")
    get_settings.cache_clear()  # 让 setenv 生效

    captured: dict = {}
    _install_fake_whisper(monkeypatch, captured)

    engine = asr_mod.ASREngine()
    await _transcribe(engine, tmp_path / "dummy.wav")

    assert captured["kwargs"].get("initial_prompt") == "VideoMind, RAG, Whisper, 语音识别"


# ── 守护契约 6：engine 透传 config 模型名（既有集成测试曾硬编码 tiny，这里防死）──
async def test_engine_reads_model_name_from_settings(monkeypatch, tmp_path):
    """ASR_MODEL 改值后，engine 透传到 WhisperModel 构造器，不硬编码。"""
    pytest.importorskip("faster_whisper")
    monkeypatch.setenv("ASR_MODEL", "large-v3-turbo")
    get_settings.cache_clear()

    captured: dict = {}
    _install_fake_whisper(monkeypatch, captured)

    engine = asr_mod.ASREngine()
    await _transcribe(engine, tmp_path / "dummy.wav")

    assert captured["init_model"] == "large-v3-turbo"
