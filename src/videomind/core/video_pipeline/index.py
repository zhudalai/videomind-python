"""索引器 —— 将 ASR/OCR 文本分块 → Embedding → Qdrant + chunk 表双写。

对应 docs/RAG-RETRIEVAL.md Indexer 章节 + docs/DATA-MODEL.md chunk 表。
设计要点：
1. 输入：transcription（全量+分片）+ frame_ocr 结果
2. 合并策略：优先 ASR 文本，OCR 文本作为补充（source_type=mixed）
3. 分块：滑动窗口（chunk_size=800, overlap=120），生成 chunk_index 递增
4. 向量化：EmbeddingBackend.embed() 批处理
5. 双写事务：
   - Qdrant upsert（point_id = UUID v5，确定性）
   - PostgreSQL chunk 表 insert（content_hash 作稳定引用锚点）
6. media_file.status 最终置 ready。
"""

from __future__ import annotations

import datetime
import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from videomind.core.video_pipeline.embed import chunk_text, get_embedding_backend
from videomind.infrastructure.storage import models as m
from videomind.infrastructure.vector.qdrant import (
    build_chunk_payload,
    get_qdrant,
    make_qdrant_point_id,
)


@dataclass
class IndexResult:
    """索引阶段产出。"""

    chunk_count: int
    qdrant_count: int
    first_chunk_id: uuid.UUID | None = None


