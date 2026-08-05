"""pipeline 模块的 BM25/HybridRetriever 进程内缓存测试。

验证：(1) 同一 media_id 重复检索只构建一次 HybridRetriever（省掉全量 chunk 拉取
+ BM25 重建）；(2) invalidate_cache(media_id) 后下一次检索重建；(3) 不同 media_id
各自独立缓存。
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from videomind.core.rag.vector import VectorHit


@pytest.fixture
def mock_db() -> AsyncMock:
    """mock AsyncSession：execute() → scalars() → all() 返回空 chunk 列表。"""
    db = AsyncMock()
    result = MagicMock()
    scalars_result = MagicMock()
    scalars_result.all.return_value = []
    result.scalars.return_value = scalars_result
    db.execute.return_value = result
    return db


class TestPipelineRetrieverCache:
    """HybridRetriever 按 media_id 进程内缓存。"""

    @patch("videomind.core.rag.pipeline.record_rag_trace", new_callable=AsyncMock)
    @patch("videomind.core.rag.pipeline.get_reranker")
    @patch("videomind.core.rag.pipeline.ContextExpander")
    @patch("videomind.core.rag.pipeline.HybridRetriever")
    async def test_retriever_cached_per_media(
        self,
        mock_hr_cls,
        mock_expander_cls,
        mock_get_rr,
        mock_trace,
        mock_db,
    ):
        """同一 media_id 两次检索，HybridRetriever 应只实例化一次。"""
        from videomind.core.rag import pipeline

        # 清缓存保证起点干净
        pipeline.invalidate_retriever_cache()

        hit = VectorHit(chunk_id=str(uuid.uuid4()), score=0.5, content="x")
        mock_hr = MagicMock()
        mock_hr.search = AsyncMock(return_value=[hit])
        mock_hr_cls.return_value = mock_hr

        mock_expander = MagicMock()
        mock_expander.expand = AsyncMock(return_value=[hit])
        mock_expander_cls.return_value = mock_expander
        mock_reranker = MagicMock()
        mock_reranker.rerank = AsyncMock(return_value=[hit])
        mock_get_rr.return_value = mock_reranker

        mid = uuid.uuid4()
        await pipeline.search(mock_db, "q1", mid)
        await pipeline.search(mock_db, "q2", mid)

        # HybridRetriever(...) 构造调用应只发生 1 次（第二次命中缓存）
        assert mock_hr_cls.call_count == 1, (
            f"expected 1 HybridRetriever build, got {mock_hr_cls.call_count}"
        )
        # 但 search 被调用 2 次（每次检索都执行）
        assert mock_hr.search.await_count == 2

    @patch("videomind.core.rag.pipeline.record_rag_trace", new_callable=AsyncMock)
    @patch("videomind.core.rag.pipeline.get_reranker")
    @patch("videomind.core.rag.pipeline.ContextExpander")
    @patch("videomind.core.rag.pipeline.HybridRetriever")
    async def test_invalidate_rebuilds(
        self,
        mock_hr_cls,
        mock_expander_cls,
        mock_get_rr,
        mock_trace,
        mock_db,
    ):
        """invalidate_retriever_cache(mid) 后再检索应重建该 media 的 retriever。"""
        from videomind.core.rag import pipeline

        pipeline.invalidate_retriever_cache()

        hit = VectorHit(chunk_id=str(uuid.uuid4()), score=0.5, content="x")
        mock_hr = MagicMock()
        mock_hr.search = AsyncMock(return_value=[hit])
        mock_hr_cls.return_value = mock_hr
        mock_expander = MagicMock()
        mock_expander.expand = AsyncMock(return_value=[hit])
        mock_expander_cls.return_value = mock_expander
        mock_reranker = MagicMock()
        mock_reranker.rerank = AsyncMock(return_value=[hit])
        mock_get_rr.return_value = mock_reranker

        mid = uuid.uuid4()
        await pipeline.search(mock_db, "q1", mid)
        pipeline.invalidate_retriever_cache(mid)
        await pipeline.search(mock_db, "q2", mid)

        # 失效后第二次检索应重建 → 2 次构造
        assert mock_hr_cls.call_count == 2, (
            f"expected 2 builds after invalidate, got {mock_hr_cls.call_count}"
        )

    @patch("videomind.core.rag.pipeline.record_rag_trace", new_callable=AsyncMock)
    @patch("videomind.core.rag.pipeline.get_reranker")
    @patch("videomind.core.rag.pipeline.ContextExpander")
    @patch("videomind.core.rag.pipeline.HybridRetriever")
    async def test_different_media_separate_caches(
        self,
        mock_hr_cls,
        mock_expander_cls,
        mock_get_rr,
        mock_trace,
        mock_db,
    ):
        """不同 media_id 各自独立缓存，各构建一次。"""
        from videomind.core.rag import pipeline

        pipeline.invalidate_retriever_cache()

        hit = VectorHit(chunk_id=str(uuid.uuid4()), score=0.5, content="x")
        mock_hr = MagicMock()
        mock_hr.search = AsyncMock(return_value=[hit])
        mock_hr_cls.return_value = mock_hr
        mock_expander = MagicMock()
        mock_expander.expand = AsyncMock(return_value=[hit])
        mock_expander_cls.return_value = mock_expander
        mock_reranker = MagicMock()
        mock_reranker.rerank = AsyncMock(return_value=[hit])
        mock_get_rr.return_value = mock_reranker

        m1, m2 = uuid.uuid4(), uuid.uuid4()
        await pipeline.search(mock_db, "q", m1)
        await pipeline.search(mock_db, "q", m2)
        await pipeline.search(mock_db, "q", m1)
        await pipeline.search(mock_db, "q", m2)

        # 两个 media 各构建一次 → 共 2 次
        assert mock_hr_cls.call_count == 2, (
            f"expected 2 builds for 2 media, got {mock_hr_cls.call_count}"
        )
