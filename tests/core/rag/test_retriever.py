"""HybridRetriever 混合检索编排的单元测试

验证 Vector + BM25 双通道并行检索、RRF 融合排序、
空通道边界情况。
"""

import uuid
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from videomind.core.rag.vector import VectorHit


# ---------------------------------------------------------------------------
# 测试辅助
# ---------------------------------------------------------------------------

@dataclass
class _MockChunk:
    """模拟 Chunk ORM 对象，提供 .id (UUID) 和 .content (str)。"""
    id: uuid.UUID
    content: str


def _chunk_id_hex(i: int) -> str:
    """生成固定 UUID 字符串，方便跟踪 chunk_id。"""
    return f"a0000000-0000-0000-0000-{i:012d}"


def _make_chunk(i: int, content: str = "") -> _MockChunk:
    return _MockChunk(id=uuid.UUID(_chunk_id_hex(i)), content=content or f"chunk-{i}")


def _make_vhit(i: int, score: float = 0.9, **kwargs) -> VectorHit:
    """创建与 _MockChunk 对应的 VectorHit。"""
    return VectorHit(
        chunk_id=_chunk_id_hex(i),
        score=score,
        content=kwargs.pop("content", f"content-{i}"),
        start_ms=kwargs.pop("start_ms", 0),
        end_ms=kwargs.pop("end_ms", 100),
        source_type=kwargs.pop("source_type", "asr"),
        content_hash=kwargs.pop("content_hash", "hash-1"),
        **kwargs,
    )


# ---------------------------------------------------------------------------
# 测试类
# ---------------------------------------------------------------------------

class TestHybridRetriever:
    """HybridRetriever 混合检索编排测试。"""

    @pytest.mark.asyncio
    async def test_hybrid_returns_merged_results(self):
        """两个通道各返回 2 条 → 融合结果应包含来自双方的全部命中。"""
        c1, c2, c3, c4 = _make_chunk(1), _make_chunk(2), _make_chunk(3), _make_chunk(4)
        chunks = [c1, c2, c3, c4]

        h1 = _make_vhit(1, score=0.92)
        h2 = _make_vhit(2, score=0.85)

        bm_hits = [
            (c3.id, 5.0),
            (c4.id, 4.0),
        ]

        with patch("videomind.core.rag.retriever.VectorRetriever") as mock_vr_cls, \
             patch("videomind.core.rag.retriever.InMemoryBM25") as mock_bm25_cls:
            v_instance = mock_vr_cls.return_value
            v_instance.retrieve = AsyncMock(return_value=[h1, h2])

            b_instance = mock_bm25_cls.return_value
            b_instance.search = MagicMock(return_value=bm_hits)

            from videomind.core.rag.retriever import HybridRetriever
            hr = HybridRetriever(media_id=uuid.uuid4(), chunks=chunks)
            results = await hr.search("test query", top_k=10)

        # 应该包含来自双方的 chunk
        result_ids = {r.chunk_id for r in results}
        assert _chunk_id_hex(1) in result_ids, "Vector channel hit 1 缺失"
        assert _chunk_id_hex(2) in result_ids, "Vector channel hit 2 缺失"
        assert _chunk_id_hex(3) in result_ids, "BM25-only hit 3 缺失"
        assert _chunk_id_hex(4) in result_ids, "BM25-only hit 4 缺失"

    @pytest.mark.asyncio
    async def test_hybrid_empty_channels(self):
        """两端都返回空 → 结果为空。"""
        chunks = [_make_chunk(1)]

        with patch("videomind.core.rag.retriever.VectorRetriever") as mock_vr_cls, \
             patch("videomind.core.rag.retriever.InMemoryBM25") as mock_bm25_cls:
            v_instance = mock_vr_cls.return_value
            v_instance.retrieve = AsyncMock(return_value=[])

            b_instance = mock_bm25_cls.return_value
            b_instance.search = MagicMock(return_value=[])

            from videomind.core.rag.retriever import HybridRetriever
            hr = HybridRetriever(media_id=uuid.uuid4(), chunks=chunks)
            results = await hr.search("empty query")

        assert results == []

    @pytest.mark.asyncio
    async def test_hybrid_rrf_ordering(self):
        """RRF 融合后按 score 降序排列。

        构造场景：
        - Vector: [chunk_a (rank 0), chunk_b (rank 1)]
        - BM25:   [chunk_b (rank 0), chunk_c (rank 1)]
        RRF 得分：
        - b: 1/61 + 1/61 > a: 1/61 > c: 1/62
        最终顺序应为 b > a > c。
        """
        a, b, c = _make_chunk(1), _make_chunk(2), _make_chunk(3)
        chunks = [a, b, c]

        # Vector: b 排第一(score 0.95), a 排第二(score 0.70)
        v_b = _make_vhit(2, score=0.95)
        v_a = _make_vhit(1, score=0.70)

        # BM25: b 排第一(5.0), c 排第二(4.0), a 排第三(3.0)
        bm_b = (b.id, 5.0)
        bm_c = (c.id, 4.0)
        bm_a = (a.id, 3.0)

        with patch("videomind.core.rag.retriever.VectorRetriever") as mock_vr_cls, \
             patch("videomind.core.rag.retriever.InMemoryBM25") as mock_bm25_cls:
            v_instance = mock_vr_cls.return_value
            v_instance.retrieve = AsyncMock(return_value=[v_b, v_a])

            b_instance = mock_bm25_cls.return_value
            b_instance.search = MagicMock(return_value=[bm_b, bm_a, bm_c])

            from videomind.core.rag.retriever import HybridRetriever
            hr = HybridRetriever(media_id=uuid.uuid4(), chunks=chunks)
            results = await hr.search("test", top_k=5)

        ids_in_order = [r.chunk_id for r in results]
        # b 排名应该最高
        assert ids_in_order[0] == _chunk_id_hex(2), f"expected chunk_b first, got {ids_in_order}"
        # 所有三个都应该出现
        assert set(ids_in_order) == {_chunk_id_hex(1), _chunk_id_hex(2), _chunk_id_hex(3)}

    @pytest.mark.asyncio
    async def test_bm25_only_hit_gets_content_from_chunks(self):
        """仅 BM25 命中且不在 Vector 中的 chunk，应从传入 chunks 回填 content。"""
        c99 = _make_chunk(99, content="bm25-unique-content")
        chunks = [c99]

        with patch("videomind.core.rag.retriever.VectorRetriever") as mock_vr_cls, \
             patch("videomind.core.rag.retriever.InMemoryBM25") as mock_bm25_cls:
            v_instance = mock_vr_cls.return_value
            v_instance.retrieve = AsyncMock(return_value=[])

            b_instance = mock_bm25_cls.return_value
            b_instance.search = MagicMock(return_value=[(c99.id, 3.0)])

            from videomind.core.rag.retriever import HybridRetriever
            hr = HybridRetriever(media_id=uuid.uuid4(), chunks=chunks)
            results = await hr.search("test")

        assert len(results) == 1
        assert results[0].content == "bm25-unique-content"
        assert results[0].chunk_id == str(c99.id)