class Indexer:
    """索引器：ASR/OCR → chunk → embedding → Qdrant + PostgreSQL。"""

    # 分块参数对应 docs/DATA-MODEL.md knowledge_base 默认
    CHUNK_SIZE = 800
    CHUNK_OVERLAP = 120

    def __init__(self) -> None:
        self._embedder = get_embedding_backend()
        self._qdrant = get_qdrant()

    async def index(
        self,
        db: AsyncSession,
        media_id: uuid.UUID,
        transcription: m.Transcription | None,
        chunks: list[m.TranscriptionChunk],
        ocr_results: list[m.FrameOCR] | None = None,
    ) -> IndexResult:
        """执行索引。

        Args:
            db: DB session
            media_id: 视频 ID
            transcription: 全量转写记录（含 full_text）
            chunks: transcription_chunk 列表（按 chunk_index 排序）
            ocr_results: frame_ocr 列表（可选）

        Returns:
            IndexResult
        """
        # 0. 幂等清理：先删掉该 media_id 下旧 chunk 行 + Qdrant 旧 points。
        # chunk.id 是 uuid5(media_id + chunk_index) 确定性映射，重跑 = 同一索引快照重生成，
        # 必须清旧再插入，否则 IntegrityError(UniqueViolation chunk.pkey)。
        await db.execute(delete(m.Chunk).where(m.Chunk.media_id == media_id))
        await db.flush()
        # 同步失效该 media 的 HybridRetriever 进程缓存（rag.pipeline 按 media_id 单例缓存
        # BM25 索引），否则重处理后的新 chunk 不会进检索，老 chunk 已删会导致缓存陈旧。
        from videomind.core.rag.pipeline import invalidate_retriever_cache
        invalidate_retriever_cache(media_id)
        # 同步失效该 media 的 RAG 语义缓存条目（1.3）：chunks 变化后旧答案不再可信，
        # 不等 TTL 立即失效；invalidate 内部 fail-open，失败不阻断索引
        from videomind.core.rag.semantic_cache import invalidate_semantic_cache_for_media
        await invalidate_semantic_cache_for_media(media_id)
        # 同步清掉 Qdrant 旧 points（按 payload.media_id 过滤），保持双写一致
        try:
            from qdrant_client.http import models as qm
            await self._qdrant._client.delete(
                collection_name=self._qdrant._collection,
                points_selector=qm.FilterSelector(
                    filter=qm.Filter(
                        must=[
                            qm.FieldCondition(
                                key="media_id",
                                match=qm.MatchValue(value=str(media_id)),
                            )
                        ]
                    )
                ),
            )
        except Exception:
            # Qdrant 通常允许不存在的 point 清理失败；忽略首次跑时的"collection 不存在"
            pass

        # 1. 准备待分块文本段（ASR chunk text + OCR text）
        text_segments: list[tuple[str, str, int, int, str]] = []
        # (content, source_type, start_ms, end_ms, segment_id)
        # 这里先用简单拼接策略：ASR 为主，OCR 追加

        # 按时间顺序合并 ASR chunks
        for c in sorted(chunks, key=lambda x: x.chunk_index):
            if c.text and c.text.strip():
                text_segments.append((
                    c.text.strip(),
                    "asr",
                    c.start_ms,
                    c.end_ms,
                    "",  # segment_id 占位，后续可关联 video_segment
                ))

        # OCR 文本作为补充段
        if ocr_results:
            for ocr in ocr_results:
                if ocr.ocr_text and ocr.ocr_text.strip():
                    text_segments.append((
                        ocr.ocr_text.strip(),
                        "ocr",
                        ocr.frame_ms,
                        ocr.frame_ms + 1000,
                        "",
                    ))

        if not text_segments:
            # 无可索引文本（如静音/无字幕/无 OCR 视频直链）也要正常终结：
            # 把 media_file.status 置 ready 并补 completed_at，否则会永远停在
            # downloaded/transcoded 阶段让用户误以为管线卡死。
            media = await db.execute(select(m.MediaFile).where(m.MediaFile.id == media_id))
            media_orm = media.scalar_one_or_none()
            if media_orm:
                media_orm.status = "ready"
                media_orm.completed_at = datetime.datetime.now(datetime.timezone.utc)
                await db.flush()
            return IndexResult(chunk_count=0, qdrant_count=0)

        # 2. 滑动窗口分块（逐段分块，保持时间边界）
        all_chunks_data: list[dict[str, Any]] = []
        chunk_idx = 0

        for seg_text, source, seg_start, seg_end, _ in text_segments:
            pieces = chunk_text(
                seg_text,
                chunk_size=self.CHUNK_SIZE,
                chunk_overlap=self.CHUNK_OVERLAP,
            )
            for piece_idx, p in enumerate(pieces):
                # 近似时间分配（按段内 piece 序号均分时段）。必须用段内序号：
                # 旧实现用全局 chunk_idx，多段时第 N 段的 start 会漂出段界
                # （start ≥ seg_end），RAG 引用时间戳指向错误视频位置。
                duration = seg_end - seg_start
                p_start = seg_start + int(duration * piece_idx / max(1, len(pieces)))
                p_end = p_start + max(1000, int(duration / max(1, len(pieces))))
                all_chunks_data.append({
                    "content": p,
                    "source_type": source,
                    "start_ms": p_start,
                    "end_ms": p_end,
                    "chunk_index": chunk_idx,
                })
                chunk_idx += 1

        # 3. 批量向量化
        texts = [c["content"] for c in all_chunks_data]
        embed_result = await self._embedder.embed(texts)
        vectors = embed_result.vectors

        # 4. 双写：Qdrant + PostgreSQL chunk 表
        await self._qdrant.ensure_collection()

        qdrant_points: list[dict[str, Any]] = []
        chunk_orms: list[m.Chunk] = []

        for c, vec in zip(all_chunks_data, vectors):
            # 生成确定性 point_id & content_hash
            content_hash = hashlib.md5(c["content"].encode()).hexdigest()  # 32 hex
            point_id = make_qdrant_point_id(media_id, c["chunk_index"])

            # Qdrant payload
            payload = build_chunk_payload(
                chunk_id=point_id,
                media_id=media_id,
                segment_id=None,
                chunk_index=c["chunk_index"],
                start_ms=c["start_ms"],
                end_ms=c["end_ms"],
                source_type=c["source_type"],
                content_hash=content_hash,
                content=c["content"],
            )
            qdrant_points.append({
                "point_id": point_id,
                "vector": vec,
                "payload": payload,
            })

            # PostgreSQL Chunk ORM
            chunk_orms.append(m.Chunk(
                id=point_id,  # 与 Qdrant 同 UUID
                media_id=media_id,
                segment_id=None,
                chunk_index=c["chunk_index"],
                content=c["content"],
                content_hash=content_hash,
                token_count=len(c["content"]),  # 粗估
                start_ms=c["start_ms"],
                end_ms=c["end_ms"],
                source_type=c["source_type"],
                qdrant_point_id=point_id,
                manifest_sha256="",  # 后续补全 ChunkManifest SHA256
            ))

        # 批量写入
        await self._qdrant.upsert_batch(qdrant_points)

        for orm in chunk_orms:
            db.add(orm)
        await db.flush()

        # 5. 更新 media_file.status = ready
        media = await db.execute(select(m.MediaFile).where(m.MediaFile.id == media_id))
        media_orm = media.scalar_one_or_none()
        if media_orm:
            media_orm.status = "ready"
            media_orm.completed_at = datetime.datetime.now(datetime.timezone.utc)
            await db.flush()

        return IndexResult(
            chunk_count=len(chunk_orms),
            qdrant_count=len(qdrant_points),
            first_chunk_id=chunk_orms[0].id if chunk_orms else None,
        )


_indexer: Indexer | None = None


def get_indexer() -> Indexer:
    global _indexer
    if _indexer is None:
        _indexer = Indexer()
    return _indexer