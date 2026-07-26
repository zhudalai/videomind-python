"""确定性重排器 DeterministicReranker —— 三阶段线性加权重排序。

对应 docs/RAG-RETRIEVAL.md 的重排序设计。

设计要点：
1. 三阶段线性加权：position (0.3) + source (0.2) + original (0.5)
2. 位次衰减：列表靠前的 hit 获得更高位次分，鼓励原通道的排序信号
3. 来源偏好：asr(0.3) > mixed(0.25) > ocr(0.2) > 其他(0.15)
4. 原始分 clamp 到 [0, 1]，适配 RRF 分等非 [0,1] 范围的分数
5. 原地修改 score 字段后按新得分降序排序返回
"""

from __future__ import annotations

from videomind.core.rag.vector import VectorHit


class DeterministicReranker:
    """确定性重排器，三阶段线性加权。

    权重（总和 1.0）：
        position_weight = 0.3   — 位次衰减：越靠前的 hit 加权重
        source_weight   = 0.2   — 来源类型偏好：asr > mixed > ocr
        original_weight = 0.5   — RRF/原通道的原始得分保留

    source_type 映射表：
        asr   -> 0.3
        ocr   -> 0.2
        mixed -> 0.25
        其他   -> 0.15
    """

    SOURCE_BONUS: dict[str, float] = {"asr": 0.3, "ocr": 0.2, "mixed": 0.25}

    def rerank(self, hits: list[VectorHit]) -> list[VectorHit]:
        """返回重排序后（按新得分降序）的 hit 列表，原地修改 score 字段。

        Args:
            hits: 待重排序的 VectorHit 列表。每个 hit 的 score 会被原地更新。

        Returns:
            同一列表对象，已按新得分降序排列。空列表时返回 []。
        """
        n = len(hits)
        if n == 0:
            return []

        for i, hit in enumerate(hits):
            # 位次分数: 1 - i/n (range 0..~1)
            position_score = 1.0 - (i / n) if n > 1 else 1.0

            # 来源分数
            source_score = self.SOURCE_BONUS.get(hit.source_type, 0.15)

            # 原始得分归一化到 [0, 1]
            original_score = max(0.0, min(1.0, hit.score))

            hit.score = (
                0.3 * position_score +
                0.2 * source_score +
                0.5 * original_score
            )

        hits.sort(key=lambda h: h.score, reverse=True)
        return hits


__all__ = ["DeterministicReranker"]