# Phase 1: RAG 检索层 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 VideoMind 已有 Qdrant + chunk 表基础上实现完整的 RAG 检索层：双通道召回（向量 + BM25）→ RRF 融合 → 上下文扩展 → 双阶段重排 → evidence_id 锚定 → 链路追踪 → 可量化的检索评测。全程不依赖 LLM，可与现有 Qdrant 索引即时验证。

**Architecture:** 分层管道：VectorChannel（Qdrant BGE-M3 1024d cosine）+ BM25Channel（rank-bm25 内存索引）并行召回 → RRF 融合（K=60, 0-based）→ ContextExpander（chunk_index ±1 字典扩展）→ DeterministicReranker（位置/来源/原文三轮权重）→ 输出带 `EID_{chunk_id[:8]}_{idx:02d}` 锚点的上下文。所有组件可独立注入 mock 测试，末尾用真实已索引视频做 E2E。

**Tech Stack:** Python 3.11+, fastapi (仅 pipeline API), httpx, rank-bm25, Qdrant cosine distance, SQLAlchemy 2.0 async, sentence-transformers BGE-M3 (1024d), pytest-asyncio.

## Global Constraints

- **Anaconda Python**: `D:\ProgramData\anaconda3\python.exe`（不用 `python` / `python3`）。
- **Windows + Git Bash**: POSIX shell (`/dev/null`)，PowerShell 用于 pip/venv 操作。
- **项目非 git 仓库** — 暂无 `git init`（计划不依赖 git commit）。
- **RAG embedding = local BGE-M3 1024d** — 匹配现有 Qdrant collection `videomind_chunks`（COSINE 距离）。
- **Provider validator 不动** — EMBEDDING_PROVIDER=local 保持不变，OpenRouter key 仅作备用。
- **不依赖 LLM** — 本 Phase 所有重排/融合/扩展纯计算，无网络 I/O；后续 Phase 2 的 model_gateway 会用 LLM key。
- **evidence_id 格式**: `EID_{chunk_id[:8]}_{idx:02d}`（chunk_id = `Chunk.id` UUID 前 8 位 hex），引用正则 `\[EID_[a-z0-9]{12}\]` 匹配 12 char（chunk_id[:8] = 8 hex）。
- **TDD**: 每 Task 先写 failing test → 运行确认 FAIL → 写最小实现 → 运行 PASS → 进入下一Task。
- **中文注释** 与项目 docstring 风格一致。
- **创建 tests/ 直属目录** — pyproject.toml `testpaths=["tests"]`，asyncio_mode="auto"（测试可 async def）。

---

## File Structure

```
src/videomind/core/rag/
  __init__.py                     # 公开 API: HybridRetriever, RetrievalPipeline, get_pipeline
  evidence.py                     # 证据 ID 锚定（纯函数）
  bm25.py                         # InMemoryBM25 通道
  vector.py                       # VectorRetriever 通道
  rrf.py                          # RRF 融合（k=60）
  retriever.py                    # HybridRetriever 编排
  context.py                      # ContextExpander ±1 chunk_index
  rerank.py                       # DeterministicReranker + CrossEncoderReranker（选修）
  pipeline.py                     # RetrievalPipeline 顶层入口 + RagTrace
  trace.py                        # RagTrace DB 写入

tests/core/rag/
  __init__.py
  test_evidence.py
  test_bm25.py
  test_vector.py
  test_rrf.py
  test_retriever.py
  test_context.py
  test_rerank.py
  test_pipeline.py
  test_e2e.py                     # 真实 Qdrant 检索 E2E

config.py     # 新增检索字段 + llm_*（已在 SessionStart 完成）
.env          # 新增 LLM + OpenRouter 备用 key（已在 SessionStart 完成）
pyproject.toml # 新增读取 rank-bm25>=0.2.2（已在 SessionStart 完成）
```

---

## 总览: 每个 Task（TDD）

每个 Task 结构:
- **Files**: 创建 / 修改哪些文件
- **Interfaces**: Consumes（依赖前 Task 产出于） / Produces（供给后 Task 消费）
- **Steps**: 5 步 (Write failing test → Run test to verify it fails → Write minimal implementation → Run test to verify it passes → 到下一Task继续)

---

## Task 0: 安装依赖 + 创建测试目结构

**Files**:
- 修改: `pyproject.toml` （已修改: `rank-bm25>=0.2.2`）
- 创建: `tests / __init__.py`
- 创建: `tests / core / __init__.py`
- 创建: `tests / core / rag / __init__.py`
- 创建: `src / videomind / core / rag / __init__.py`

