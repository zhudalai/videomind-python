"""VectorRetriever 向量检索通道的单元测试

验证向量 Embedding + Qdrant 检索链路：空命中、payload 解析、过滤参数传递、top_k/threshold 控制。
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from videomind.core.rag.vector import VectorHit, VectorRetriever
from videomind.core.video_pipeline.embed import EmbedResult


_VECTOR_DIM = 1024  # BGE-M3 维度，与默认 embedder 对齐


def _mock_embed_result() -> EmbedResult:
    """构造一个嵌入 mock 结果。"""
    return EmbedResult(
        vectors=[[0.1] * _VECTOR_DIM],
        model_name="mock-embed",
        dim=_VECTOR_DIM,
    )


class TestVectorRetriever:
    """VectorRetriever 检索通道测试。"""

    @pytest.mark.asyncio
    async def test_retrieve_empty_when_qdrant_empty(self):
        """Qdrant search 返回 [] 时，retrieve 也应返回空列表。"""
        mock_embed = AsyncMock()
        mock_embed.embed.return_value = _mock_embed_result()
        mock_qdrant = AsyncMock()
        mock_qdrant.search.return_value = []

        with patch("videomind.core.rag.vector.get_embedding_backend", return_value=mock_embed), \
             patch("videomind.core.rag.vector.get_qdrant", return_value=mock_qdrant):
            retriever = VectorRetriever()
            results = await retriever.retrieve("关键词", uuid.uuid4())

        assert results == []

    @pytest.mark.asyncio
    async def test_retrieve_returns_hits(self):
        """Qdrant 返回 2 条 payload 时，应组装为 2 条 VectorHit。"""
        mock_embed = AsyncMock()
        mock_embed.embed.return_value = _mock_embed_result()
        mock_qdrant = AsyncMock()
        mock_qdrant.search.return_value = [
            {
                "id": "chunk-1",
                "score": 0.95,
                "payload": {
                    "chunk_id": "chunk-1",
                    "content": "向量检索测试",
                    "start_ms": 0,
                    "end_ms": 5000,
                    "source_type": "asr",
                    "content_hash": "abc123",
                },
            },
            {
                "id": "chunk-2",
                "score": 0.80,
                "payload": {
                    "chunk_id": "chunk-2",
                    "content": "第二个 chunk",
                    "start_ms": 5000,
                    "end_ms": 10000,
                    "source_type": "ocr",
                    "content_hash": "def456",
                },
            },
        ]

        with patch("videomind.core.rag.vector.get_embedding_backend", return_value=mock_embed), \
             patch("videomind.core.rag.vector.get_qdrant", return_value=mock_qdrant):
            retriever = VectorRetriever()
            results = await retriever.retrieve("测试查询", uuid.uuid4())

        assert len(results) == 2
        assert isinstance(results[0], VectorHit)
        assert results[0].chunk_id == "chunk-1"
        assert results[0].score == 0.95
        assert results[0].content == "向量检索测试"
        assert results[0].start_ms == 0
        assert results[0].end_ms == 5000
        assert results[0].source_type == "asr"
        assert results[0].content_hash == "abc123"
        assert results[1].chunk_id == "chunk-2"
        assert results[1].source_type == "ocr"

    @pytest.mark.asyncio
    async def test_media_id_filter_passed(self):
        """Qdrant.search 调用时 filters 应包含 media_id。"""
        media_id = uuid.uuid4()
        mock_embed = AsyncMock()
        mock_embed.embed.return_value = _mock_embed_result()
        mock_qdrant = AsyncMock()
        mock_qdrant.search.return_value = []

        with patch("videomind.core.rag.vector.get_embedding_backend", return_value=mock_embed), \
             patch("videomind.core.rag.vector.get_qdrant", return_value=mock_qdrant):
            retriever = VectorRetriever()
            await retriever.retrieve("查询", media_id)

        mock_qdrant.search.assert_called_once()
        call_kwargs = mock_qdrant.search.call_args.kwargs
        assert call_kwargs["filters"] == {"media_id": str(media_id)}

    @pytest.mark.asyncio
    async def test_score_threshold_passed(self):
        """score_threshold=0.5 应传入 Qdrant.search。"""
        mock_embed = AsyncMock()
        mock_embed.embed.return_value = _mock_embed_result()
        mock_qdrant = AsyncMock()
        mock_qdrant.search.return_value = []

        with patch("videomind.core.rag.vector.get_embedding_backend", return_value=mock_embed), \
             patch("videomind.core.rag.vector.get_qdrant", return_value=mock_qdrant):
            retriever = VectorRetriever()
            await retriever.retrieve("查询", uuid.uuid4(), score_threshold=0.5)

        call_kwargs = mock_qdrant.search.call_args.kwargs
        assert call_kwargs["score_threshold"] == 0.5

    @pytest.mark.asyncio
    async def test_top_k_default_and_override(self):
        """默认 top_k=10 → limit=10；top_k=5 → limit=5。"""
        mock_embed = AsyncMock()
        mock_embed.embed.return_value = _mock_embed_result()
        mock_qdrant = AsyncMock()
        mock_qdrant.search.return_value = []

        with patch("videomind.core.rag.vector.get_embedding_backend", return_value=mock_embed), \
             patch("videomind.core.rag.vector.get_qdrant", return_value=mock_qdrant):
            retriever = VectorRetriever()

            # 默认 top_k=10
            await retriever.retrieve("test", uuid.uuid4())
            assert mock_qdrant.search.call_args.kwargs["limit"] == 10

            # 覆盖 top_k=5
            await retriever.retrieve("test", uuid.uuid4(), top_k=5)
            assert mock_qdrant.search.call_args.kwargs["limit"] == 5