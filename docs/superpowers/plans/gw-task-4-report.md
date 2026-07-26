# gw-task-4: TokenAccounting Token 计费入库 (TDD)

**状态**: DONE
**日期**: 2026-07-25
**策略**: TDD（先 test_accounting.py -> FAIL -> accounting.py -> PASS）

## 创建的文件

| 文件 | 路径 |
|------|------|
| 测试 | `tests/core/model_gateway/test_accounting.py` |
| 实现 | `src/videomind/core/model_gateway/accounting.py` |

## 测试覆盖

总 5 个用例，全部 PASS。

| 用例 | 场景 | 关键断言 |
|------|------|----------|
| `test_record_success` | 成功调用 | DB add/flush 各调用一次；AiCallLog 字段（provider/model/task_type/token/cost/trace_id）正确映射；Prometheus 三个指标 (LLM_CALL_TOTAL/LLM_CALL_LATENCY/LLM_TOKEN_USAGE) 正确上报 |
| `test_record_error` | 错误状态 | status="timeout" + error_code="GATEWAY_TIMEOUT" 写入 |
| `test_record_no_colon_modeled` | model_id 不带冒号 | provider=model=model_id（都取 "ollama"） |
| `test_record_db_failure_is_idle` | DB 写入异常 | flush 抛 Exception，不泄露到调用方；Prometheus 指标仍正常上报 |
| `test_record_default_task_type` | task_type=None | 默认 fallback 为 "chat" |

## 实现要点

- **model_id 解析**：`"provider:model"` 用 `partition(":")` 拆分为 provider 和 model，无冒号则两者相同。
- **DB 写入隔离**：try/except 包裹 `db.add(log)` + `await db.flush()`，异常仅记录 structlog.exception，不阻断调用方。
- **Prometheus 指标**：DB 失败后仍正常上报（三条 `Counter.inc()` + 一条 `observe()`）。
- **task_type 默认值**：`request.task_type or "chat"`，None 时 fallback。

## 文件清单

```
tests/core/model_gateway/test_accounting.py  -- 5 test cases
src/videomind/core/model_gateway/accounting.py -- TokenAccounting 类实现
```

## 测试结果

```
tests/core/model_gateway/test_accounting.py - 5/5 PASSED
tests/core/model_gateway/ (full suite): 20/20 PASSED (no regressions)
```