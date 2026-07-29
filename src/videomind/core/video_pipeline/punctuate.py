"""ASR 后处理：LLM 加中文标点（方案 2）。

为什么单独成模块：Whisper 系列（Groq / OpenAI 兼容各档）在中文上不产出标点，
裸文本可读性差。用轻量 LLM 给全文补标点恢复可读性，不动 chunks。

设计要点（对应 tests/core/video_pipeline/test_punctuate_unit.py 契约）：
1. **纯函数 + 注入 client**：不读 config、不碰网络，方便单测用 AsyncMock 三态。
   调用方（tasks.py）负责读 config 决定开关 + 构造 OpenAICompatibleClient。
2. **只改 full_text**：加标点只提升展示可读性；chunks 保留裸文本 + 时间戳，
   检索/对齐层零影响（标点不影响向量召回）。
3. **防改字回退**：去标点后字符序 != 原文 → 回退裸原文（防 LLM 偷改字静默进库）。
4. **异常回退**：LLM 抛错/超时 → 回退裸原文，不向上抛（加标点不阻塞 ASR 主流程）。
5. **空文本早退**：full_text 空 → 不调 LLM 省一次推理。
6. **多语言中立 prompt**：不夹"翻译成中文"等语言指令，让模型按文本本身处理（多语言契约）。
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Protocol

from videomind.core.video_pipeline.asr import ASRResult
from videomind.core.model_gateway.types import ChatRequest

# 移除集：中文标点 + ASCII 标点 + 空白。保留汉字/字母/数字 —— 这些是"字"，
# 改了即视为偷改字。空格/换行不算字（加标点段可能引入，属可接受）。
_PUNCT_RE = re.compile(
    "[\\s，。？！、；：“”‘’（）《》【】……——，.?!;:\"'()\\[\\]…—\\-]"
)


class _ChatLike(Protocol):
    """最小 client 契约：有 async chat(ChatRequest) -> ChatResponse。"""
    async def chat(self, request: ChatRequest) -> Any: ...


def _strip_punct(s: str) -> str:
    """移除所有标点与空白，保留汉字/字母/数字 —— 用于防改字校验。"""
    return _PUNCT_RE.sub("", s)


def _build_prompt(text: str) -> str:
    """多语言中立的标点恢复 prompt —— 不夹"翻译"等语言指令。"""
    return (
        "下面是一段语音识别转写文本，所有标点符号都已丢失。"
        "请只补充标点符号（逗号、句号、问号、顿号等）使其通顺可读。"
        "严格要求：不增删任何字符、不改写替换任何字词、"
        "只输出加标点后的文本本身，不要任何前后缀说明。\n\n"
        f"{text}"
    )


async def punctuate_text(
    text: str,
    client: _ChatLike,
    model: str,
    *,
    max_tokens: int = 8192,
    temperature: float = 0.1,
) -> tuple[str, bool]:
    """对单段文本加标点。

    Args:
        text: ASR 裸文本。
        client: 注入的 LLM 客户端（OpenAICompatibleClient 或 mock）。
        model: 模型名（透传给 ChatRequest.model）。
        max_tokens: 最大生成 token。reasoning 模型思考吃 token，需给足（实测 8192 够）。
        temperature: 采样温度，0.1 压低改写概率。

    Returns:
        (加标点后文本, ok)。ok=True 表示通过防改字校验；ok=False 表示空文本/
        异常/改字回退 —— 此时返回值等于原 text。
    """
    if not text:
        return text, False
    request = ChatRequest(
        messages=[{"role": "user", "content": _build_prompt(text)}],
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    try:
        resp = await client.chat(request)
        content = (resp.content or "").strip()
    except Exception:
        # 加标点不应阻塞 ASR：异常回退裸原文
        return text, False
    if not content:
        return text, False
    # 防改字：去标点后字符序必须与原文一致，否则 LLM 偷改了字 → 回退
    if _strip_punct(content) == _strip_punct(text):
        return content, True
    return text, False


async def punctuate_result(
    result: ASRResult,
    client: _ChatLike,
    model: str,
    *,
    max_tokens: int = 8192,
    temperature: float = 0.1,
) -> ASRResult:
    """对 ASRResult.full_text 加标点，chunks 原样不动。

    返回新的 ASRResult（不 mutate 入参）。

    失败/空文本/改字时 full_text 保留裸原文，其余字段（language/model_name/
    duration_sec/chunks）原样透传。
    """
    new_full_text, _ok = await punctuate_text(
        result.full_text, client, model,
        max_tokens=max_tokens, temperature=temperature,
    )
    return replace(result, full_text=new_full_text)