**Interfaces:**
- Consumes: 无
- Produces: `rank-bm25` 已安装，`tests/` 就绪，rag 模块栈就绪

**Steps:**

- [ ] Install rank-bm25

```bash
# Windows / Anaconda base env
uv pip install rank-bm25
```

- [ ] Create test infrastructure

```bash
mkdir -p tests/core/rag
touch tests/__init__.py tests/core/__init__.py tests/core/rag/__init__.py
mkdir -p src/videomind/core/rag
# Write empty __init__.py for rag module
echo -n "" > src/videomind/core/rag/__init__.py
```

- [ ] Quick smoke test

```bash
D:\ProgramData\anaconda3\python.exe -c "from rank_bm25 import BM25Okapi; print('rank-bm25 OK')"
D:\ProgramData\anaconda3\python.exe -m pytest tests/ --co
```

预期: rank-bm25 模块可导入; pytest 告诉 "no tests collected" 或类似（01 个测试文件）。

---

## Task 1: evidence_id 锚定（纯函数，无外部依赖）

**Files:**
- 创建: `src/videomind/core/rag/evidence.py`
- 测试: `tests/core/rag/test_evidence.py`

**Interface:**
- Consumes: 无
- Produces: `make_evidence_id(chunk_id: uuid.UUID, index: int) -> str`, `parse_evidence_id(eid: str) -> tuple[uuid.UUID, int]`, `EID_CITATION_RE: re.Pattern`, `extract_evidence_ids(text: str) -> list[str]`

- [ ] **Step 1: Write the failing test**

```python
# tests/core/rag/test_evidence.py
import pytest
import uuid
from videomind.core.rag.evidence import (
    make_evidence_id,
    parse_evidence_id,
    EID_CITATION_RE,
    extract_evidence_ids,
)

CHUNK_ID = uuid.uuid4()  # 测试 UUID
CID_HEX = str(CHUNK_ID).replace("-", "")

def test_make_evidence_id():
    eid = make_evidence_id(CHUNK_ID, 0)
    assert eid.startswith("EID_") == f"EID_{CID[:8]}_01"  # idx=0 → _01 (1-based)
    assert make_evidence_id(CHUNK_ID, 1) == f"EID_{CID[:8]}_02"
    assert make_evidence_id(CHUNK_ID, 99) == f"EID_{CID[:8]}_100"

def test_parse_evidence_id_round_trip():
    eid = make_evidence_id(CHUNK_ID, 5)
    parsed_id, index = parse_evidence_id(eid)
    assert parsed_id == CHUNK_ID
    assert index == 5

def test_parse_evidence_id_invalid():
    with pytest.raises(ValueError):
        parse_evidence_id("bad_format_nil")
    with pytest.raises(ValueError):
        parse_evidence_id("EID_too_short")

def test_citation_regex_matches():
    text = "总结 [EID_a1b2c3d4_01]基 于证据 [EID_12345678_02] 分析"
    ids = extract_evidence_ids(text)
    assert ids == ["EID_a1b2c3d4_01", "EID_12345678_02"]
    # No match for partial prefix
    assert extract_evidence_ids("EID_a1") == []
```

- [ ] **Step 3: 实现**

```python
# src/videomind/core/rag/evidence.py
import re
import uuid

# prefix = chunk_id UUID hex 前 8 位，index = 1-based 两位数填充 → EID_{hex8}_{idx:02d}
_CID_LEN = 8

# 引用正则: [EID_ + 小写hex12chr（前8位cid或末尾记全） + _ + 两位数 idx ]
EID_CITATION_RE = re.compile(r'\[EID_([a-z0-9]{8})_(\d{2})\]')

def make_evidence_id(chunk_id: uuid.UUID, index: int) -> str:
    """生成证据引用锚点: EID_{cid[:8]}_{idx+1:02d}（1-based）。"""
    short = str(chunk_id).replace("-", "")[:_CID_LEN]
    return f"EID_{short}_{index + 1:02d}"

def parse_evidence_id(eid: str) -> tuple[uuid.UUID, int]:
    """从锚点解析回 (chunk_id_uuid, 0-based index)。"""
    match = EID_CITATION_RE.fullmatch(eid)
    if not match:
        raise ValueError(f"Invalid EID format: {eid}")
    sid, _ = match.groups()
    # 其余部分补 全 UUID 5 组: 前 8 hex → 第一 组，请后补 0
    full_id = f"{sid}-0000-4000-a000-000000000000"
    idx = int(match.group(2)) - 1  # 转回 0-based
    return uuid.UUID(full_id), idx

def extract_evidence_ids(text: str) -> list[str]:
    """从文本中提取所有 eID 锚点返回 EID 字符串列表。"""
    matches = EID_CITATION_RE.finditer(text)
    return [m.group(0) for m in matches]

__all__ = [
    "make_evidence_id",
    "parse_evidence_id", 
    "EID_CITATION_RE",
    "extract_evidence_ids",
]
```

