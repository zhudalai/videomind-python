"""pipeline 模块测试 —— mock 所有子依赖，验证检索管线入口编排逻辑。"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from videomind.core.rag.rerank_backend import DeterministicRerankerAdapter
from videomind.core.rag.vector import VectorHit


@pytest.fixture
def media_uuid() -> uuid.UUID:
    """测试用固定 media_id。"""
    return uuid.uuid4()


@pytest.fixture
def chunk_uuid() -> uuid.UUID:
    """测试用固定 chunk UUID。"""
    return uuid.uuid4()


@pytest.fixture
def mock_db() -> AsyncMock:
    """创建一个 mock AsyncSession，预配置 execute()→scalars()→all() 调用链。"""
    db = AsyncMock()
    result = MagicMock()
    scalars_result = MagicMock()
    scalars_result.all.return_value = []
    result.scalars.return_value = scalars_result
    db.execute.return_value = result
    return db


def make_hit(chunk_id: uuid.UUID, score: float = 0.85, content: str = "test content") -> VectorHit:
    """快速构造一个用于 mock 的 VectorHit。"""
    return VectorHit(
        chunk_id=str(chunk_id),
        score=score,
        content=content,
        start_ms=1000,
        end_ms=5000,
        source_type="asr",
        content_hash="abc123",
    )


class TestPipelineSearch:
    """RetrievalPipeline.search() 单元测试（mock 所有依赖）。"""

    @patch("videomind.core.rag.pipeline.record_rag_trace", new_callable=AsyncMock)
    @patch("videomind.core.rag.pipeline.get_reranker")
    @patch("videomind.core.rag.pipeline.ContextExpander")
    @patch("videomind.core.rag.pipeline.HybridRetriever")
    async def test_search_returns_context_and_evidence(
        self,
        mock_hr_cls,
        mock_expander_cls,
        mock_get_rr,
        mock_trace,
        mock_db,
        media_uuid,
        chunk_uuid,
    ):
        """mock 返回 2 hits，验证 result 包含 context 和 evidence 列表。"""
        from videomind.core.rag import pipeline

        pipeline.invalidate_retriever_cache()  # 测试间隔离 _RETRIEVER_CACHE
        hit1 = make_hit(chunk_uuid, score=0.9, content="first chunk")
        hit2 = make_hit(uuid.uuid4(), score=0.7, content="second chunk")

        # HybridRetriever.search → [hit1, hit2]
        mock_retriever = MagicMock()
        mock_retriever.search = AsyncMock(return_value=[hit1, hit2])
        mock_hr_cls.return_value = mock_retriever

        # ContextExpander.expand → 透传（P1-2 后 expand 在 rerank 之后，原样返回 rerank 顺序）
        mock_expander = MagicMock()

        async def _passthrough(_db, _mid, hits):
            return hits

        mock_expander.expand = AsyncMock(side_effect=_passthrough)
        mock_expander_cls.return_value = mock_expander

        # get_reranker() → plain mock，rerank(async) 返回 [hit2, hit1]
        mock_reranker = MagicMock()
        mock_reranker.rerank = AsyncMock(return_value=[hit2, hit1])
        mock_get_rr.return_value = mock_reranker

        result = await pipeline.search(mock_db, "test query", media_uuid)

        assert "context" in result
        assert "evidence" in result
        assert "raw_hits" in result

        assert len(result["context"]) == 2
        assert len(result["evidence"]) == 2
        assert len(result["raw_hits"]) == 2

        # 验证 context 顺序与 rerank 结果一致
        assert result["context"][0] == "second chunk"
        assert result["context"][1] == "first chunk"

        # 验证 evidence_id 格式 + P2-1g score 已 min-max 归一到 [0,1]
        for ev in result["evidence"]:
            assert ev.id.startswith("EID_")
            assert ev.chunk_id in [str(chunk_uuid), str(hit2.chunk_id)]
            assert 0.0 <= ev.score <= 1.0
        # 归一后得分最高命中（原 0.9）为 1.0，得分最低（原 0.7）为 0.0
        assert result["evidence"][0].score == 0.0  # hit2 原 0.7 → 归一最低
        assert result["evidence"][1].score == 1.0  # hit1 原 0.9 → 归一最高

    @patch("videomind.core.rag.pipeline.record_rag_trace", new_callable=AsyncMock)
    @patch("videomind.core.rag.pipeline.get_reranker")
    @patch("videomind.core.rag.pipeline.ContextExpander")
    @patch("videomind.core.rag.pipeline.HybridRetriever")
    async def test_search_empty_result(
        self,
        mock_hr_cls,
        mock_expander_cls,
        mock_get_rr,
        mock_trace,
        mock_db,
        media_uuid,
    ):
        """HybridRetriever 返回空 → context/evidence/raw_hits 均为空。"""
        from videomind.core.rag import pipeline

        pipeline.invalidate_retriever_cache()
        mock_retriever = MagicMock()
        mock_retriever.search = AsyncMock(return_value=[])
        mock_hr_cls.return_value = mock_retriever

        mock_expander = MagicMock()
        mock_expander.expand = AsyncMock(return_value=[])
        mock_expander_cls.return_value = mock_expander

        mock_reranker = MagicMock()
        mock_reranker.rerank = AsyncMock(return_value=[])
        mock_get_rr.return_value = mock_reranker

        result = await pipeline.search(mock_db, "nothing", media_uuid)

        assert result == {"context": [], "evidence": [], "raw_hits": []}

        # 即使结果为空，trace 也应该被调用
        mock_trace.assert_awaited_once()

    @patch("videomind.core.rag.pipeline.record_rag_trace", new_callable=AsyncMock)
    @patch("videomind.core.rag.pipeline.get_reranker")
    @patch("videomind.core.rag.pipeline.ContextExpander")
    @patch("videomind.core.rag.pipeline.HybridRetriever")
    async def test_search_delegates_to_pipeline_order(
        self,
        mock_hr_cls,
        mock_expander_cls,
        mock_get_rr,
        mock_trace,
        mock_db,
        media_uuid,
        chunk_uuid,
    ):
        """验证 reranker → expander → evidence_id 调用链顺序（P1-2 后）。"""
        from videomind.core.rag import pipeline

        pipeline.invalidate_retriever_cache()
        hit = make_hit(chunk_uuid)

        mock_retriever = MagicMock()
        mock_retriever.search = AsyncMock(return_value=[hit])
        mock_hr_cls.return_value = mock_retriever

        mock_expander = MagicMock()
        mock_expander.expand = AsyncMock(return_value=[hit])
        mock_expander_cls.return_value = mock_expander

        mock_reranker = MagicMock()
        mock_reranker.rerank = AsyncMock(return_value=[hit])
        mock_get_rr.return_value = mock_reranker

        # 记录调用顺序
        parent = MagicMock()
        parent.attach_mock(mock_retriever.search, "search")
        parent.attach_mock(mock_expander.expand, "expand")
        parent.attach_mock(mock_reranker.rerank, "rerank")
        parent.attach_mock(mock_trace, "trace")

        result = await pipeline.search(mock_db, "query", media_uuid)

        # 验证调用链顺序（P1-2 后）：search → rerank → expand → trace
        call_order = [c[0] for c in parent.method_calls]
        assert call_order.index("search") < call_order.index("rerank")
        assert call_order.index("rerank") < call_order.index("expand")
        assert call_order.index("expand") < call_order.index("trace")


# ---------------------------------------------------------------------------
# P1-2：rerank 在 expand 之前 —— 邻居以 score=0.0 追加置尾，不参与重排
# ---------------------------------------------------------------------------


def _make_seq_db(chunk_lists: list[list]) -> AsyncMock:
    """构造 mock AsyncSession：execute() 按调用顺序依次返回各组 chunk 列表。

    用于 pipeline 集成测试（真实 DeterministicReranker + 真实 ContextExpander）：
      第 1 次 → _get_retriever 拉全量 chunk（HybridRetriever 被 mock，忽略）；
      第 2 次 → ContextExpander 查命中 chunk 的 chunk_index；
      第 3 次 → ContextExpander 查 ±1 邻居 chunk。
    """

    def _result(chunks):
        r = MagicMock()
        s = MagicMock()
        s.all.return_value = chunks
        r.scalars.return_value = s
        return r

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_result(c) for c in chunk_lists])
    return db


def _Chunk(chunk_id: uuid.UUID, media_id: uuid.UUID, idx: int, content: str, source: str = "asr") -> object:
    """构造一个字段足够的 Chunk ORM 实例，供真实 ContextExpander 读取。"""
    from videomind.infrastructure.storage.models import Chunk

    return Chunk(
        id=chunk_id, media_id=media_id, chunk_index=idx, content=content,
        start_ms=idx * 1000, end_ms=(idx + 1) * 1000, source_type=source,
        content_hash=f"h{idx}", token_count=0,
    )


class TestPipelineRerankExpandOrder:
    """P1-2：调用链由 search→expand→rerank 改为 search→rerank→expand；邻居不参与重排。"""

    @patch("videomind.core.rag.pipeline.record_rag_trace", new_callable=AsyncMock)
    @patch("videomind.core.rag.pipeline.get_reranker")
    @patch("videomind.core.rag.pipeline.ContextExpander")
    @patch("videomind.core.rag.pipeline.HybridRetriever")
    async def test_rerank_runs_before_expand(
        self, mock_hr_cls, mock_expander_cls, mock_get_rr,
        mock_trace, mock_db, media_uuid, chunk_uuid,
    ):
        """search → rerank → expand → trace 调用顺序。"""
        from videomind.core.rag import pipeline

        pipeline.invalidate_retriever_cache()
        hit = make_hit(chunk_uuid)
        mock_retriever = MagicMock()
        mock_retriever.search = AsyncMock(return_value=[hit])
        mock_hr_cls.return_value = mock_retriever
        mock_expander = MagicMock()
        mock_expander.expand = AsyncMock(return_value=[hit])
        mock_expander_cls.return_value = mock_expander
        mock_reranker = MagicMock()
        mock_reranker.rerank = AsyncMock(return_value=[hit])
        mock_get_rr.return_value = mock_reranker

        parent = MagicMock()
        parent.attach_mock(mock_retriever.search, "search")
        parent.attach_mock(mock_reranker.rerank, "rerank")
        parent.attach_mock(mock_expander.expand, "expand")
        parent.attach_mock(mock_trace, "trace")

        await pipeline.search(mock_db, "query", media_uuid)

        order = [c[0] for c in parent.method_calls]
        assert order.index("search") < order.index("rerank")
        assert order.index("rerank") < order.index("expand")
        assert order.index("expand") < order.index("trace")

    @patch("videomind.core.rag.pipeline.record_rag_trace", new_callable=AsyncMock)
    @patch("videomind.core.rag.pipeline.get_reranker")
    @patch("videomind.core.rag.pipeline.ContextExpander")
    @patch("videomind.core.rag.pipeline.HybridRetriever")
    async def test_rerank_receives_only_real_hits(
        self, mock_hr_cls, mock_expander_cls, mock_get_rr,
        mock_trace, mock_db, media_uuid, chunk_uuid,
    ):
        """rerank 入参仅含真实检索命中；邻居由 expand 在 rerank 后生成，不进入 rerank。"""
        from videomind.core.rag import pipeline

        pipeline.invalidate_retriever_cache()
        real = make_hit(chunk_uuid, score=0.3, content="real")
        mock_retriever = MagicMock()
        mock_retriever.search = AsyncMock(return_value=[real])
        mock_hr_cls.return_value = mock_retriever

        reranked = make_hit(chunk_uuid, score=0.9, content="real")
        mock_reranker = MagicMock()
        mock_reranker.rerank = AsyncMock(return_value=[reranked])
        mock_get_rr.return_value = mock_reranker

        neighbor = make_hit(uuid.uuid4(), score=0.0, content="nbr")
        mock_expander = MagicMock()
        mock_expander.expand = AsyncMock(return_value=[reranked, neighbor])
        mock_expander_cls.return_value = mock_expander

        await pipeline.search(mock_db, "q", media_uuid)

        # rerank 收到 query(位置1) + 真实命中列表(位置2)，仅含真实命中
        rerank_args = mock_reranker.rerank.call_args
        rerank_input = rerank_args[1]["hits"] if "hits" in rerank_args.kwargs else rerank_args[0][1]
        assert [h.chunk_id for h in rerank_input] == [str(chunk_uuid)]
        assert all(h.chunk_id != neighbor.chunk_id for h in rerank_input)

        # expand 收到的是 rerank 输出（位置参数第 3 个），而非原始检索命中
        expand_input = mock_expander.expand.call_args[0][2]
        assert [h.chunk_id for h in expand_input] == [str(chunk_uuid)]

    @patch("videomind.core.rag.pipeline.record_rag_trace", new_callable=AsyncMock)
    @patch("videomind.core.rag.pipeline.get_reranker")
    @patch("videomind.core.rag.pipeline.ContextExpander")
    @patch("videomind.core.rag.pipeline.HybridRetriever")
    async def test_raw_hits_scores_preserved_despite_rerank_mutation(
        self, mock_hr_cls, mock_expander_cls, mock_get_rr,
        mock_trace, mock_db, media_uuid, chunk_uuid,
    ):
        """rerank 原地改分不应污染 raw_hits 原始检索分（pipeline 传拷贝给 rerank）。"""
        from videomind.core.rag import pipeline

        pipeline.invalidate_retriever_cache()
        original = make_hit(chunk_uuid, score=0.42, content="x")
        mock_retriever = MagicMock()
        mock_retriever.search = AsyncMock(return_value=[original])
        mock_hr_cls.return_value = mock_retriever

        async def fake_rerank(_query, hits):
            for h in hits:
                h.score = 0.99  # 模拟真实 reranker 原地改分
            return hits

        mock_reranker = MagicMock()
        mock_reranker.rerank = AsyncMock(side_effect=fake_rerank)
        mock_get_rr.return_value = mock_reranker

        async def _passthrough(_db, _mid, hits):
            return hits

        mock_expander = MagicMock()
        mock_expander.expand = AsyncMock(side_effect=_passthrough)
        mock_expander_cls.return_value = mock_expander

        result = await pipeline.search(mock_db, "q", media_uuid)

        # raw_hits 保留原始检索分（拷贝隔离，不归一）
        assert result["raw_hits"][0].score == 0.42
        # evidence 用 rerank 后的分；单真命中归一化退化保留高分为 1.0（P2-1g span≈0）
        assert result["evidence"][0].score == 1.0


class TestPipelineRerankBeforeExpandIntegration:
    """P1-2 端到端：真实 DeterministicRerankerAdapter + 真实 ContextExpander（不联网）。"""

    @patch("videomind.core.rag.pipeline.record_rag_trace", new_callable=AsyncMock)
    @patch("videomind.core.rag.pipeline.get_reranker", return_value=DeterministicRerankerAdapter())
    @patch("videomind.core.rag.pipeline.HybridRetriever")
    async def test_neighbors_appended_not_reranked(self, mock_hr_cls, mock_get_rr, mock_trace, media_uuid):
        """单命中 + 双邻居：真实命中 rerank 分>0 居首，邻居 0.0 追加置尾；raw_hits 不含邻居且保原分。

        patch get_reranker 注入真实 DeterministicRerankerAdapter，避免 .env RERANK_PROVIDER=api
        时真打 OpenRouter 联网（单元测试须离线确定）。
        """
        from videomind.core.rag import pipeline

        pipeline.invalidate_retriever_cache()

        c0, c1, c2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        raw_hit = VectorHit(
            chunk_id=str(c1), score=0.5, content="命中",
            start_ms=1000, end_ms=2000, source_type="asr", content_hash="h1",
        )
        mock_hr = MagicMock()
        mock_hr.search = AsyncMock(return_value=[raw_hit])
        mock_hr_cls.return_value = mock_hr

        # _get_retriever 全量 chunk(空) → expander 命中查询 → expander 邻居查询
        db = _make_seq_db([
            [],
            [_Chunk(c1, media_uuid, 1, "命中", "asr")],
            [_Chunk(c0, media_uuid, 0, "左邻", "asr"), _Chunk(c2, media_uuid, 2, "右邻", "asr")],
        ])

        result = await pipeline.search(db, "命中", media_uuid, top_k=20)

        # 真实命中排首位（rerank 分 > 0），双邻居以 0.0 追加置尾
        assert len(result["evidence"]) == 3
        assert result["evidence"][0].chunk_id == str(c1)
        assert result["evidence"][0].score > 0.0
        assert result["evidence"][1].score == 0.0
        assert result["evidence"][2].score == 0.0
        nbr_ids = {result["evidence"][1].chunk_id, result["evidence"][2].chunk_id}
        assert nbr_ids == {str(c0), str(c2)}

        # raw_hits 仅含真实命中，且保留原始检索分（未被 rerank 改分污染）
        assert len(result["raw_hits"]) == 1
        assert result["raw_hits"][0].chunk_id == str(c1)
        assert result["raw_hits"][0].score == 0.5


# ---------------------------------------------------------------------------
# P2-1g：evidence.score min-max 归一化 —— 修复 cross-encoder 低绝对分与降级高分混合尺度 bug
# ---------------------------------------------------------------------------


class TestPipelineScoreNormalization:
    """evidence.score 归一到 [0,1]：跨后端尺度统一、邻居不参与、raw_hits 保真、退化单命中。

    修复的 bug：cross-encoder（Nemotron raw 中文 ~0.16）与降级 Deterministic（线性加权
    ~0.5-0.95）跨 query/media 按 score 合并时，降级高分挤掉真实高相关 hit。
    """

    @patch("videomind.core.rag.pipeline.record_rag_trace", new_callable=AsyncMock)
    @patch("videomind.core.rag.pipeline.get_reranker")
    @patch("videomind.core.rag.pipeline.ContextExpander")
    @patch("videomind.core.rag.pipeline.HybridRetriever")
    async def test_scores_min_max_normalized_real_hits_only(
        self, mock_hr_cls, mock_expander_cls, mock_get_rr,
        mock_trace, mock_db, media_uuid,
    ):
        """真实命中最小分→0、最大分→1，邻居 0.0 不参与归一保持 0.0。"""
        from videomind.core.rag import pipeline

        pipeline.invalidate_retriever_cache()
        # 模拟 cross-encoder 低绝对分：真命中 0.05/0.16，邻居 0.0
        h_low = make_hit(uuid.uuid4(), score=0.05, content="low")
        h_hi = make_hit(uuid.uuid4(), score=0.16, content="hi")
        nbr = make_hit(uuid.uuid4(), score=0.0, content="nbr")
        mock_retriever = MagicMock()
        mock_retriever.search = AsyncMock(return_value=[h_low, h_hi])
        mock_hr_cls.return_value = mock_retriever
        mock_reranker = MagicMock()
        mock_reranker.rerank = AsyncMock(return_value=[h_hi, h_low])
        mock_get_rr.return_value = mock_reranker
        mock_expander = MagicMock()
        mock_expander.expand = AsyncMock(return_value=[h_hi, h_low, nbr])
        mock_expander_cls.return_value = mock_expander

        result = await pipeline.search(mock_db, "q", media_uuid)

        ev = result["evidence"]
        # 真命中 min-max：h_hi(0.16)→1.0、h_low(0.05)→0.0；邻居 0.0 不参与统计保持 0.0
        assert ev[0].score == 1.0  # 0.16→归一最高
        assert ev[1].score == 0.0  # 0.05→归一最低
        assert ev[2].score == 0.0  # 邻居
        # raw_hits 原检索分保真（不被 rerank 改也不归一）
        assert {h.score for h in result["raw_hits"]} == {0.05, 0.16}

    @patch("videomind.core.rag.pipeline.record_rag_trace", new_callable=AsyncMock)
    @patch("videomind.core.rag.pipeline.get_reranker")
    @patch("videomind.core.rag.pipeline.ContextExpander")
    @patch("videomind.core.rag.pipeline.HybridRetriever")
    async def test_single_real_hit_normalizes_to_one(
        self, mock_hr_cls, mock_expander_cls, mock_get_rr,
        mock_trace, mock_db, media_uuid, chunk_uuid,
    ):
        """单真实命中 span=0 → 退化保留高分概念为 1.0，不为 0.0（避免高分变 0）。"""
        from videomind.core.rag import pipeline

        pipeline.invalidate_retriever_cache()
        only = make_hit(chunk_uuid, score=0.42, content="only")
        nbr = make_hit(uuid.uuid4(), score=0.0, content="nbr")
        mock_retriever = MagicMock()
        mock_retriever.search = AsyncMock(return_value=[only])
        mock_hr_cls.return_value = mock_retriever
        mock_reranker = MagicMock()
        mock_reranker.rerank = AsyncMock(return_value=[only])
        mock_get_rr.return_value = mock_reranker
        mock_expander = MagicMock()
        mock_expander.expand = AsyncMock(return_value=[only, nbr])
        mock_expander_cls.return_value = mock_expander

        result = await pipeline.search(mock_db, "q", media_uuid)

        ev = result["evidence"]
        assert ev[0].score == 1.0  # 单真命中退化保留高分
        assert ev[1].score == 0.0  # 邻居

class TestPipelineRecallLayering:
    """P2-5 D-α：召回水位分层 —— recall_k 宽召回 / top_k 喂下游窄窗口解耦。"""

    @patch("videomind.core.rag.pipeline.record_rag_trace", new_callable=AsyncMock)
    @patch("videomind.core.rag.pipeline.get_reranker")
    @patch("videomind.core.rag.pipeline.ContextExpander")
    @patch("videomind.core.rag.pipeline.HybridRetriever")
    async def test_recall_k_widens_retriever_window_top_k_trims_context(
        self, mock_hr_cls, mock_expander_cls, mock_get_rr,
        mock_trace, mock_db, media_uuid,
    ):
        """recall_k=5, top_k=2：retriever 召回 5 条（宽），evidence 仅 2 条（窄喂下游），raw_hits 露出 5 条。"""
        from videomind.core.rag import pipeline

        pipeline.invalidate_retriever_cache()
        hits = [make_hit(uuid.uuid4(), score=0.1 * (5 - i), content=f"h{i}") for i in range(5)]
        mock_retriever = MagicMock()
        mock_retriever.search = AsyncMock(return_value=hits)
        mock_hr_cls.return_value = mock_retriever

        mock_reranker = MagicMock()
        mock_reranker.rerank = AsyncMock(return_value=hits)
        mock_get_rr.return_value = mock_reranker

        mock_expander = MagicMock()
        mock_expander.expand = AsyncMock(side_effect=lambda _db, _mid, h: list(h))
        mock_expander_cls.return_value = mock_expander

        result = await pipeline.search(mock_db, "q", media_uuid, top_k=2, recall_k=5)

        # retriever 用宽召回窗口（recall_k=5）调用
        assert mock_retriever.search.call_args.kwargs["top_k"] == 5
        # 喂下游仅 top_k=2 条
        assert len(result["evidence"]) == 2
        assert len(result["context"]) == 2
        # raw_hits 暴露宽召回全貌（5 条），不被 top_k 截断
        assert len(result["raw_hits"]) == 5

    @patch("videomind.core.rag.pipeline.record_rag_trace", new_callable=AsyncMock)
    @patch("videomind.core.rag.pipeline.get_reranker")
    @patch("videomind.core.rag.pipeline.ContextExpander")
    @patch("videomind.core.rag.pipeline.HybridRetriever")
    async def test_recall_k_none_falls_back_to_top_k(
        self, mock_hr_cls, mock_expander_cls, mock_get_rr,
        mock_trace, mock_db, media_uuid,
    ):
        """recall_k=None 向后兼容：retriever 召回窗口 == top_k，行为同旧。"""
        from videomind.core.rag import pipeline

        pipeline.invalidate_retriever_cache()
        hits = [make_hit(uuid.uuid4(), score=0.5, content="h0"), make_hit(uuid.uuid4(), score=0.3, content="h1")]
        mock_retriever = MagicMock()
        mock_retriever.search = AsyncMock(return_value=hits)
        mock_hr_cls.return_value = mock_retriever
        mock_reranker = MagicMock()
        mock_reranker.rerank = AsyncMock(return_value=hits)
        mock_get_rr.return_value = mock_reranker
        mock_expander = MagicMock()
        mock_expander.expand = AsyncMock(side_effect=lambda _db, _mid, h: list(h))
        mock_expander_cls.return_value = mock_expander

        result = await pipeline.search(mock_db, "q", media_uuid, top_k=2)

        # 不传 recall_k → retriever 窗口退化为 top_k=2
        assert mock_retriever.search.call_args.kwargs["top_k"] == 2
        assert len(result["evidence"]) == 2
        assert len(result["raw_hits"]) == 2

    @patch("videomind.core.rag.pipeline.record_rag_trace", new_callable=AsyncMock)
    @patch("videomind.core.rag.pipeline.get_reranker")
    @patch("videomind.core.rag.pipeline.ContextExpander")
    @patch("videomind.core.rag.pipeline.HybridRetriever")
    async def test_empty_result_no_division_error(
        self, mock_hr_cls, mock_expander_cls, mock_get_rr,
        mock_trace, mock_db, media_uuid,
    ):
        """空命中 real_scores 为空 → 不归一、不触发零除；context/evidence 为空。"""
        from videomind.core.rag import pipeline

        pipeline.invalidate_retriever_cache()
        mock_retriever = MagicMock()
        mock_retriever.search = AsyncMock(return_value=[])
        mock_hr_cls.return_value = mock_retriever
        mock_reranker = MagicMock()
        mock_reranker.rerank = AsyncMock(return_value=[])
        mock_get_rr.return_value = mock_reranker
        mock_expander = MagicMock()
        mock_expander.expand = AsyncMock(return_value=[])
        mock_expander_cls.return_value = mock_expander

        result = await pipeline.search(mock_db, "nothing", media_uuid)

        assert result == {"context": [], "evidence": [], "raw_hits": []}
        assert result["evidence"] == []  # 归一化空列表不报错（real_scores=[] → lo/hi=0 跳过）