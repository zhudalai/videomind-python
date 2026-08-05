"""评估指标 recall@k / MRR / nDCG 的数学正确性单元测试（TDD）。

覆盖 videomind.core.rag.metrics 三个排序质量指标：
  - recall_at_k: gold 是否出现在 top-k 命中里（多 gold 取命中比例）
  - mrr: 第一个命中 gold 的倒数排名（1/rank）
  - ndcg: DCG / iDCG，位置折扣 1/log2(rank+1)，相关性二值（命中=1）

设计原则：
  - 纯数学函数，无 IO / 无 DB / 无 async —— 测数学正确性，不测管线
  - 输入是 ranked_chunk_ids: list[str] 与 gold_chunk_ids: list[str]（多 gold 支持）
  - 单 gold 退化为特例
  - 边界：空列表、gold 不在结果里、k 超过列表长度
"""

from __future__ import annotations

import math

import pytest

from videomind.core.rag.metrics import mrr, ndcg, recall_at_k


# ───────────────────────── recall@k ─────────────────────────


class TestRecallAtK:
    """recall@k = |gold ∩ top-k| / |gold|。"""

    def test_gold_at_first_position_full_recall(self):
        ranked = ["a", "b", "c", "d"]
        assert recall_at_k(ranked, ["a"], k=4) == 1.0

    def test_gold_at_last_position_within_k(self):
        ranked = ["a", "b", "c", "d"]
        assert recall_at_k(ranked, ["d"], k=4) == 1.0

    def test_gold_beyond_k_is_zero(self):
        """gold 在第 5 位但 k=4 → 不该计入。"""
        ranked = ["a", "b", "c", "d", "g"]
        assert recall_at_k(ranked, ["g"], k=4) == 0.0

    def test_gold_not_in_results_is_zero(self):
        ranked = ["a", "b", "c"]
        assert recall_at_k(ranked, ["x"], k=3) == 0.0

    def test_multiple_gold_partial_recall(self):
        """3 个 gold 命中 1 个 recall=1/3。"""
        ranked = ["g1", "b", "c"]
        assert recall_at_k(ranked, ["g1", "g2", "g3"], k=3) == pytest.approx(1 / 3)

    def test_multiple_gold_all_within_k_full_recall(self):
        ranked = ["x", "g1", "y", "g2"]
        assert recall_at_k(ranked, ["g1", "g2"], k=4) == 1.0

    def test_k_larger_than_ranked_clamped(self):
        """k > len(ranked) 不报错，按实际长度算。"""
        ranked = ["a", "g"]
        assert recall_at_k(ranked, ["g"], k=100) == 1.0

    def test_empty_results_zero_recall(self):
        assert recall_at_k([], ["g"], k=5) == 0.0

    def test_empty_gold_returns_zero_not_nan(self):
        """无 gold（标注缺失）→ 0 而非 0/0 形式的 nan。"""
        assert recall_at_k(["a", "b"], [], k=2) == 0.0

    def test_duplicate_gold_counted_once(self):
        """gold 列表含重复 id 不虚增分母（去重）。"""
        ranked = ["a", "b"]
        # 两份相同 gold，命中 1 个唯一 id → recall=1/1=1.0
        assert recall_at_k(ranked, ["a", "a"], k=2) == 1.0

    def test_k_zero_returns_zero(self):
        assert recall_at_k(["a", "b"], ["a"], k=0) == 0.0


# ───────────────────────── MRR ─────────────────────────


class TestMRR:
    """MRR = 1 / rank_of_first_gold（最靠前 gold 的倒数排名）。"""

    def test_gold_at_position_1_is_one(self):
        assert mrr(["a", "b", "c"], ["a"]) == 1.0

    def test_gold_at_position_2_is_half(self):
        assert mrr(["a", "g", "c"], ["g"]) == 0.5

    def test_gold_at_position_3(self):
        assert mrr(["a", "b", "g"], ["g"]) == pytest.approx(1 / 3)

    def test_gold_not_in_results_is_zero(self):
        assert mrr(["a", "b", "c"], ["x"]) == 0.0

    def test_multiple_gold_first_hit_wins(self):
        """多 gold 时取最靠前那个的 RR。"""
        ranked = ["a", "g1", "b", "g2"]
        # 第一个 gold(g1) 在第 2 位 → 1/2
        assert mrr(ranked, ["g1", "g2"]) == 0.5

    def test_empty_results_zero(self):
        assert mrr([], ["g"]) == 0.0

    def test_empty_gold_zero(self):
        assert mrr(["a", "b"], []) == 0.0


# ───────────────────────── nDCG ─────────────────────────


class TestNDCG:
    """nDCG = DCG / iDCG，二值相关性：gold 命中位置=1，否则=0。

    DCG = sum_{i where rel_i=1} 1/log2(i+2)，i 从 0 起 → 位置1折扣=log2(2)=1
    iDCG = gold 全部排在最前的理想 DCG。
    """

    def test_gold_at_top_is_one(self):
        # gold 在第 0 位：DCG = 1/log2(2) = 1；iDCG = 1 → nDCG=1
        assert ndcg(["a", "b", "c"], ["a"]) == 1.0

    def test_gold_at_position_2_zero_indexed(self):
        # gold 在第 1 位：DCG = 1/log2(3) ≈ 0.6309
        # iDCG（gold 排第0位）= 1/log2(2) = 1
        expected = 1 / math.log2(3)
        assert ndcg(["a", "g", "b"], ["g"]) == pytest.approx(expected)

    def test_gold_not_in_results_is_zero(self):
        assert ndcg(["a", "b", "c"], ["x"]) == 0.0

    def test_two_golds_ideal_order_is_perfect(self):
        """两 gold 都排最前两位 → nDCG=1.0。"""
        ranked = ["g1", "g2", "a", "b"]
        assert ndcg(ranked, ["g1", "g2"]) == 1.0

    def test_two_golds_swapped_order_is_not_one(self):
        """两 gold 但位置颠倒（g2 第0、g1 第1，理想是 g1 第0、g2 第1）。
        二值相关性下两位置贡献对称（都是 1 分），nDCG 仍应=1.0（因为二值 rel
        不区分两个 gold 的先后）。验证二值 nDCG 的对称性。"""
        ranked = ["g2", "g1", "a", "b"]
        assert ndcg(ranked, ["g1", "g2"]) == 1.0

    def test_two_golds_split_positions(self):
        """g1 第0、g2 第2：DCG = 1/log2(2) + 1/log2(4) = 1 + 0.5 = 1.5
        iDCG = 1/log2(2) + 1/log2(3) = 1 + 0.6309 = 1.6309
        nDCG = 1.5/1.6309 ≈ 0.9196"""
        ranked = ["g1", "a", "g2", "b"]
        dcg = 1 / math.log2(2) + 1 / math.log2(4)  # 1 + 0.5
        idcg = 1 / math.log2(2) + 1 / math.log2(3)  # 1 + 0.6309
        assert ndcg(ranked, ["g1", "g2"]) == pytest.approx(dcg / idcg)

    def test_empty_results_zero(self):
        assert ndcg([], ["g"]) == 0.0

    def test_empty_gold_zero(self):
        assert ndcg(["a", "b"], []) == 0.0

    def test_ndcg_range_zero_to_one(self):
        """任意排列 nDCG ∈ [0,1]。"""
        ranked = ["a", "b", "g1", "c", "g2", "d"]
        val = ndcg(ranked, ["g1", "g2"])
        assert 0.0 <= val <= 1.0
