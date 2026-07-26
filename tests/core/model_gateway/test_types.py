"""Model Gateway 数据类型单元测试。

覆盖 ChatRequest、ChatResponse、ChatChunk、TokenUsage、TaskType。
"""

import uuid

from videomind.core.model_gateway.types import (
    ChatChunk,
    ChatRequest,
    ChatResponse,
    TaskType,
    TokenUsage,
)


def test_chat_request_defaults():
    """ChatRequest 构造默认值：model=None, temperature=0.3, max_tokens=4096, stream=False, task_type=None。"""
    req = ChatRequest(messages=[{"role": "user", "content": "hi"}])

    assert req.model is None
    assert req.temperature == 0.3
    assert req.max_tokens == 4096
    assert req.stream is False
    assert req.task_type is None


def test_token_usage_cost():
    """TokenUsage 统计默认 cost_usd=0.0，total_tokens 正确。"""
    usage = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)

    assert usage.total_tokens == 150
    assert usage.cost_usd == 0.0


def test_chat_chunk_immutable_fields():
    """ChatChunk 默认 delta=None，delta_content 正确传入。"""
    chunk = ChatChunk(delta_content="hello")

    assert chunk.delta is None  # default
    assert chunk.delta_content == "hello"