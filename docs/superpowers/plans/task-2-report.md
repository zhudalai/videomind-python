# Task 2 Report: InMemoryBM25 关键词检索

## 创建/修改的文件

| 文件 | 操作 |
|---|---|
| `tests/core/rag/test_bm25.py` | 新建 — 8 个测试用例 |
| `src/videomind/core/rag/bm25.py` | 新建 — InMemoryBM25 实现 |

## 测试输出

```
$ python -m pytest tests/core/rag/test_bm25.py -v

tests/core/rag/test_bm25.py::TestInMemoryBM25Basic::test_build_and_search PASSED
tests/core/rag/test_bm25.py::TestInMemoryBM25Basic::test_empty_index_search_returns_empty PASSED
tests/core/rag/test_bm25.py::TestInMemoryBM25Basic::test_build_empty_list PASSED
tests/core/rag/test_bm25.py::TestInMemoryBM25Basic::test_rebuild_replaces_old_corpus PASSED
tests/core/rag/test_bm25.py::TestInMemoryBM25Basic::test_top_k_limits_results PASSED
tests/core/rag/test_bm25.py::TestInMemoryBM25Basic::test_no_matching_terms PASSED
tests/core/rag/test_bm25.py::TestInMemoryBM25Basic::test_single_chunk PASSED
tests/core/rag/test_bm25.py::TestRegistry::test_isolated_instances PASSED

============================== 8 passed in 0.19s ==============================
```

## Concern

- BM25 在单文档语料场景下 score 可能为负（IDF 为负导致），这是 `rank-bm25` 的预期行为，不影响排序正确性。测试中已调整为仅验证返回的 chunk_id 和 float 类型，不强制 `score > 0`。