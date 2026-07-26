# Task 5: HybridRetriever 编排 -- 完成报告

**状态**: DONE
**日期**: 2026-07-25

---

## 实现概要

实现了 `HybridRetriever` 混合检索编排器，并行调用 Vector + BM25 双通道检索，通过 RRF 算法融合排序。

### 新增文件

| 文件 | 说明 |
|---|---|
| `src/videomind/core/rag/retriever.py` | HybridRetriever 编排器（72 行） |
| `tests/core/rag/test_retriever.py` | 4 个单元测试（mock 外部依赖） |

### HybridRetriever 设计

- 初始化时接收 `media_id` + `chunks` 列表，通过 `InMemoryBM25.build(chunks)` 构建关键词索引
- 建立 `chunk_id → content` 查找表，用于只出现在 BM25 通道中、向量通道缺失的命中回填
- `search()` 方法通过 `asyncio.gather` 并行调用 `VectorRetriever.retrieve` + `asyncio.to_thread(InMemoryBM25.search)`
- 两通道排名通过 `rrf_fuse()` 融合后，用 RRF score 覆盖 VectorHit.score

### 关键设计决策

1. **chunk_id 类型统一**: VectorHit.chunk_id 是 `str`，BM25 的 ID 是 `uuid.UUID`。RRF 融合以 `uuid.UUID` 为键，最终装箱回字符串。
2. **BM25 同步转异步**: `InMemoryBM25.search` 是同步方法，通过 `asyncio.to_thread` 跑在线程池中。
3. **BM25-only 命中回填**: 对只在 BM25 出现、不在向量通道的 chunk，从初始化时构建的 `self._chunk_content` 查找 content 属性创建 VectorHit。

### 测试覆盖（4 个纯 mock 测试）

| 测试 | 场景 |
|---|---|
| `test_hybrid_returns_merged_results` | 两通道各 2 个结果 → 合并后含 4 条不同 hit |
| `test_hybrid_empty_channels` | 两通道均空 → 返回 `[]` |
| `test_hybrid_rrf_ordering` | 构造非对称 RRF 得分 → 验证严格降序排列 |
| `test_bm25_only_hit_gets_content_from_chunks` | BM25 独有 chunk → content 从 chunks 回填 |

### 回归测试

全部 34 个 RAG 测试通过，无回归。

### 执行记录

```
RED:    4 failed (ImportError: no module 'retriever')
GREEN:  4 passed
REGRESSION: 34 passed (含历史 30 个测试)
```