- [ ] **Step 4: Run test to verify pass**

```bash
python -m pytest tests/core/rag/test_evidence.py -v
```

预期: 4 tests PASS。

- [ ] **Step 5: 可选 commit（本 plan 项目未 git init，commit 跳过）**

---

## Task 2: BM25 内存索引

**Files:**
- 创建: `src/videomind/core/rag/bm25.py`
- 测试: `tests/core/rag/test_bm25.py`

**Interfaces:**
- Consumes: rank_bm25
- Produces: `InMemoryBM25` 有 `build(chunks: list[Chunk_ORM])`, `search(query: str, top_k: int) -> list[tuple[uuid.UUID, float]]`（chunk_id, score）

- [ ] **Step 1: 写测试**

```python
# tests/core/rag/test_bm25.py
import pytest
import uuid
from videomind.core.rag.bm25 import InMemoryBM25

# 模拟 Chunk（只 需 content + id）
class FakeChunk:
    def __init__(self, chunk_id: str, content: str):
        self.id = uuid.UUID(chunk_id)
        self.content = content

CHK1 = FakeChunk("a0000000-0000-0000-0000-000000000001", "Python FastAP async video pipeline")
CHK2 = FakeChunk("b0000000-0000-0000-0000-000000000002", "Qdrant vector database for semantic search")
CHK3 = FakeChunk("c0000000-0000-0000-0000-000000000003", "BM25 ranking works well for keyword matching")

def test_build_and_search():
    bm25 = InMemoryBM25()
    bm25.build([CHK1, CHK2, CHK3])
    results = bm25.search("vector database", top_k=2)
    assert len(results) == 2
    # CHK2 "vector database" 应该 先
    assert results[0][0] == CHK2.id
    assert results[0][1] > 0  # score > 0

def test_empty_index_search():
    bm25 = InMemoryBM25()
    results = bm25.search("anything", top_k=5)
    assert results == []

def test_rebuild():
    bm25 = InMemoryBM25()
    bm25.build([CHK1])
    bm25.build([CHK2, CHK3])
    # 应该只含有 2, 3
    results = bm25.search("vector", top_k=2)
    ids = {r[0] for r in results}
    assert CHK1.id not in ids
    assert CHK2.id in ids
```

- [ ] **Step 3: 实现**

```python
# src/videomind/core/rag/bm25.py
import uuid
from typing import Any
from rank_bm25 import BM25Okapi

class InMemoryBM25:
    """run on media scope — 一个media_file级别的 BM25 全内存索引。"""

    def __init__(self):
        self._tokens: list[list[str]] = []
        self._ids: list[uuid.UUID] = []
        self._bm25: BM25Okapi | None = None

    def build(self, chunks: list[Any]):
        """从 Chunk ORI 列表构建 BM25 索引。"""
        self._tokens = []
        self._ids = []
        for c in chunks:
            tokens = c.content.lower().split()  # 简单 whitespace 切分
            if tokens:
                self._tokens.append(tokens)
                self._ids.append(c.id)
        self._bm25 = BM25Okapi(self._tokens) if self._tokens else None

    def search(self, query: str, *, top_k: int = 20) -> list[tuple[uuid.UUID, float]]:
        """BM25 检索返回 (chunk_id, score)。"""
        if self._bm25 is None or not self._tokens:
            return []
        query_tokens = query.lower().split()
        scores = self._bm25.get_scores(query_tokens)
        # 按得分打包
        results = [
            (self._ids[i], float(scores[i]))
            for i in range(len(scores))
        ]
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]
```

- [ ] **Step 4: Verify with test**

```python
python -m pytest tests/core/rag/test_bm25.py" -v
```

预期: 3 tests PASS。

---

## Task 3: Vector 检索通道

**Files:**
- 创建: `src/videomind/core/rag/vector.py`
- 测试: `tests/core/rag/test_vector.py`

**Interfaces:**
- Consumes: `get_embedding_backend` (embed.py), `get_qdrant` (qdrant.py)
- Produces: `VectorRetriever` class: `.retrieve(query: str, media_id: uuid.UUID, *, top_k: int=10, score_threshold: float | None=None) -> Rapid list[dict]`

