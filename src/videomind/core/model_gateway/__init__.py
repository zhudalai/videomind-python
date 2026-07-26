"""Model Gateway —— LLM 供应商抽象层。

核心组件：
- types: ChatRequest / ChatResponse / ChatChunk / TokenUsage / LLMProvider Protocol
- 后续 step 将实现 concrete provider + gateway 路由。
"""

from __future__ import annotations

from videomind.core.model_gateway.types import (
    ChatChunk,
    ChatRequest,
    ChatResponse,
    LLMProvider,
    TaskType,
    TokenUsage,
)

__all__ = [
    "ChatChunk",
    "ChatRequest",
    "ChatResponse",
    "LLMProvider",
    "TaskType",
    "TokenUsage",
]