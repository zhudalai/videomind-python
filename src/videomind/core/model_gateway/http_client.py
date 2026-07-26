"""OpenAI / DeepSeek 兼容 HTTP 客户端。

实现了 LLMProvider Protocol，使用 httpx 异步通信。
"""

from __future__ import annotations

import json
import time
from typing import AsyncIterator

import httpx

from videomind.core.model_gateway.types import (
    ChatChunk,
    ChatRequest,
    ChatResponse,
    TokenUsage,
)


class OpenAICompatibleClient:
    """OpenAI / DeepSeek 兼容 HTTP 客户端。

    API spec: POST {base_url}/chat/completions
    body: {model, messages, temperature, max_tokens, stream}

    Attributes:
        _base_url: 去除尾部斜杠的 API 基地址。
        _client: 共享的 httpx.AsyncClient 实例。
        _default_model: 默认模型名。
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        default_model: str = "",
        timeout: float = 60.0,
    ):
        """
        Args:
            base_url: LLM API 基地址（如 https://opencode.ai/zen/v1）。
            api_key: API 密钥。
            default_model: 未指定模型时的默认模型。
            timeout: 请求超时秒数。
        """
        self._base_url = base_url.rstrip("/")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._client = httpx.AsyncClient(headers=headers, timeout=timeout)
        self._default_model = default_model

    # ----------------------------------------------------------------
    # chat
    # ----------------------------------------------------------------

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """一次性 Chat Completions 调用，返回完整 ChatResponse。

        Args:
            request: Chat 请求参数。

        Returns:
            ChatResponse 含内容、模型、用量和延迟。

        Raises:
            httpx.HTTPStatusError: 当 HTTP 响应码非 2xx 时。
        """
        body = {
            "model": request.model or self._default_model,
            "messages": request.messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": False,
        }
        if request.response_format:
            body["response_format"] = request.response_format

        start = time.time()
        r = await self._client.post(
            f"{self._base_url}/chat/completions", json=body
        )
        r.raise_for_status()
        data = r.json()

        choice = data["choices"][0]
        usage = TokenUsage(
            prompt_tokens=data.get("usage", {}).get("prompt_tokens", 0),
            completion_tokens=data.get("usage", {}).get("completion_tokens", 0),
            total_tokens=data.get("usage", {}).get("total_tokens", 0),
        )

        return ChatResponse(
            content=choice["message"]["content"],
            model=data.get("model", body["model"]),
            usage=usage,
            finish_reason=choice.get("finish_reason", "stop"),
            latency_ms=int((time.time() - start) * 1000),
            provider=self._base_url,
        )

    # ----------------------------------------------------------------
    # stream_chat
    # ----------------------------------------------------------------

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        """流式 Chat Completions，按 SSE 行 yield ChatChunk。

        Args:
            request: Chat 请求参数。

        Yields:
            ChatChunk: 每个 delta 增量文本 + 元数据。
        """
        body = {
            "model": request.model or self._default_model,
            "messages": request.messages,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "stream": True,
        }

        r = await self._client.post(
            f"{self._base_url}/chat/completions", json=body
        )
        r.raise_for_status()

        async for line in r.aiter_lines():
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()  # strip "data: " prefix
            if payload == "[DONE]":
                break
            try:
                obj = json.loads(payload)
                choice = obj["choices"][0]
                delta = choice.get("delta", {})
                yield ChatChunk(
                    delta_content=delta.get("content", ""),
                    delta=delta,
                    finish_reason=choice.get("finish_reason"),
                    model=obj.get("model", ""),
                )
            except (json.JSONDecodeError, KeyError, IndexError):
                continue

    # ----------------------------------------------------------------
    # estimate_cost
    # ----------------------------------------------------------------

    def estimate_cost(self, usage: TokenUsage) -> float:
        """估算 Token 费用（美元）。

        默认为 0.0 —— 实际费用依赖供应商定价。

        Args:
            usage: Token 用量统计。

        Returns:
            float: 费用美元值。
        """
        return 0.0

    # ----------------------------------------------------------------
    # close
    # ----------------------------------------------------------------

    async def close(self):
        """关闭内部 httpx 客户端，释放连接。"""
        await self._client.aclose()