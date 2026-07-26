"""上下文扩展 —— 对检索命中做 ±1 chunk_index 邻居扩展。

避免截断语义上下文：如果检索命中 chunk_index=5，则同时拉取 chunk_index=4 和 6，
确保问答时能获取完整的上下文信息。
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from videomind.infrastructure.storage.models import Chunk
from videomind.core.rag.vector import VectorHit


class ContextExpander:
    """对检索命中做 ±1 chunk_index 邻居扩展，避免截断语义上下文。"""

    async def expand(
        self,
        db: AsyncSession,
        media_id: uuid.UUID,
        hits: list[VectorHit],
    ) -> list[VectorHit]:
        """扩展检索命中，添加上下文邻居 chunk。

        Args:
            db: 异步数据库会话（mock）。
            media_id: 目标视频 ID，用于限定查询范围。
            hits: 向量检索命中列表（VectorHit.chunk_id 即 Chunk.id 的 str 形式）。

        Returns:
            原始命中列表 + 邻居命中列表（原始命中在前，邻居追加以 score=0.0 附加）。
        """
        if not hits:
            return []

        # 1. 将 chunk_id 字符串转回 UUID
        hit_uuids = [uuid.UUID(h.chunk_id) for h in hits]

        # 2. 一次性查询所有命中 chunk 的 chunk_index
        result = await db.execute(
            select(Chunk).where(Chunk.id.in_(hit_uuids))
        )
        hit_chunks: list[Chunk] = list(result.scalars().all())

        # 3. 收集命中 chunk_index 和已见 ID 集合
        seen_ids: set[str] = {h.chunk_id for h in hits}
        chunk_indices: set[int] = {c.chunk_index for c in hit_chunks}

        # 4. 计算邻居索引：每个命中索引 ±1（下限为 0），排除已有索引
        neighbor_indices: set[int] = set()
        for idx in chunk_indices:
            if idx > 0:
                neighbor_indices.add(idx - 1)
            neighbor_indices.add(idx + 1)

        # 排除已经有的 chunk_index（命中的那些）
        neighbor_indices -= chunk_indices

        if not neighbor_indices:
            return list(hits)

        # 5. 查询邻居 chunk（同一 media_id，未出现在结果中）
        result = await db.execute(
            select(Chunk)
            .where(
                Chunk.media_id == media_id,
                Chunk.chunk_index.in_(list(neighbor_indices)),
                Chunk.id.notin_(hit_uuids),
            )
        )
        neighbor_chunks: list[Chunk] = list(result.scalars().all())

        # 6. 将邻居转为 VectorHit，合并到结果（去重）
        neighbor_hits: list[VectorHit] = []
        for nc in neighbor_chunks:
            ncid = str(nc.id)
            if ncid not in seen_ids:
                seen_ids.add(ncid)
                neighbor_hits.append(
                    VectorHit(
                        chunk_id=ncid,
                        score=0.0,
                        content=nc.content or "",
                        start_ms=nc.start_ms,
                        end_ms=nc.end_ms,
                        source_type=nc.source_type or "",
                        content_hash=nc.content_hash or "",
                    )
                )

        return list(hits) + neighbor_hits