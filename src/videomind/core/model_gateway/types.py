"""Model Gateway 共享数据类型与 LLMProvider 协议。

对应 Phase 2 Model Gateway 设计：
- ChatRequest / ChatResponse / ChatChunk / TokenUsage 为请求-响应模型。
- TaskType 用于 routing 键（chat / embedding / rerank）。
- LLMProvider Protocol 定义 chat / stream_chat / estimate_cost 契约。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import AsyncIterator, Literal, Protocol


# ----------------------------------------------------------------
# 熔断器状态枚举
# ----------------------------------------------------------------


class CircuitState(str, Enum):
    """熔断器三态枚举。

    CLOSED: 正常状态，允许调用。
    OPEN: 熔断打开，拒绝调用。
    HALF_OPEN: 半开探测状态，限量放行以检测服务恢复。
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

# ----------------------------------------------------------------
# 类型别名
# ----------------------------------------------------------------

TaskType = Literal["chat", "embedding", "rerank"]
"""LLM 任务类型分类，后续 step 用于 routing 键。"""

# ----------------------------------------------------------------
# 数据类型
# ----------------------------------------------------------------


@dataclass
class TokenUsage:
    """Token 用量统计。

    Attributes:
        prompt_tokens: 输入 token 数。
        completion_tokens: 输出 token 数。
        total_tokens: 总 token 数。
        cost_usd: 预估费用（美元），默认 0.0，由 LLMProvider.estimate_cost 填充。
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class ChatRequest:
    """Chat 请求。

    Attributes:
        messages: 对话消息列表 [{"role":"user","content":"..."}]。
        model: 模型名，None 时由 gateway 用默认模型。
        temperature: 采样温度（0-2）。
        max_tokens: 最大生成 token 数。
        thinking: 是否开启思考模式（deepseek/opencode extended thinking）。
        response_format: 格式约束，如 {"type": "json_object"}。
        stream: 是否流式返回。
        user_id: 可选的用户标识，用于调用日志。
        trace_id: 追踪 ID，自动生成 32 位 hex。
        timeout: 请求超时秒数。
        task_type: 任务类型（chat/embedding/rerank），用于指标分类。
        reasoning: OpenRouter reasoning 模型思维链开关（None=不传，按模型默认；
            False=禁用思维链直接吐最终答案，适合需纯 JSON 解析的调用点如 rewriter；
            True=显式启用）。序列化为 OpenRouter body 的 ``{"reasoning": {"enabled": ...}}``。
    """

    messages: list[dict]
    model: str | None = None
    temperature: float = 0.3
    max_tokens: int = 4096
    thinking: bool = False
    response_format: dict | None = None
    stream: bool = False
    user_id: str | None = None
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex[:32])
    timeout: float = 60.0
    task_type: TaskType | None = None
    reasoning: bool | None = None


@dataclass
class ChatResponse:
    """Chat 响应。

    Attributes:
        content: 生成文本内容。
        model: 实际使用的模型名。
        usage: Token 用量统计。
        finish_reason: 终止原因（stop/length/content_filter 等）。
        latency_ms: 调用延迟（毫秒）。
        provider: LLM 供应商标识。
    """

    content: str
    model: str
    usage: TokenUsage
    finish_reason: str = "stop"
    latency_ms: int = 0
    provider: str = ""


@dataclass
class ChatChunk:
    """流式 Chat 分片。

    Attributes:
        delta_content: 增量文本内容。
        delta: 原始 API delta 对象（dict），保留完整字段（tool_calls 等）。
        finish_reason: 终止原因，仅在最后一个 chunk 非空。
        model: 模型名。
    """

    delta_content: str = ""
    delta: dict | None = None
    finish_reason: str | None = None
    model: str = ""


# ----------------------------------------------------------------
# LLMProvider 契约
# ----------------------------------------------------------------


class LLMProvider(Protocol):
    """LLM 供应商协议。

    所有 LLM 后端（OpenAI-compatible、直接 API 等）实现此接口，
    由 Model Gateway 适配层统一路由调用。

    Methods:
        chat: 一次性 Chat Completions 调用，返回完整响应。
        stream_chat: 流式 Chat Completions，返回 AsyncIterator[ChatChunk]。
        estimate_cost: 根据 TokenUsage 估算费用（美元），不产生实际调用。
    """

    async def chat(self, request: ChatRequest) -> ChatResponse: ...

    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatChunk]: ...

    def estimate_cost(self, usage: TokenUsage) -> float: ...


__all__ = [
    "ChatChunk",
    "ChatRequest",
    "ChatResponse",
    "CircuitState",
    "LLMProvider",
    "TaskType",
    "TokenUsage",
]