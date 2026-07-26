"""RRF（Reciprocal Rank Fusion）多通道检索融合算法。

公式: RRF_score(chunk_id) = Σ 1 / (k + rank + 1)，其中 k=60，rank 为 0-based。
"""
import uuid

K_DEFAULT = 60


def rrf_fuse(
    rankings: list[list[tuple[uuid.UUID, float]]],
    k: int = K_DEFAULT,
) -> list[tuple[uuid.UUID, float]]:
    """RRF 融合多通道检索排名。

    每个通道的排名列表按原始得分降序排列。
    对不同通道中相同 chunk_id 的 RRF 分数进行累加，
    最终按融合后的总分降序返回。

    Args:
        rankings: 多通道排名列表，每个元素为 [(chunk_id, original_score), ...]
                  已按得分降序排列，original_score 不参与计算
        k: RRF 公式中的平滑参数，默认 60

    Returns:
        按 RRF 融合分数降序排列的 [(chunk_id, rrf_score), ...]
    """
    if not rankings:
        return []

    score_map: dict[uuid.UUID, float] = {}

    for channel in rankings:
        for rank_idx, (chunk_id, _original_score) in enumerate(channel):
            rrf_score = 1.0 / (k + rank_idx + 1)
            score_map[chunk_id] = score_map.get(chunk_id, 0.0) + rrf_score

    # 按融合分数降序排列
    fused = sorted(score_map.items(), key=lambda item: item[1], reverse=True)
    return fused