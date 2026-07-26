# GW Task 5: RoutingLLMService 路由编排 -- Report

**Status**: DONE
**Date**: 2026-07-26

## 实现摘要

实现了 `RoutingLLMService` 路由编排网关，连接 provider、熔断器和计费模块。

### 新增文件

| 文件 | 说明 |
|---|---|
| `src/videomind/core/model_gateway/router.py` | RoutingLLMService + AllProvidersFailedError |
| `tests/core/model_gateway/test_router.py` | 9 个单元测试 |

### 实现接口

```python
class AllProvidersFailedError(Exception)
    # 所有候选 Provider 全部失败

class RoutingLLMService:
    __init__(provider, model_id, health, accounting)
    async chat(request, db) -> ChatResponse
    async stream_chat(request, db) -> AsyncIterator[ChatChunk]
    async close()
```

### 测试覆盖（9 个）

| 测试 | 覆盖场景 |
|---|---|
| `test_chat_success_passthrough` | 正常通过：response 直传 + 熔断标记成功 + 计费 |
| `test_chat_sets_provider_on_response` | response.provider 设为 model_id |
| `test_chat_circuit_open_raises` | 熔断 OPEN → AllProvidersFailedError |
| `test_chat_provider_error_marks_failure_and_records` | Provider 抛异常 → mark_failure + 错误计费 |
| `test_chat_error_accounting_failure_is_masked` | 计费失败不影响原始异常传播 |
| `test_stream_chat_success_health_checked` | 流式调用先检查熔断，成功后标记 |
| `test_stream_chat_circuit_open_raises` | 流式熔断拒绝 |
| `test_stream_chat_error_marks_failure` | 流式传输中断 → mark_failure |
| `test_close_delegates_to_provider` | close() 代理到 provider.close() |

### 回归结果

全部 29 个 model_gateway 测试 PASS（含 circuit、client、accounting、types、router）。