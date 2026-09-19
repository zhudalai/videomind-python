"""Indexer 单元测试 —— 幂等清理、双写编排、时间戳分配、空文本兜底。

守护契约（对应 core/video_pipeline/index.py）：
- 幂等：重跑先删旧 chunk 行 + Qdrant 旧 points + 失效检索/语义缓存（确定性 uuid5 主键）。
- 双写一致：Qdrant point 与 chunk 表行同 UUID（point_id == chunk.id == qdrant_point_id）。
- 时间戳分配：chunk 的 start/end 必须落在其来源段的 [start_ms, end_ms] 内
  （近似按字符比例，绝不漂出段界——漂出去会让 RAG 引用的时间戳指错视频位置）。
- 空文本兜底：无 ASR/OCR 文本也正常终结（media 置 ready），不卡在中间态。
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from videomind.core.video_pipeline.index import Indexer

# ──────────────────────────── 假依赖 ────────────────────────────


class FakeEmbedder:
    def __init__(self):
        self.calls: list[list[str]] = []

    async def embed(self, texts):
        self.calls.append(list(texts))
        return SimpleNamespace(
            vectors=[[float(i)] for i, _ in enumerate(texts)],
            model_name="fake", dim=1,
        )


class FakeQdrant:
    def __init__(self):
        self._collection = "test_coll"
        self._client = SimpleNamespace(delete=AsyncMock())
        self.ensured = 0
        self.batches: list[list[dict]] = []

    async def ensure_collection(self):
        self.ensured += 1

    async def upsert_batch(self, points):
        self.batches.append(list(points))


class _FakeResult:
    def __init__(self, value=None):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    """delete/select 都走 execute；scalar_one_or_none 返回预置 media。"""

    def __init__(self, media):
        self._media = media
        self.added: list = []
        self.flush_count = 0

    async def execute(self, stmt):
        return _FakeResult(self._media)

    def add(self, orm):
        self.added.append(orm)

    async def flush(self):
        self.flush_count += 1


@pytest.fixture
def indexer_kit(monkeypatch):
    """装配 Indexer：假 embedder/qdrant + 屏蔽语义缓存失效（其自身有独立测试）。"""
    embedder = FakeEmbedder()
    qdrant = FakeQdrant()
    invalidate = AsyncMock(return_value=0)
    monkeypatch.setattr(
        "videomind.core.video_pipeline.index.get_embedding_backend", lambda: embedder
    )
    monkeypatch.setattr(
        "videomind.core.video_pipeline.index.get_qdrant", lambda: qdrant
    )
    monkeypatch.setattr(
        "videomind.core.rag.semantic_cache.invalidate_semantic_cache_for_media",
        invalidate,
    )
    return SimpleNamespace(
        indexer=Indexer(), embedder=embedder, qdrant=qdrant, invalidate=invalidate
    )


def _asr_chunk(text: str, idx: int, start_ms: int, end_ms: int):
    return SimpleNamespace(text=text, chunk_index=idx, start_ms=start_ms, end_ms=end_ms)


def _ocr(text: str, frame_ms: int):
    return SimpleNamespace(ocr_text=text, frame_ms=frame_ms)


def _media():
    return SimpleNamespace(status="transcoded", completed_at=None)


# ──────────────────────────── 测试 ────────────────────────────


async def test_index_full_flow_double_write(indexer_kit):
    """ASR+OCR 合并 → embed 一次批调用 → Qdrant/PG 双写同 UUID → media ready。"""
    kit = indexer_kit
    mid = uuid.uuid4()
    media = _media()
    session = FakeSession(media)

    result = await kit.indexer.index(
        session, mid,
        transcription=None,
        chunks=[_asr_chunk("第一段文本", 0, 0, 2000)],
        ocr_results=[_ocr("画面文字", 5000)],
    )

    # 产出计数
    assert result.chunk_count == 2 and result.qdrant_count == 2
    assert result.first_chunk_id is not None

    # embed 一次批量调用，ASR 在前 OCR 在后
    assert kit.embedder.calls == [["第一段文本", "画面文字"]]

    # Qdrant：ensure + 清旧 + upsert 一批 2 点
    assert kit.qdrant.ensured == 1
    kit.qdrant._client.delete.assert_awaited_once()
    assert len(kit.qdrant.batches) == 1 and len(kit.qdrant.batches[0]) == 2
    payloads = [p["payload"] for p in kit.qdrant.batches[0]]
    assert payloads[0]["source_type"] == "asr" and payloads[0]["chunk_index"] == 0
    assert payloads[1]["source_type"] == "ocr" and payloads[1]["chunk_index"] == 1
    assert all(p["media_id"] == str(mid) for p in payloads)

    # 双写同 UUID：chunk 行 id == qdrant_point_id == point_id
    from videomind.infrastructure.storage import models as m

    assert all(isinstance(c, m.Chunk) for c in session.added)
    for orm, point in zip(session.added, kit.qdrant.batches[0]):
        assert orm.id == point["point_id"] == orm.qdrant_point_id

    # media 终结 + 缓存失效被调用
    assert media.status == "ready" and media.completed_at is not None
    kit.invalidate.assert_awaited_once()


async def test_index_empty_segments_marks_ready_without_embedding(indexer_kit):
    """无 ASR/OCR 文本（静音视频）：正常终结 ready，embed/upsert 零调用。"""
    kit = indexer_kit
    media = _media()
    session = FakeSession(media)

    result = await kit.indexer.index(
        session, uuid.uuid4(),
        transcription=None,
        chunks=[_asr_chunk("   ", 0, 0, 1000)],  # 纯空白视为无文本
        ocr_results=[],
    )

    assert result.chunk_count == 0 and result.qdrant_count == 0
    assert media.status == "ready" and media.completed_at is not None
    assert kit.embedder.calls == []
    assert kit.qdrant.batches == []


async def test_index_timestamps_stay_within_source_segment(indexer_kit):
    """多段输入时 chunk 时间戳不得漂出各自段界（按段内 piece 序号近似分配）。

    段 B 的 piece 若用全局 chunk_idx 计算，start_ms 会超过段 end_ms，
    RAG 引用时间戳指向错误视频位置。
    """
    kit = indexer_kit
    session = FakeSession(_media())

    # 段 A：ASR [0, 4000)ms，长文本切多片；段 B：OCR [60000, 61000)ms，同样多片
    long_text_a = "甲说了一些内容。" * 200   # ~1600 字 > CHUNK_SIZE 800 → ≥2 pieces
    long_text_b = "画面上的文字乙。" * 200
    await kit.indexer.index(
        session, uuid.uuid4(),
        transcription=None,
        chunks=[_asr_chunk(long_text_a, 0, 0, 4000)],
        ocr_results=[_ocr(long_text_b, 60000)],
    )

    asr_chunks = [c for c in session.added if c.source_type == "asr"]
    ocr_chunks = [c for c in session.added if c.source_type == "ocr"]
    assert len(asr_chunks) >= 2 and len(ocr_chunks) >= 2

    for c in asr_chunks:
        assert 0 <= c.start_ms < 4000, f"ASR chunk 漂出段界: {c.start_ms}"
        assert c.end_ms > c.start_ms
    for c in ocr_chunks:
        assert 60000 <= c.start_ms < 61000, f"OCR chunk 漂出段界: {c.start_ms}"
        assert c.end_ms > c.start_ms
