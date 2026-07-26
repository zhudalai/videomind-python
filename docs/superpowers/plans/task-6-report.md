# Task 6 报告：ContextExpander 上下文扩展

**日期**：2026-07-25
**状态**：DONE
**触发**：严格 TDD

---

## 完成内容

| 文件 | 动作 | 内容 |
|---|---|---|
| `tests/core/rag/test_context.py` | **新增** | 5 个测试用例 |
| `src/videomind/core/rag/context.py` | **新增** | ContextExpander 实现 |

---

## TDD 流程

### RED — 测试先 FAIL

```
ModuleNotFoundError: No module named 'videomind.core.rag.context'
```

模块不存在，5 个待测用例皆因 ImportError 失败。符合预期。

### GREEN — 最小实现 → 全部 PASS

```
tests/core/rag/test_context.py::test_expand_noop_when_empty_hits PASSED
tests/core/rag/test_context.py::test_expand_single_hit_with_neighbors PASSED
tests/core/rag/test_context.py::test_expand_no_left_neighbor PASSED
tests/core/rag/test_context.py::test_expand_deduplication PASSED
tests/core/rag/test_context.py::test_expand_preserves_original_order PASSED
```

### 回归：全部 RAG 测试 39/39 PASS（5 新 + 34 旧）

```
tests/core/rag/test_bm25.py     8 passed
tests/core/rag/test_context.py  5 passed
tests/core/rag/test_evidence.py 12 passed
tests/core/rag/test_retriever.py 4 passed
tests/core/rag/test_rrf.py      4 passed
tests/core/rag/test_vector.py   5 passed
```

---

## 测试用例覆盖

| 测试 | 场景 |
|---|---|
| `test_expand_noop_when_empty_hits` | 空输入 → 空输出，不调用 DB |
| `test_expand_single_hit_with_neighbors` | 中间 chunk → 前后各一个邻居，共 3 条 |
| `test_expand_no_left_neighbor` | chunk_index=0，无左侧邻居，仅扩展右侧 |
| `test_expand_deduplication` | 两个相邻命中共享邻居 → 自动去重 |
| `test_expand_preserves_original_order` | 原始命中保持在前，邻居追加在末尾（score=0.0） |

---

## 实现要点

- `VectorHit.chunk_id`（str） → `uuid.UUID()` 转换后与 `Chunk.id` 匹配
- 两次 DB 查询：首次查 hit chunk_index，第二次查 neighbor chunks
- 邻居索引公式：`chunk_index ± 1`（下限为 0）
- 自动排除已有的 chunk_index 和 chunk_id 以避免重复
- Mock 策略：`AsyncMock` + `MagicMock` 模拟 ScalarsResult 双链