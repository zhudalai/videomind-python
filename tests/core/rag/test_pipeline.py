"""pipeline 模块测试 —— mock 所有子依赖，验证检索管线入口编排逻辑。"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
    @patch("videomind.core.rag.pipeline.DeterministicReranker")
    @patch("videomind.core.rag.pipeline.ContextExpander")
    @patch("videomind.core.rag.pipeline.HybridRetriever")
    async def test_search_returns_context_and_evidence(
        self,
        mock_hr_cls,
        mock_expander_cls,
        mock_reranker_cls,
        mock_trace,
        mock_db,
        media_uuid,
        chunk_uuid,
    ):
        """mock 返回 2 hits，验证 result 包含 context 和 evidence 列表。"""
        from videomind.core.rag import pipeline

        hit1 = make_hit(chunk_uuid, score=0.9, content="first chunk")
        hit2 = make_hit(uuid.uuid4(), score=0.7, content="second chunk")

        # HybridRetriever.search → [hit1, hit2]
        mock_retriever = MagicMock()
        mock_retriever.search = AsyncMock(return_value=[hit1, hit2])
        mock_hr_cls.return_value = mock_retriever

        # ContextExpander.expand → 透传
        mock_expander = MagicMock()
        mock_expander.expand = AsyncMock(return_value=[hit1, hit2])
        mock_expander_cls.return_value = mock_expander

        # DeterministicReranker.rerank → 透传
        mock_reranker = MagicMock()
        mock_reranker.rerank = MagicMock(return_value=[hit2, hit1])
        mock_reranker_cls.return_value = mock_reranker

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

        # 验证 evidence_id 格式
        for ev in result["evidence"]:
            assert ev.id.startswith("EID_")
            assert ev.chunk_id in [str(chunk_uuid), str(hit2.chunk_id)]
            assert ev.score > 0

    @patch("videomind.core.rag.pipeline.record_rag_trace", new_callable=AsyncMock)
    @patch("videomind.core.rag.pipeline.DeterministicReranker")
    @patch("videomind.core.rag.pipeline.ContextExpander")
    @patch("videomind.core.rag.pipeline.HybridRetriever")
    async def test_search_empty_result(
        self,
        mock_hr_cls,
        mock_expander_cls,
        mock_reranker_cls,
        mock_trace,
        mock_db,
        media_uuid,
    ):
        """HybridRetriever 返回空 → context/evidence/raw_hits 均为空。"""
        from videomind.core.rag import pipeline

        mock_retriever = MagicMock()
        mock_retriever.search = AsyncMock(return_value=[])
        mock_hr_cls.return_value = mock_retriever

        mock_expander = MagicMock()
        mock_expander.expand = AsyncMock(return_value=[])
        mock_expander_cls.return_value = mock_expander

        mock_reranker = MagicMock()
        mock_reranker.rerank = MagicMock(return_value=[])
        mock_reranker_cls.return_value = mock_reranker

        result = await pipeline.search(mock_db, "nothing", media_uuid)

        assert result == {"context": [], "evidence": [], "raw_hits": []}

        # 即使结果为空，trace 也应该被调用
        mock_trace.assert_awaited_once()

    @patch("videomind.core.rag.pipeline.record_rag_trace", new_callable=AsyncMock)
    @patch("videomind.core.rag.pipeline.DeterministicReranker")
    @patch("videomind.core.rag.pipeline.ContextExpander")
    @patch("videomind.core.rag.pipeline.HybridRetriever")
    async def test_search_delegates_to_pipeline_order(
        self,
        mock_hr_cls,
        mock_expander_cls,
        mock_reranker_cls,
        mock_trace,
        mock_db,
        media_uuid,
        chunk_uuid,
    ):
        """验证 expander → reranker → evidence_id 调用链顺序。"""
        from videomind.core.rag import pipeline
        from unittest.mock import call

        hit = make_hit(chunk_uuid)

        mock_retriever = MagicMock()
        mock_retriever.search = AsyncMock(return_value=[hit])
        mock_hr_cls.return_value = mock_retriever

        mock_expander = MagicMock()
        mock_expander.expand = AsyncMock(return_value=[hit])
        mock_expander_cls.return_value = mock_expander

        mock_reranker = MagicMock()
        mock_reranker.rerank = MagicMock(return_value=[hit])
        mock_reranker_cls.return_value = mock_reranker

        # 记录调用顺序
        parent = MagicMock()
        parent.attach_mock(mock_retriever.search, "search")
        parent.attach_mock(mock_expander.expand, "expand")
        parent.attach_mock(mock_reranker.rerank, "rerank")
        parent.attach_mock(mock_trace, "trace")

        result = await pipeline.search(mock_db, "query", media_uuid)

        # 验证调用链顺序：search → expand → rerank → trace
        call_order = [c[0] for c in parent.method_calls]
        assert call_order.index("search") < call_order.index("expand")
        assert call_order.index("expand") < call_order.index("rerank")
        assert call_order.index("rerank") < call_order.index("trace")