# Task 8 报告：RagTrace 检索链路追踪 DB 写入

## 状态：DONE

## 变更摘要

| 文件 | 操作 | 说明 |
|---|---|---|
| `src/videomind/core/rag/trace.py` | 新增 | `record_rag_trace()` 协程函数 |
| `tests/core/rag/test_trace.py` | 新增 | 3 个 TDD 测试用例 |

## 实现内容

### `record_rag_trace(db, *, query, ...) -> RagTrace`

- **输入**: `AsyncSession` + keyword-only 参数（query 必填，其余 11 个可选）
- **操作**: 创建 `RagTrace` ORM 实例，调用 `db.add()` + `await db.flush()`
- **返回**: `RagTrace` 实例（flush 后 DB 填充 `id`/`created_at`）
- **所有 JSONB 字段**：`rewritten_queries`, `intent_path`, `retrieval_channels`, `fused_results`, `expanded_results`, `reranked_results`, `final_context`, `token_usage`

### 测试用例（3 tests）

| 测试 | 覆盖场景 |
|---|---|
| `test_record_rag_trace_basic` | 仅传 query，其余默认 None，验证 `db.add` 和 `db.flush` 被调用 |
| `test_record_rag_trace_full` | 全部 12 个字段填充，逐字段断言传递正确性 |
| `test_record_rag_trace_returns_trace` | 返回值为 `RagTrace` 实例且与传入 `db.add` 的对象为同一引用 |

### Mock 策略

- `db = MagicMock()` — 非 async 的 `add`
- `db.flush = AsyncMock()` — 单独的 `flush` 作为异步 mock
- 验证 `db.flush.assert_awaited_once()` 确保 flush 被 await

## 测试结果

```
tests/core/rag/test_trace.py::TestRecordRagTrace::test_record_rag_trace_basic PASSED
tests/core/rag/test_trace.py::TestRecordRagTrace::test_record_rag_trace_full PASSED
tests/core/rag/test_trace.py::TestRecordRagTrace::test_record_rag_trace_returns_trace PASSED

=== 3 passed in 0.61s ===
```

## 回归结果

全部 RAG 模块 50 tests 通过，无回归：

```
tests/core/rag/test_bm25.py (8), test_context.py (5), test_evidence.py (11),
test_rerank.py (8), test_retriever.py (4), test_rrf.py (4), test_trace.py (3),
test_vector.py (5) — 50 passed in 2.09s
```