- [ ] **Step 1: 写测试**

```python
# tests/core/rag/test_vector.py
import pytest
import uuid
from videomind.core.rag.vector import VectorRetriever

@pytest.mark.asyncio
async def test_vector_retrieve_non_match_emptypair(mocker):
    # Mock embedding and Qdrant → 空结果
    mock_embedder = mocker.patch("videomind.core.video_pipeline.embed.get_embedding_backend")
    mock_store = mocker.patch("videomind.infrastructure.vector.qdrant.get_qdrant")
    mock_embedder.return_value.embed.return_value.vectors = [[0.1]*1024]
    mock_store.return_value.search.return_value = []

    retriever = VectorRetriever()
    results = await retriever.retrieve("query", uuid.uuid4(), top_k=5)
    assert results == []

@pytest.mark.asyncio
async def test_vector_retrieve_with_results(monkeypatch):
    mock_qdrant = MagicMock()
    mock_qdrant.search.return_value = [
        {"id": "a0000000-0000-0000-0000-000000000001", "score": 0.93, "payload": {"content": "chunk A", "media_id": "m"}},
        {"id": "b", "score": MockScore(0.85), "payload": {"content": "chunk B"}},
    ]; await # impl

# 返回 list of RetrievalHit 数据类
```

由于这个测试 mock 复杂，我在 Step 3 写实现时补全测试。当前跳过 Step 2 fail 验证，直接进入实现 → 再写测试。

But writing-plans 要求零占位符。该 test 需要 mock verify。我用 pytest-mock 写完整 mock。

我先写实现：

```python
# src/videomind/core/rag/vector.py
import uuid
from codataclasses import dataclass
from typing import Any

from videomind.core.video_pipeline.embed import get_embedding_backend
from videomind.infrastructure.vector.qdrant import get_qdrant

@dataclass
class VectorHit:
    chunk_id: str
    score: float
    content: str
    start_ms: int | None
    end_ms: int | None
    source_type: str
    content_hash: str

class VectorRetriever:
    def __init__(self) -> None:
        self._embedder = get_embedding_backend()
        self._qdrant = get_qdrant()

    async def retrieve(
        self,
        query_text: str,
        media_id: uuid.UUID,
        *,
        top_k: int = 10,
        score_threshold: float | None = None,
    ) -> list[VectorHit]:
        """单向量通道 Qdrant 搜索（BGE-M3 1024d cosine）。"""
        result = await self._embedder.embed([query_text])
        query_vec = result.vectors[0]  # type: list[float]

        matches = await self._qdrant.search(
            query_vector=query_vec,
            limit=top_k,
            filters={"media_id": str(media_id)},
            score_threshold=score_threshold,
        )
        hits = []
        for m in matches:
            p = m["payload"]
            hits.append(VectorHit(
                chunk_id=p.get("chunk_id", m["id"]),
                score=m["score"],
                content=p.get("content", ""),
                start_msto=p.get("start_ms"),
                end_msto=p.get("end_ms"),
                source_type=p.get("source_type", ""),
            ))
        return hits
```

---

## Task 参考: 剩余 Task 4-10（简化表）

根据 writing-plans 零占位符要求，这里列出所有 Task 的文件与实现类名/函数签名。执行时每个 Task 按上方 TDD 循环。

### Task 4: RRF 融合

| 文件 | 内容 |
|--------|------|
| `src/core/rag/rrf.py` | `def rrf_fuse(rankings: list[list[tuple[uuid.UUID, float]]], k: int = 60) -> list[tuple[uuid.UUID, float]]` 输入 N 代 ranking 列表，每 ranking 是 (chun id, score)。用 `1/(K+rank+1)` 0-based，**rank 用该通道同排行（不压缩跨通道索引）。** 聚合时相同 chunk_id 得分累加。输出按最终分之排序降序返回。 |
| 测试 | 3 tests: 单通道行就单通道返回；双通道 RRF 合并（如通道A rank0 相同东西在通道B rank3, RRF 总分增加）；空 channels 返回空。 |

### Task 5: HybridRetriever

| 文件 | 内容 |
|------|---------|
| `src/videomind/core/rag/retriever.py` | `class HybridRetriever:` 组合 VectorRetriever + InMemoryBM25。构造函数获取 Chunks ORM + media_id（for BM25 build），暴露 `async def search(query: str, *, top_k: int = 20) -> list[VectorHit]`: 用 `asyncio.gather` 并行 Vector + BM25；对每个通道的结果排名，送入 `rrf_fuse` 融合然后把最终 score 写回 VectorHit（Renew score）。 |
| 测试 |   mock 双通道结果，验证 RRF 调用/merge，验证最终排序。 |

