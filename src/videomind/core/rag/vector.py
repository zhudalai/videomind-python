"""向量检索通道 —— encode query -> Qdrant 向量近邻检索。

对应 docs/RAG-RETRIEVAL.md 向量检索通道设计。
设计要点：
1. VectorRetriever 封装 Embedding + Qdrant 检索全链路：
   embed query -> 得到向量 -> Qdrant.search(query_vector) -> 组装 VectorHit 列表。
2. 按 media_id 过滤，避免跨视频污染。
3. 支持 top_k 与 score_threshold 参数。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from videomind.core.video_pipeline.embed import get_embedding_backend
from videomind.infrastructure.vector.qdrant import get_qdrant


@dataclass
class VectorHit:
    """向量检索命中。"""

    chunk_id: str
    score: float
    content: str
    start_ms: int | None = None
    end_ms: int | None = None
    source_type: str = ""
    content_hash: str = ""


class VectorRetriever:
    """向量检索通道：encode query → Qdrant 近邻搜索 → VectorHit 列表。"""

    def __init__(self) -> None:
        """注入 embed 后端与 Qdrant 客户端。"""
        self._embedder = get_embedding_backend()
        self._qdrant = get_qdrant()

    async def retrieve(
        self,
        query: str,
        media_id: uuid.UUID,
        *,
        top_k: int = 10,
        score_threshold: float | None = None,
    ) -> list[VectorHit]:
        """检索与 query 最相关的 chunk（向量语义匹配）。

        Args:
            query: 查询文本。
            media_id: 目标视频 ID，用于过滤跨视频污染。
            top_k: 最大返回条数，默认 10。
            score_threshold: 最低余弦相似度阈值，低于此值不返回。

        Returns:
            VectorHit 列表，按相似度降序排列。空结果时返回 []。
        """
        # 1. 编码 query 为 embedding vector
        embed_result = await self._embedder.embed([query])
        query_vector = embed_result.vectors[0]

        # 2. Qdrant 近邻检索
        hits = await self._qdrant.search(
            query_vector,
            limit=top_k,
            filters={"media_id": str(media_id)},
            score_threshold=score_threshold,
        )

        # 3. 组装 VectorHit 列表
        results: list[VectorHit] = []
        for h in hits:
            payload = h["payload"]
            results.append(
                VectorHit(
                    chunk_id=h["id"],
                    score=h["score"],
                    content=payload.get("content", ""),
                    start_ms=payload.get("start_ms"),
                    end_ms=payload.get("end_ms"),
                    source_type=payload.get("source_type", ""),
                    content_hash=payload.get("content_hash", ""),
                )
            )
        return results


__all__ = ["VectorHit", "VectorRetriever"]