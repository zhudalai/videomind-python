"""punctuate 单元测试 —— 守护 LLM 加标点（方案 2）行为契约。

设计：punctuate.py 抽纯函数 + 注入 client（不读 config、不碰网络）。
单测用 AsyncMock client，三态：干净通过 / 偷改字回退裸文本 / 异常回退裸文本。

守护契约（对应方案 2：ASR 裸文本 → LLM 加标点恢复，解决 Whisper 中文不产标点）：
- 干净：LLM 只加标点不改字（去标点后字符序 == 原文）→ full_text 带标点，ok=True
- 防改字：LLM 偷改/删字（去标点后 != 原文）→ 回退裸 full_text，ok=False（防 LLM 静默改字进库）
- 异常：LLM 抛错/超时 → 回退裸 full_text 不向上抛，ok=False（加标点不该阻塞 ASR 主流程）
- 空文本：full_text 空 → 不调 LLM，直接回退（省一次推理）
- chunks 始终不动：标点只给展示用 full_text，检索/时间戳对齐用的 chunks 保持裸文本 + 时间戳
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from videomind.core.video_pipeline.asr import ASRResult
from videomind.core.model_gateway.types import ChatResponse, TokenUsage


# ────────────── fixtures ──────────────

RAW = "为什么会有越来越多人选择游轮这种旅行方式那我们今天就要从吃喝住行玩价格五个方面"
# 干净：只加「？」「、」，字符序不变
PUNCT_CLEAN = "为什么会有越来越多人选择游轮这种旅行方式？那我们今天就要从吃喝住行玩、价格五个方面"
# 改字：删了「会」字 → 去标点后 != RAW
PUNCT_CHANGED = "为什么有越来越多人选择游轮这种旅行方式？那我们今天就要从吃喝住行玩价格五个方面"


def _result(full_text: str, chunks: list[dict] | None = None) -> ASRResult:
    return ASRResult(
        full_text=full_text,
        language="zh",
        model_name="whisper-large-v3-turbo",
        duration_sec=3.0,
        chunks=chunks if chunks is not None else [],
    )


def _client_returning(content: str) -> MagicMock:
    client = MagicMock()
    client.chat = AsyncMock(return_value=ChatResponse(
        content=content, model="ling", usage=TokenUsage(), finish_reason="stop",
    ))
    return client


def _client_raising(exc: Exception) -> MagicMock:
    client = MagicMock()
    client.chat = AsyncMock(side_effect=exc)
    return client


# ────────────── punctuate_text 三态 ──────────────

async def test_clean_punctuation_passes_through():
    """干净：只加标点不改字 → 返回带标点文本，ok=True。"""
    from videomind.core.video_pipeline.punctuate import punctuate_text
    c = _client_returning(PUNCT_CLEAN)
    out, ok = await punctuate_text(RAW, c, "inclusionai/ling-3.0-flash:free")
    assert ok is True
    assert out == PUNCT_CLEAN


async def test_changed_chars_falls_back_to_raw():
    """改字：LLM 偷删了字 → ok=False，回退原文（防改字静默进库）。"""
    from videomind.core.video_pipeline.punctuate import punctuate_text
    c = _client_returning(PUNCT_CHANGED)
    out, ok = await punctuate_text(RAW, c, "ling")
    assert ok is False
    assert out == RAW


async def test_llm_exception_falls_back_to_raw():
    """异常：LLM 抛错 → ok=False，回退原文，不向上抛（加标点不阻塞 ASR）。"""
    from videomind.core.video_pipeline.punctuate import punctuate_text
    c = _client_raising(RuntimeError("boom"))
    out, ok = await punctuate_text(RAW, c, "ling")
    assert ok is False
    assert out == RAW


async def test_empty_text_skips_llm_call():
    """空文本：full_text 空 → 不调 LLM，直接回退。"""
    from videomind.core.video_pipeline.punctuate import punctuate_text
    c = _client_returning("should not be called")
    out, ok = await punctuate_text("", c, "ling")
    assert ok is False
    assert out == ""
    c.chat.assert_not_called()


async def test_language_neutral_prompt_does_not_pin_language():
    """prompt 不含硬编码语言指令 → 让模型按文本本身处理（多语言中立契约）。"""
    from videomind.core.video_pipeline.punctuate import punctuate_text
    c = _client_returning(PUNCT_CLEAN)
    await punctuate_text(RAW, c, "ling")
    req = c.chat.await_args.args[0]
    user_msg = req.messages[-1]["content"]
    # 不夹带"翻译成中文""译为英文"等指令
    for forbidden in ("翻译", "译为", "translate", "convert to english"):
        assert forbidden not in user_msg.lower().replace("翻译", "翻译"), \
            f"prompt 夹带了语言指令: {forbidden}"


# ────────────── punctuate_result ──────────────

async def test_result_full_text_punctuated_chunks_unchanged():
    """result 加工：full_text 带标点，chunks 字段原样不动（检索/对齐零影响）。"""
    from videomind.core.video_pipeline.punctuate import punctuate_result
    chunks = [
        {"index": 0, "start_ms": 0, "end_ms": 3260, "text": "为什么会有越来越多人选择游轮这种旅行方式"},
        {"index": 1, "start_ms": 3260, "end_ms": 5740, "text": "那我们今天就要从吃喝住行玩价格五个方面"},
    ]
    r = _result(RAW, chunks)
    c = _client_returning(PUNCT_CLEAN)
    out = await punctuate_result(r, c, "ling")
    assert out.full_text == PUNCT_CLEAN
    assert out.chunks == chunks
    # 其余字段透传不变
    assert out.language == "zh"
    assert out.model_name == "whisper-large-v3-turbo"
    assert out.duration_sec == 3.0


async def test_result_empty_full_text_skips_llm():
    """result full_text 空 → 不调 LLM，原样返回。"""
    from videomind.core.video_pipeline.punctuate import punctuate_result
    r = _result("", [])
    c = _client_returning("x")
    out = await punctuate_result(r, c, "ling")
    assert out.full_text == ""
    c.chat.assert_not_called()


async def test_result_on_failure_keeps_raw_full_text():
    """改字/异常时 result 的 full_text 保留裸原文，chunks 同样不动。"""
    from videomind.core.video_pipeline.punctuate import punctuate_result
    chunks = [{"index": 0, "start_ms": 0, "end_ms": 1000, "text": "abc"}]
    r = _result(RAW, chunks)
    c = _client_raising(RuntimeError("net down"))
    out = await punctuate_result(r, c, "ling")
    assert out.full_text == RAW
    assert out.chunks == chunks
