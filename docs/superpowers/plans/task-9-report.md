# Task 9 Report: RetrievalPipeline 顶层入口

**状态**: DONE
**日期**: 2026-07-25

---

## 目标

实现 `src/videomind/core/rag/pipeline.py` —— RAG 检索管线顶层入口，编排完整检索链路。

## 文件变更

| 文件 | 操作 |
|------|------|
| `src/videomind/core/rag/pipeline.py` | 新建 |
| `tests/core/rag/test_pipeline.py` | 新建 |

## 实现内容

### `pipeline.py` — `search()` 协程入口

完整检索管线流程：

1. **查询 Chunks** — `select(Chunk).where(Chunk.media_id == media_id)` 从 DB 拉取该视频所有 chunk
2. **HybridRetriever** — 用 chunks 构建 BM25 索引，并行调用向量+关键词双通道检索
3. **ContextExpander** — 对检索命中做 chunk_index 邻居扩展
4. **DeterministicReranker** — 三阶段线性加权确定性重排序
5. **Evidence ID** — 为每个重排命中分配 `EID_{hex8}_{idx+1:02d}` 锚点
6. **record_rag_trace** — 将完整链路写入 DB（失败不阻断检索，try/except 兜底）

返回值结构：
```python
{
    "context": [str, ...],          # 有序上下文原文列表
    "evidence": [Evidence, ...],    # 带 EID 的证据锚点列表
    "raw_hits": [VectorHit, ...],   # 混合检索原始命中
}
```

### `Evidence` dataclass

```python
@dataclass
class Evidence:
    id: str           # EID_xxx_xx
    chunk_id: str     # UUID 字符串
    content: str      # chunk 文本
    score: float      # 最终重排得分
    start_ms: int | None
    end_ms: int | None
    source_type: str  # asr/ocr/mixed
```

## 测试结果

### pipeline 模块测试（3 个测试，全部 PASS）

| 测试 | 说明 |
|------|------|
| `test_search_returns_context_and_evidence` | mock 2 hits 返回，验证 context + evidence 列表格式正确，evidence.id 以 EID_ 开头 |
| `test_search_empty_result` | HybridRetriever 返回空，验证 {"context": [], "evidence": [], "raw_hits": []}，trace 仍被调用 |
| `test_search_delegates_to_pipeline_order` | 验证 expander → reranker → evidence_id → trace 调用链顺序 |

### 全部 RAG 回归：53/53 PASS

```
tests/core/rag/test_bm25.py      — 8 passed
tests/core/rag/test_context.py   — 5 passed
tests/core/rag/test_evidence.py  — 12 passed
tests/core/rag/test_pipeline.py  — 3 passed  (new)
tests/core/rag/test_rerank.py    — 8 passed
tests/core/rag/test_retriever.py — 4 passed
tests/core/rag/test_rrf.py       — 4 passed
tests/core/rag/test_trace.py     — 3 passed
tests/core/rag/test_vector.py    — 5 passed
```

## 测试策略说明

全部依赖通过 `@patch` 在 pipeline 模块导入层 mock：

- `HybridRetriever` — mock 构造函数和 `.search()`，透传预设的 VectorHit 列表
- `ContextExpander` — mock `.expand()` 透传
- `DeterministicReranker` — mock `.rerank()` 可交换顺序
- `record_rag_trace` — 使用 `AsyncMock` 验证调用
- DB 查询链 — `mock_db.execute` 配置 `.scalars().all()` 返回空列表

每个测试验证逻辑层面的编排正确性，不依赖真实 DB/Qdrant/embedding。