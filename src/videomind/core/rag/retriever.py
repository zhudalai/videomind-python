"""混合检索编排器 —— 并行执行 Vector+BM25 双通道检索，RRF 融合排序。

HybridRetriever 在初始化时注入 chunks 列表构建 BM25 索引，
search() 时并行调用 VectorRetriever 和 InMemoryBM25，两条通道的结果
通过 RRF 算法融合，最终返回按融合得分降序排列的 VectorHit 列表。

设计要点：
1. 初始化时在 BM25 上 build(chunks)，搜索时并行调用两端。
2. BM25 只返回 (UUID, score)，需与 VectorHit 的 chunk_id 对齐映射。
3. 仅 BM25 命中但未在向量通道出现的 chunk，从传入的 chunks 回填 content。
4. VectorHit.score 由 RRF 融合分数取代，反映多通道综合相关性。
"""

from __future__ import annotations

import asyncio
import uuid
from concurrent.futures import ThreadPoolExecutor

from videomind.core.rag.bm25 import InMemoryBM25
from videomind.core.rag.rrf import rrf_fuse
from videomind.core.rag.vector import VectorHit, VectorRetriever

# 与 RRF 保持一致（rrf_fuse 内部默认值为 60）
_RRF_K = 60


class HybridRetriever:
    """向量 + BM25 混合检索编排器。

    初始化时构建 BM25 索引，search() 时并行调用两个检
    索通道，再通过 RRF 融合排名。
    """

    def __init__(self, media_id: uuid.UUID, chunks: list[object]) -> None:
        """初始化混合检索器。

        Args:
            media_id: 视频 media_id，用于向量检索过滤。
            chunks: Chunk-like 对象列表，必须有 .id (UUID) 和 .content (str)。
        """
        self._media_id = media_id
        self._vector = VectorRetriever()
        self._bm25 = InMemoryBM25()
        self._bm25.build(chunks)

        # chunks 的 ID→内容 查找表，用于 BM25-only hit 回填 content
        self._chunk_content: dict[str, str] = {
            str(c.id): c.content for c in chunks
        }

        # 已有 VectorHit 的缓存：UUID_str → VectorHit
        self._id_map: dict[str, VectorHit] = {}

    async def search(self, query: str, *, top_k: int = 20) -> list[VectorHit]:
        """并行执行 Vector + BM25 检索，RRF 融合后返回结果。

        Args:
            query: 查询文本。
            top_k: 每个通道的最大返回条数，默认 20。

        Returns:
            按 RRF 融合得分降序排列的 VectorHit 列表。
        """
        # 1. 并行调用两个检索引擎
        v_hits, bm_results = await asyncio.gather(
            self._vector.retrieve(query, self._media_id, top_k=top_k),
            asyncio.to_thread(self._bm25.search, query, top_k=top_k),
        )

        # 2. 构建 id_map（Vector 通道已生成 VectorHit）
        hit_by_uuid: dict[uuid.UUID, VectorHit] = {}
        for h in v_hits:
            uid = uuid.UUID(h.chunk_id)
            hit_by_uuid[uid] = h

        # 3. 为 BM25-only 命中补建 VectorHit（从 chunks 查 content）
        if isinstance(bm_results, list):
            for chunk_id, _score in bm_results:
                if chunk_id not in hit_by_uuid:
                    content = self._chunk_content.get(
                        str(chunk_id), ""
                    )
                    hit_by_uuid[chunk_id] = VectorHit(
                        chunk_id=str(chunk_id),
                        score=0.0,  # 占位，后续被 RRF 分数覆盖
                        content=content,
                    )

        # 4. 构建 RRF 通道排名
        #    向量通道排名：按 VectorHit.score 降序（即原始向量得分）
        v_sorted = sorted(v_hits, key=lambda h: h.score, reverse=True)
        v_ranking: list[tuple[uuid.UUID, float]] = [
            (uuid.UUID(h.chunk_id), h.score) for h in v_sorted
        ]

        # BM25 通道排名：按返回顺序（已按得分降序）
        if not isinstance(bm_results, list):
            bm_ranking: list[tuple[uuid.UUID, float]] = []
        else:
            bm_ranking = [(c_id, s) for c_id, s in bm_results]

        # 5. RRF 融合
        rankings = []
        if v_ranking:
            rankings.append(v_ranking)
        if bm_ranking:
            rankings.append(bm_ranking)

        fused = rrf_fuse(rankings)

        # 6. 用 RRF 得分覆写 VectorHit，返回最终结果
        results: list[VectorHit] = []
        for chunk_id, rrf_score in fused:
            if chunk_id in hit_by_uuid:
                hit = hit_by_uuid[chunk_id]
                hit.score = rrf_score
                results.append(hit)

        return results


__all__ = ["HybridRetriever"]