"""ContextExpander 上下文扩展 单元测试。

遵循严格 TDD：先写测试 → FAIL → 实现 → PASS。
mock AsyncSession，不连接真实 DB。
"""

import uuid

import pytest
from unittest.mock import AsyncMock, MagicMock

from videomind.core.rag.context import ContextExpander
from videomind.core.rag.vector import VectorHit
from videomind.infrastructure.storage.models import Chunk


def _make_chunk(
    chunk_id: uuid.UUID,
    media_id: uuid.UUID,
    chunk_index: int,
    content: str = "",
    start_ms: int | None = None,
    end_ms: int | None = None,
    source_type: str = "asr",
    content_hash: str = "",
) -> Chunk:
    """构造一个 Chunk ORM 实例用于 mock 返回。"""
    return Chunk(
        id=chunk_id,
        media_id=media_id,
        chunk_index=chunk_index,
        content=content,
        start_ms=start_ms,
        end_ms=end_ms,
        source_type=source_type,
        content_hash=content_hash,
        token_count=0,
    )


def _mock_db(hit_chunks: list[Chunk], neighbor_chunks: list[Chunk]) -> AsyncMock:
    """构造一个 mock AsyncSession，按调用顺序返回两级查询结果。

    第1次 execute → 返回 hit_chunks（查 chunk_index 用）
    第2次 execute → 返回 neighbor_chunks（查邻居用）
    """
    def _make_result(chunks: list[Chunk]) -> MagicMock:
        scalars_mock = MagicMock()
        scalars_mock.all = MagicMock(return_value=chunks)
        result_mock = MagicMock()
        result_mock.scalars = MagicMock(return_value=scalars_mock)
        return result_mock

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[_make_result(hit_chunks), _make_result(neighbor_chunks)]
    )
    return db


# ───────────────────────────── 测试用例 ─────────────────────────────


@pytest.mark.asyncio
async def test_expand_noop_when_empty_hits():
    """空输入 → 空输出，不调用任何 DB 查询。"""
    media_id = uuid.uuid4()
    db = AsyncMock()

    expander = ContextExpander()
    expanded = await expander.expand(db, media_id, [])

    assert expanded == []
    db.execute.assert_not_called()


@pytest.mark.asyncio
async def test_expand_single_hit_with_neighbors():
    """中间位置的命中 → 扩展出前后两个邻居，共 3 条结果。"""
    media_id = uuid.uuid4()
    chunk_ids = [uuid.uuid4() for _ in range(5)]

    # chunk_index=2 的命中，应扩展出 chunk_index=1 和 3
    hit = VectorHit(
        chunk_id=str(chunk_ids[2]),
        score=0.9,
        content="chunk 2",
        start_ms=60000,
        end_ms=65000,
        source_type="asr",
        content_hash="h2",
    )

    hit_chunks = [_make_chunk(chunk_ids[2], media_id, 2, "chunk 2", 60000, 65000, "asr", "h2")]
    neighbor_chunks = [
        _make_chunk(chunk_ids[1], media_id, 1, "chunk 1", 55000, 60000, "asr", "h1"),
        _make_chunk(chunk_ids[3], media_id, 3, "chunk 3", 65000, 70000, "asr", "h3"),
    ]

    db = _mock_db(hit_chunks, neighbor_chunks)
    expander = ContextExpander()
    expanded = await expander.expand(db, media_id, [hit])

    assert len(expanded) == 3
    expanded_ids = {h.chunk_id for h in expanded}
    assert str(chunk_ids[1]) in expanded_ids
    assert str(chunk_ids[2]) in expanded_ids
    assert str(chunk_ids[3]) in expanded_ids

    # 原始命中保持前排，邻居插入
    assert expanded[0].chunk_id == str(chunk_ids[2])


