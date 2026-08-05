"""检索排序质量评估指标 —— recall@k / MRR / nDCG（纯数学函数，无 IO）。

用于 P2-2 RAG 检索效果评测：给定一条 query 的最终排序命中 chunk_id 序列与
人工标注的 gold chunk_id(s)，量化该查询的检索质量。三指标互补刻画不同侧面：

  - recall@k: 召回完备性 —— gold 是否进入前 k（"用户能看到前 k 条，答案在不在"）
  - MRR: 排序质量 —— 第一个 gold 的倒数排名（"用户要往下翻多少才看到答案"）
  - nDCG: 排序质量 + 位置折扣 —— 多 gold 时理想排序贴近度（"gold 是否尽量靠前"）

设计：
  - 纯函数，签名 ranked: list[str]、gold: list[str]、k: int → float
  - 二值相关性（命中=1），适合单 gold / 多 gold 无损检索评估
  - 边界：空列表 / gold 不在结果 / k 越界 / 重复 gold，返回有意义的 0（非 nan）
"""

from __future__ import annotations

import math

__all__ = ["recall_at_k", "mrr", "ndcg"]


def recall_at_k(ranked_chunk_ids: list[str], gold_chunk_ids: list[str], *, k: int) -> float:
    """recall@k = |gold ∩ top-k| / |gold|。

    Args:
        ranked_chunk_ids: 按相关性降序排列的 chunk_id 序列（检索系统输出）。
        gold_chunk_ids: 人工标注的正确 chunk_id（允许重复，自动去重）。
        k: cutoff，只看前 k 个。

    Returns:
        0.0 ~ 1.0；gold 为空（标注缺失）→ 0.0（非 nan）。
    """
    gold_set = set(gold_chunk_ids)
    if not gold_set or k <= 0:
        return 0.0
    topk = set(ranked_chunk_ids[:k])
    hits = len(gold_set & topk)
    return hits / len(gold_set)


def mrr(ranked_chunk_ids: list[str], gold_chunk_ids: list[str]) -> float:
    """MRR = 1 / rank_of_first_gold（最靠前 gold 的倒数排名）。

    多 gold 时取第一个命中的位置对应的倒数排名（rank 从 1 起）。

    Args:
        ranked_chunk_ids: 降序排列的 chunk_id 序列。
        gold_chunk_ids: 正确标注。

    Returns:
        0.0 ~ 1.0；无 gold 或 gold 不在结果 → 0.0。
    """
    gold_set = set(gold_chunk_ids)
    if not gold_set:
        return 0.0
    for i, cid in enumerate(ranked_chunk_ids):
        if cid in gold_set:
            return 1.0 / (i + 1)
    return 0.0


def ndcg(ranked_chunk_ids: list[str], gold_chunk_ids: list[str]) -> float:
    """nDCG = DCG / iDCG，二值相关性 reliability。

    DCG = sum_{i: ranked[i] in gold} 1 / log2(i + 2)   （i 从 0 起）
    iDCG = gold 全部排最前的理想 DCG = sum_{j=0}^{min(|gold|, n)-1} 1 / log2(j + 2)

    Args:
        ranked_chunk_ids: 降序排列的 chunk_id 序列。
        gold_chunk_ids: 正确标注（去重后取唯一数）。

    Returns:
        0.0 ~ 1.0；无 gold → 0.0；无结果 → 0.0。
    """
    gold_set = set(gold_chunk_ids)
    if not gold_set or not ranked_chunk_ids:
        return 0.0

    # DCG：实际命中位置折扣累加
    dcg = sum(
        1.0 / math.log2(i + 2)
        for i, cid in enumerate(ranked_chunk_ids)
        if cid in gold_set
    )

    # iDCG：理想情况 gold 全排最前。命中数 m = min(|gold|, len(ranked))
    n = min(len(gold_set), len(ranked_chunk_ids))
    idcg = sum(1.0 / math.log2(j + 2) for j in range(n))

    if idcg == 0:
        return 0.0
    return dcg / idcg
