"""Qdrant 异步客户端 —— 向量检索库。

对应 docs/RAG-RETRIEVAL.md + docs/DATA-MODEL.md chunk 表 qdrant_point_id。
设计要点：
1. **collection schema** 与 chunk 表对齐：payload 含 media_id / segment_id /
   chunk_index / start_ms / end_ms / source_type / content_hash。
2. point_id 用 UUID v5（确定性，与 DB 双写校验）。
3. HNSW + Cosine 距离（BGE-M3 已归一化，余弦即点积）。
"""

from __future__ import annotations

import uuid
from functools import lru_cache
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qm

from videomind.config import get_settings

# chunk 表 content_hash 作 namespace，chunk_index 作 name → 稳定 UUID v5
# 使用 UUID v5 标准命名空间（RFC 4122 URL namespace），16 字节固定
_QDRANT_UUID_NAMESPACE = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")  # RFC4122 URL namespace


def make_qdrant_point_id(media_id: uuid.UUID, chunk_index: int) -> uuid.UUID:
    """生成确定性 Qdrant point ID（UUID v5）。

    与 DB chunk.qdrant_point_id 双写校验：相同 (media_id, chunk_index) 总得同 ID。
    """
    name = f"{media_id}:{chunk_index}"
    return uuid.uuid5(_QDRANT_UUID_NAMESPACE, name)


@lru_cache
def get_qdrant() -> "QdrantStore":
    return QdrantStore()


class QdrantStore:
    """Qdrant 向量库封装：collection 初始化 + upsert / search / delete。"""

    def __init__(self) -> None:
        s = get_settings()
        self._client = AsyncQdrantClient(url=s.qdrant_url)
        self._collection = s.qdrant_collection
        self._dim = s.embedding_dim

    async def ensure_collection(self) -> None:
        """启动时确保 collection 存在（幂等）。"""
        collections = await self._client.get_collections()
        names = {c.name for c in collections.collections}
        if self._collection in names:
            return
        await self._client.create_collection(
            collection_name=self._collection,
            vectors_config=qm.VectorParams(
                size=self._dim,
                distance=qm.Distance.COSINE,
            ),
        )

    async def upsert_chunk(
        self,
        *,
        point_id: uuid.UUID,
        vector: list[float],
        payload: dict[str, Any],
    ) -> None:
        """写入单个 chunk 向量。payload 与 chunk 表对齐。"""
        await self._client.upsert(
            collection_name=self._collection,
            points=[
                qm.PointStruct(
                    id=str(point_id),
                    vector=vector,
                    payload=payload,
                )
            ],
        )

    async def upsert_batch(
        self, points: list[dict[str, Any]]
    ) -> None:
        """批量写入。points = [{"point_id":..., "vector":[...], "payload":{}}]"""
        batch = [
            qm.PointStruct(
                id=str(p["point_id"]),
                vector=p["vector"],
                payload=p["payload"],
            )
            for p in points
        ]
        await self._client.upsert(collection_name=self._collection, points=batch)

    async def search(
        self,
        query_vector: list[float],
        *,
        limit: int = 20,
        filters: dict[str, Any] | None = None,
        score_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """向量近邻检索。返回 [{id, score, payload}]"""
        flt = _build_filter(filters) if filters else None
        res = await self._client.query_points(
            collection_name=self._collection,
            query=query_vector,
            limit=limit,
            query_filter=flt,
            score_threshold=score_threshold,
        )
        return [
            {
                "id": p.id,
                "score": p.score,
                "payload": p.payload,
            }
            for p in res.points
        ]

    async def delete_by_media(self, media_id: uuid.UUID) -> None:
        """删除某视频的所有 chunk 向量。"""
        await self._client.delete(
            collection_name=self._collection,
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

    async def count(self, media_id: uuid.UUID | None = None) -> int:
        """统计向量数（可按 media_id 过滤）。"""
        flt = (
            qm.Filter(
                must=[
                    qm.FieldCondition(
                        key="media_id", match=qm.MatchValue(value=str(media_id))
                    )
                ]
            )
            if media_id
            else None
        )
        r = await self._client.count(
            collection_name=self._collection, count_filter=flt, exact=True
        )
        return r.count

    async def close(self) -> None:
        await self._client.close()


def _build_filter(filters: dict[str, Any]) -> qm.Filter:
    """把简单 {'k': v} dict 转成 Qdrant Filter（全 must match）。"""
    return qm.Filter(
        must=[
            qm.FieldCondition(key=k, match=qm.MatchValue(value=v))
            for k, v in filters.items()
        ]
    )


def build_chunk_payload(
    *,
    chunk_id: uuid.UUID,
    media_id: uuid.UUID,
    segment_id: uuid.UUID | None,
    chunk_index: int,
    start_ms: int | None,
    end_ms: int | None,
    source_type: str,
    content_hash: str,
    content: str,
) -> dict[str, Any]:
    """构造与 chunk 表对齐的 Qdrant payload。"""
    return {
        "chunk_id": str(chunk_id),
        "media_id": str(media_id),
        "segment_id": str(segment_id) if segment_id else None,
        "chunk_index": chunk_index,
        "start_ms": start_ms,
        "end_ms": end_ms,
        "source_type": source_type,
        "content_hash": content_hash,
        "content": content,
    }
