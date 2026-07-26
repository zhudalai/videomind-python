"""DeterministicReranker 确定性重排测试。

验证三阶段线性加权重排：
  - 空列表
  - 单 hit
  - 不同 source_type 下的排序优先级（asr > mixed > ocr）
  - 位次衰减效果
"""

import pytest

from videomind.core.rag.rerank import DeterministicReranker
from videomind.core.rag.vector import VectorHit


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def make_hit(chunk_id: str, score: float, source_type: str) -> VectorHit:
    """快速构造测试用 VectorHit。"""
    return VectorHit(
        chunk_id=chunk_id,
        score=score,
        content="...",
        source_type=source_type,
        content_hash="hash",
    )


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------

class TestDeterministicReranker:
    """确定性重排器测试集合。"""

    def test_rerank_empty(self):
        """空列表输入返回空列表。"""
        reranker = DeterministicReranker()
        result = reranker.rerank([])
        assert result == []

    def test_rerank_single_hit(self):
        """单个 hit 时 score 被正确更新且仍为列表。"""
        hit = make_hit("only", 0.5, "asr")
        reranker = DeterministicReranker()
        result = reranker.rerank([hit])

        assert len(result) == 1
        assert result[0].chunk_id == "only"
        # 位次分=1.0, 来源分=0.3, 原始分=0.5
        expected = 0.3 * 1.0 + 0.2 * 0.3 + 0.5 * 0.5
        assert result[0].score == pytest.approx(expected)

    def test_rerank_ordering_asr_first(self):
        """同等原始分，输入 asr→mixed→ocr 顺序时，位次+来源同向加分保持排序。"""
        hits = [
            make_hit("b", 0.5, "asr"),
            make_hit("c", 0.5, "mixed"),
            make_hit("a", 0.5, "ocr"),
        ]
        reranker = DeterministicReranker()
        result = reranker.rerank(hits)

        # 位次+来源同向：asr(b) > mixed(c) > ocr(a)
        assert result[0].chunk_id == "b"
        assert result[1].chunk_id == "c"
        assert result[2].chunk_id == "a"

    def test_rerank_different_source_types(self):
        """不同来源类型：asr 排前 + mixed + ocr + other 输入，位次+来源均同向加分。"""
        hits = [
            make_hit("asr_hit", 0.6, "asr"),
            make_hit("mixed_hit", 0.6, "mixed"),
            make_hit("ocr_hit", 0.6, "ocr"),
            make_hit("unknown_hit", 0.6, "other"),
        ]
        reranker = DeterministicReranker()
        result = reranker.rerank(hits)

        # 同等位次+原始分，来源bonus：asr(0.3) > mixed(0.25) > ocr(0.2) > other(0.15)
        assert result[0].chunk_id == "asr_hit"
        assert result[1].chunk_id == "mixed_hit"
        assert result[2].chunk_id == "ocr_hit"
        assert result[3].chunk_id == "unknown_hit"

    def test_rerank_position_weights(self):
        """靠前的 hit 因位次衰减权重更高，能在同来源同原始分时胜出。"""
        hits = [
            make_hit("first", 0.5, "ocr"),
            make_hit("second", 0.5, "ocr"),
            make_hit("third", 0.5, "ocr"),
        ]
        reranker = DeterministicReranker()
        result = reranker.rerank(hits)

        # 第一位位次 > 第二位 > 第三位
        assert result[0].chunk_id == "first"
        assert result[1].chunk_id == "second"
        assert result[2].chunk_id == "third"

    def test_rerank_original_score_dominates(self):
        """原始得分权重 0.5 占主导，src+position 相同时靠原始分排序。"""
        hits = [
            make_hit("low", 0.1, "asr"),
            make_hit("high", 0.9, "asr"),
        ]
        reranker = DeterministicReranker()
        result = reranker.rerank(hits)

        # 原始分高者胜出（权重 0.5 占优 + source 相同 + 位次接近）
        assert result[0].chunk_id == "high"
        assert result[1].chunk_id == "low"

    def test_rerank_score_clamped(self):
        """原始分超过 1.0 时被 clamp 到 1.0，低于 0.0 时 clamp 到 0.0。"""
        hits = [
            make_hit("over", 2.5, "asr"),
            make_hit("under", -0.5, "ocr"),
        ]
        reranker = DeterministicReranker()
        result = reranker.rerank(hits)

        # 验证 clamp 效果：over 的原始分应按 1.0 计算，under 按 0.0
        for hit in result:
            assert 0.0 <= hit.score <= 1.0

    def test_rerank_inplace_modification(self):
        """rerank 应原地修改传入的 hit 列表的 score 字段。"""
        hits = [
            make_hit("a", 0.5, "asr"),
            make_hit("b", 0.5, "ocr"),
        ]
        original_hits = hits[:]  # 保存引用
        reranker = DeterministicReranker()
        result = reranker.rerank(hits)

        # 传入的列表对象本身被排序
        assert result is hits
        # 原对象 score 已被修改
        assert hits[0].score != 0.5
        assert hits[1].score != 0.5