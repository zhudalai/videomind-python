# GW Task 1: Model Gateway 数据类型 + LLMProvider 协议

**状态**: DONE  
**日期**: 2026-07-25  
**文件**:
- `src/videomind/core/model_gateway/types.py`（新增）
- `src/videomind/core/model_gateway/__init__.py`（更新，导出类型）
- `tests/core/model_gateway/test_types.py`（新增）

## 测试结果

```
tests/core/model_gateway/test_types.py::test_chat_request_defaults PASSED
tests/core/model_gateway/test_types.py::test_token_usage_cost PASSED
tests/core/model_gateway/test_types.py::test_chat_chunk_immutable_fields PASSED
3 passed in 0.09s
```

## 实现概要

| 类型 | 说明 |
|------|------|
| `TaskType` | `Literal["chat", "embedding", "rerank"]` 任务类型路由键 |
| `TokenUsage` | prompt/completion/total tokens + cost_usd（默认 0.0） |
| `ChatRequest` | 包含 messages、model、temperature(0.3)、max_tokens(4096)、stream(False)、thinking、response_format、user_id、trace_id（auto uuid4 hex 32 位）、timeout(60.0)、task_type |
| `ChatResponse` | content、model、usage(TokenUsage)、finish_reason("stop")、latency_ms、provider |
| `ChatChunk` | delta_content、delta(dict\|None)、finish_reason、model |
| `LLMProvider` | Protocol: `chat()`、`stream_chat()`、`estimate_cost()` |

## 风格一致性

- 使用 `from __future__ import annotations`（与 RAG 模块一致）
- 中文 docstring，`@dataclass` 风格（与 `VectorHit`、`Evidence` 一致）
- `__all__` 导出列表（与 `evidence.py`、`metrics.py` 一致）