@pytest.mark.asyncio
async def test_expand_no_left_neighbor():
    """chunk_index=0 的命中 → 无左侧邻居，仅扩展右侧。"""
    media_id = uuid.uuid4()
    chunk_ids = [uuid.uuid4() for _ in range(3)]

    hit = VectorHit(
        chunk_id=str(chunk_ids[0]),
        score=0.95,
        content="chunk 0 start",
        start_ms=0,
        end_ms=5000,
        source_type="asr",
        content_hash="h0",
    )

    hit_chunks = [_make_chunk(chunk_ids[0], media_id, 0, "chunk 0", 0, 5000, "asr", "h0")]
    neighbor_chunks = [
        _make_chunk(chunk_ids[1], media_id, 1, "chunk 1", 5000, 10000, "asr", "h1"),
    ]

    db = _mock_db(hit_chunks, neighbor_chunks)
    expander = ContextExpander()
    expanded = await expander.expand(db, media_id, [hit])

    assert len(expanded) == 2
    expanded_ids = {h.chunk_id for h in expanded}
    assert str(chunk_ids[0]) in expanded_ids
    assert str(chunk_ids[1]) in expanded_ids
    # 确保没有负索引
    assert str(chunk_ids[2]) not in expanded_ids


@pytest.mark.asyncio
async def test_expand_deduplication():
    """两个相邻命中共享邻居 → 去重，不重复添加。"""
    media_id = uuid.uuid4()
    chunk_ids = [uuid.uuid4() for _ in range(4)]

    # chunk_index=1 和 chunk_index=2 的命中，共同邻居 chunk_index=0 和 3
    hits = [
        VectorHit(chunk_id=str(chunk_ids[1]), score=0.9, content="c1",
                  start_ms=5000, end_ms=10000, source_type="asr", content_hash="h1"),
        VectorHit(chunk_id=str(chunk_ids[2]), score=0.85, content="c2",
                  start_ms=10000, end_ms=15000, source_type="asr", content_hash="h2"),
    ]

    hit_chunks = [
        _make_chunk(chunk_ids[1], media_id, 1, "c1", 5000, 10000, "asr", "h1"),
        _make_chunk(chunk_ids[2], media_id, 2, "c2", 10000, 15000, "asr", "h2"),
    ]
    # 邻居：chunk_index=0 (来自 chunk_index=1 的左邻), chunk_index=3 (来自 chunk_index=2 的右邻)
    # 注意 chunk_index=1 和 2 本身不应重复出现（即使 chunk_index=1 是 chunk_index=2 的左邻）
    neighbor_chunks_pg = [
        _make_chunk(chunk_ids[0], media_id, 0, "c0", 0, 5000, "asr", "h0"),
        _make_chunk(chunk_ids[3], media_id, 3, "c3", 15000, 20000, "asr", "h3"),
    ]

    db = _mock_db(hit_chunks, neighbor_chunks_pg)
    expander = ContextExpander()
    expanded = await expander.expand(db, media_id, hits)

    assert len(expanded) == 4  # 原始2命中 + 2唯一邻居
    expanded_ids = {h.chunk_id for h in expanded}
    assert str(chunk_ids[0]) in expanded_ids
    assert str(chunk_ids[1]) in expanded_ids
    assert str(chunk_ids[2]) in expanded_ids
    assert str(chunk_ids[3]) in expanded_ids


@pytest.mark.asyncio
async def test_expand_preserves_original_order():
    """扩展后原始 hit 保持在结果列表前部，邻居追加在末尾。"""
    media_id = uuid.uuid4()
    chunk_ids = [uuid.uuid4() for _ in range(3)]

    hit = VectorHit(chunk_id=str(chunk_ids[1]), score=0.9,
                    content="original", start_ms=5000, end_ms=10000,
                    source_type="asr", content_hash="h1")

    hit_chunks = [_make_chunk(chunk_ids[1], media_id, 1, "original", 5000, 10000, "asr", "h1")]
    neighbor_chunks = [
        _make_chunk(chunk_ids[0], media_id, 0, "left", 0, 5000, "asr", "h0"),
        _make_chunk(chunk_ids[2], media_id, 2, "right", 10000, 15000, "asr", "h2"),
    ]

    db = _mock_db(hit_chunks, neighbor_chunks)
    expander = ContextExpander()
    expanded = await expander.expand(db, media_id, [hit])

    assert len(expanded) == 3
    # 原始命中的 chunk 在前
    assert expanded[0].chunk_id == str(chunk_ids[1])
    assert expanded[0].score == 0.9
    # 邻居追加在后（按 score=0.0）
    neighbor_ids = {h.chunk_id for h in expanded[1:]}
    assert str(chunk_ids[0]) in neighbor_ids
    assert str(chunk_ids[2]) in neighbor_ids