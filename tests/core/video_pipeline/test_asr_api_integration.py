"""ASR API 路径真实集成测试 —— 真调 Groq transcription 端点。

L3 真集成：依赖 ASR_API_BASE_URL + ASR_API_KEY 配置；未配则 skip（守门）。
不依赖 GPU/容器，只测 API 路径本身。用合成 wav（ffmpeg 生成静音/正弦）减少变量，
但 Groq 对纯静音可能返回空 segments —— 重点验证端到端不崩 + ASRResult shape 正确。

音频素材用 test_video_path fixture 经 transcode 抽出来的 wav（与 local 集成测一致）。
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from videomind.config import get_settings
from videomind.core.video_pipeline import asr as asr_mod


@pytest.mark.infra
class TestASRApiIntegration:
    """Groq transcription 真集成（守门：key 未配则 skip）。"""

    async def test_transcribe_via_groq_returns_asr_result(self, test_video_path, tmp_path):
        """通过真 Groq 端点转录 → 返回合法 ASRResult（端到端不崩 + shape 正确）。"""
        s = get_settings()
        if not s.asr_api_base_url or not s.asr_api_key:
            pytest.skip("ASR_PROVIDER=api 集成测试需配置 ASR_API_BASE_URL + ASR_API_KEY")

        pytest.importorskip("httpx")

        # 复用 transcode 抽音频（与 local 集成测同一 wav 来源）
        from videomind.core.video_pipeline.transcode import get_transcoder
        transcoder = get_transcoder()
        content_hash = uuid.uuid4().hex[:32]
        tc_result = await transcoder.execute(Path(test_video_path), content_hash)

        asr_mod._asr_engine = None
        get_settings.cache_clear()
        engine = asr_mod.ASREngine()
        engine._provider = "api"  # 强制 api 路径，独立于 .env 的 ASR_PROVIDER

        try:
            result = await engine.transcribe(tc_result.audio_local, uuid.uuid4())
        except Exception as e:
            if _is_net_error(e):
                pytest.skip(f"Groq 不可达: {e}")
            raise

        from videomind.core.video_pipeline.asr import ASRResult
        assert isinstance(result, ASRResult)
        assert isinstance(result.full_text, str)
        assert isinstance(result.language, str)
        assert result.model_name == s.asr_api_model
        assert isinstance(result.chunks, list)


def _is_net_error(exc: BaseException) -> bool:
    """复用 conftest 的连接判定，独立成局部函数避免 import 循环。"""
    msg = str(exc).lower()
    if any(k in msg for k in (
        "connect", "timeout", "name or service not known", "no route to host",
        "connection reset", "broken pipe", "eof",
    )):
        return True
    return isinstance(exc, (ConnectionError, TimeoutError, OSError))
