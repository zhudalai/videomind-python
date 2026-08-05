"""本地 BGE cross-encoder reranker 后端（sentence-transformers）。

模型 BAAI/bge-reranker-v2-m3：与现有 BGE-M3 embedder 同源、多语（CJK 强）、CPU 可跑、
零 API 成本、离线可用。cross-encoder 对 (query, doc) 逐对打分，相关性信号远强于固定权重。

延迟加载：CrossEncoder 在构造期即下载/加载模型（重），由 rerank_backend._try_local_backend
包裹并容错降级到 DeterministicRerankerAdapter，避免拖垮应用启动。

BGE-reranker-v2-m3 推荐用法：CrossEncoder(model).predict([(q, d), ...]) 返回相关性分数，
本后端按其降序重组 hits 并回填 score（raw score 直接用于跨文档比较，不做 sigmoid 归一）。
"""

from __future__ import annotations

import logging
from typing import Any

from videomind.core.rag.vector import VectorHit

log = logging.getLogger(__name__)


class LocalBGERerankBackend:
    """本地 sentence-transformers cross-encoder 重排后端。

    Args:
        model_name: HuggingFace 模型名，默认 BAAI/bge-reranker-v2-m3。
        device: torch 设备（auto/cuda/cpu），与 embedding 后端对齐。
        max_length: (query+doc) 截断长度，BGE-v2-m3 原生 8192，默认留模型默认。

    构造期加载模型（网络下载可能慢/失败），由外层 _try_local_backend 容错降级。
    """

    def __init__(
        self,
        *,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        device: str = "cpu",
        max_length: int | None = None,
    ) -> None:
        from sentence_transformers import CrossEncoder  # 延迟重 import

        kw: dict[str, Any] = {}
        if max_length is not None:
            kw["max_length"] = max_length
        self._model = CrossEncoder(model_name, device=device, **kw)
        self._model_name = model_name

    async def rerank(self, query: str, hits: list[VectorHit]) -> list[VectorHit]:
        """对 hits 用 query-文档 cross-encoder 打分，按分数降序返回新列表。

        不原地改输入：构造新 VectorHit。空输入返回 []。声明 async 适配统一 Reranker 协议
        （predict 是同步 CPU/GPU 计算，放 async 不会真并发受益，但保证调用点 await 一致）。
        """
        if not hits:
            return []

        pairs = [(query, h.content) for h in hits]
        scores = self._model.predict(pairs)  # ndarray shape (len,)

        # 按分数降序重排原 hits，回填 score
        order = sorted(range(len(hits)), key=lambda i: float(scores[i]), reverse=True)
        return [
            VectorHit(
                chunk_id=hits[i].chunk_id,
                score=float(scores[i]),
                content=hits[i].content,
                start_ms=hits[i].start_ms,
                end_ms=hits[i].end_ms,
                source_type=hits[i].source_type,
                content_hash=hits[i].content_hash,
            )
            for i in order
        ]


__all__ = ["LocalBGERerankBackend"]