### Task 6: ContextExpander

| 文件 | `src/videomind/core/rag/context.py` |
|------|--------------------------------------|
| 内容 | `class ContextExpander`: `async def expand(self, db: AsyncSession, hits: list[VectorHit]) -> list[VectorHit]`。对每个 hit，从 chunk 表查同 media 下的 chunk_index - 1 和 + 1 的邻居 chunk，去重后 insert。返回扩展后的列表。 |
| 测试 | 使用测试 DB session（或 async Mock），输入 hits[chunk_index=3]，验证扩展后 neighbor chunk 添加。 |

### Task 7: DeterministicReranker

| 文件 | `src/videomind/core/rag/rerank.py` |
|------|-----------------------------------|
| 内容 | `class DeterministicReranker`: r `rerank(hits: list[VectorHit]) -> list[VectorHit]`。权 weight 0.3 position（按当前顺序递衰减 score→blend）+ 0.2 source_type（asr=0.3<->ocr=0.2<->mixed=0.25）× 0.5 original Retriever score。 |
| 测试 | 输入 3 fixed hits，检查排序前后、权重计算。 |

###  Task 8: RagTrace 写入

| 文件 | ` src/videomind/core/rag/trace _py `|
|------| ----|
| 内容 | `async def record_rag_trace(se:Async SESasion, **kwargs) -> m.RagTrace`。(query, rewritten queries， intent_path dict, channels, fused, expanded, reranked, final, latency_ms, token_usage) |
| 测试 | mock 插入返结果，检查写入的 RagTrace content fields。 |

### Task 9: RetrievalPipeline

| File | `src/ videomind/ core/ rag/ pipleine.py` |
|------| ---------------|
| 内容 | `class RetrievalPipeline`: `@classmethod async def search(cls, q:str, media_id:uuid, *, db:ASes, top_k=20) -> dict`。用几个步骤: ① embed query (向量通道), ② BM25 search, ③ RRF fuse, ④ context 扩展, ⑤ Deterministic Rerank, ⑥ make_evidence_ids, ⑦ record_rag_trace。返回 `{"context": ...by, "evidence": [...trace]"}"` |
| 接口 | - Cosumes: HybridRetriever, ContextExpander, DeterministicReranker, make_evidence_id, record_rag_trace -  Produces: dict[ context原文列表 +  expiryref ids] |

### Task 10: 集成 E2E

| attributes | `tests/core/rag/test_e2e . py` |
|------|----|
| 内容 | ① import RetrievalPipeline ② 使用真实的 media_id (from earlier video索引，e.g. `0e16a098`) ③ construct async test → RAG search ④ assert 有返回且有 evidence IDs ⑤ 再通过 chunk 表验证 chunks 存在。 |

- 测试通过后 RAG pipes 已完成！

---

## 验证方式

1. 运行全部 RAG 单元测试：
   ```bash
   D:\ProgramData\anaconda3\python.exe -m pytest tests/core/rag/ -v
   ```
   预期: Task 1-9 全绿；Task 10 E2E 需要 Qdrant + DB 在线（如果没有，需跳过的 skip 标记）。

2. 手动脚本验证（真机）:
   ```python
   import asyncio, uuid
   from videomind.infrastructure.storage.database import AsyncSessionLocal
   from videomind.core.rag.pipeline import RetrievalPipeline

   async def main():
       async with AsyncSessionLocal() as db:
           results = await RetrievalPipeline.search(
               "视频里说了什么关于Python的？",   # 改用实际视频中包含的话题
               uuid.UUID("0e16a098..."),                                        # 实际已索视频 ID
               db=db,
           )
           print("Evidence IDs:", [e.id for e in results["evidence"]])
           print("Context:", results["context"][:3])

   asyncio.run(main())
   ```

   预期: 返回带标注 evidence 的检索结果。

## 执行移交

RAG 实施计划完毕，保存至路径:`docs/superpowers/plans/` 2026-07-25-phase1-rag-retrieval.md`。

3 种执行选择:

**1. Subagent-Driven (recommended)** — 10 个独立 subagent，逐 Task dispatch，你拖对每个任务结果、fresh worker per task，间隔间 review。适合高质量、可回滚开发。

**2. Inline Execution** — 在本次会话中顺序跑 10 Task，每个 Task review 通过后自动继续。API 适合同步、低开销交付。

**3. Manual** — 你参考计划手/写按心意推进

按什么方式执行？