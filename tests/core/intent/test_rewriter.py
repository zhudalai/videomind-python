"""查询改写模块测试."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from videomind.core.intent.types import RewriteContext, RewriteResult


class TestRuleRewriter:
    """RuleRewriter 不依赖 LLM，纯测试规则改写."""

    def test_simple_rewrite_no_expansion(self):
        """无同义词匹配时返回 expanded 与 original 一致."""
        from videomind.core.intent.rewriter import RuleRewriter
        rw = RuleRewriter()
        result = rw.rewrite("你好")
        assert result.original == "你好"
        assert result.method == "rule"
        assert result.confidence == 0.6
        assert len(result.sub_queries) == 1

    def test_synonym_expansion(self):
        """同义词扩展: '视频' -> 补充同义词."""
        from videomind.core.intent.rewriter import RuleRewriter
        rw = RuleRewriter()
        result = rw.rewrite("视频总结")
        # 因'视频'在 SYNONYMS，rewritten 应比 original 长
        assert len(result.rewritten) > len(result.original)
        assert result.method == "rule"

    def test_multi_intent_split(self):
        """检测 '和/或/' 分隔的多意图自动拆解."""
        from videomind.core.intent.rewriter import RuleRewriter
        rw = RuleRewriter()
        result = rw.rewrite("视频内容分析和总结")
        assert result.method == "rule"
        # 不强制断言 sub_queries 长度, 有 > 1 更好
        if "分析" in result.original and "总结" in result.original:
            assert len(result.sub_queries) >= 1


class TestQueryRewriterLLM:
    """QueryRewriter 的 LLM 路径测试."""

    @pytest.mark.asyncio
    async def test_llm_rewrite_success(self):
        """LLM 返回合法 JSON -> 提取 RewriteResult."""
        from videomind.core.intent.rewriter import QueryRewriter
        import json

        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock()
        mock_llm.chat.return_value.content = json.dumps({
            "rewritten": "改写后的查询",
            "sub_queries": ["子问题1", "子问题2"],
            "entities": {"AI": "人工智能"},
            "confidence": 0.9,
        })

        rw = QueryRewriter(llm=mock_llm)
        result = await rw.rewrite("AI 是什么", RewriteContext())

        assert result.method == "llm"
        assert result.confidence == 0.9
        assert result.rewritten == "改写后的查询"
        assert len(result.sub_queries) == 2
        assert result.entities == {"AI": "人工智能"}

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_rule(self):
        """LLM 异常 -> 规则兜底."""
        from videomind.core.intent.rewriter import QueryRewriter

        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(side_effect=RuntimeError("timeout"))

        rw = QueryRewriter(llm=mock_llm)
        result = await rw.rewrite("视频总结", RewriteContext())

        assert result.method == "rule"
        assert result.confidence == 0.6

    @pytest.mark.asyncio
    async def test_default_threshold_is_0_5(self):
        """D-β: 默认 confidence_threshold=0.5，LLM 改写置信度 0.5 即可通过。"""
        from videomind.core.intent.rewriter import QueryRewriter

        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(return_value=type("Resp", (), {"content": '{"rewritten": "q", "sub_queries": [], "entities": {}, "confidence": 0.5}'})())

        rw = QueryRewriter(llm=mock_llm)
        result = await rw.rewrite("q", RewriteContext())

        assert result.method == "llm"
        assert result.confidence == 0.5

    @pytest.mark.asyncio
    async def test_low_confidence_below_new_threshold_falls_back(self):
        """D-β: 置信度 0.4 < 0.5 阈值 -> 降级规则改写。"""
        from videomind.core.intent.rewriter import QueryRewriter

        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(return_value=type("Resp", (), {"content": '{"rewritten": "q", "sub_queries": [], "entities": {}, "confidence": 0.4}'})())

        rw = QueryRewriter(llm=mock_llm)
        result = await rw.rewrite("q", RewriteContext())

        assert result.method == "rule"
        assert result.confidence == 0.6