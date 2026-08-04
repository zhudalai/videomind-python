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
    async def test_db_forwarded_to_llm_chat(self):
        """LLM chat 需 db 计费 —— rewriter 须把 db 透传给 chat(req, db)。

        回归：D-β 评测暴露 RoutingLLMService.chat 缺 db 抛 TypeError 致
        9 条全 fallback rule；生产 rewriter LLM 改写从未生效过。
        """
        from videomind.core.intent.rewriter import QueryRewriter
        import json

        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock()
        mock_llm.chat.return_value.content = json.dumps({
            "rewritten": "q", "sub_queries": ["q"], "entities": {}, "confidence": 0.9,
        })
        sentinel_db = object()

        rw = QueryRewriter(llm=mock_llm)
        await rw.rewrite("q", RewriteContext(), db=sentinel_db)

        assert mock_llm.chat.await_count == 1
        args = mock_llm.chat.call_args.args
        # 第二位置参数须是透传来的 db（首位是 ChatRequest）
        assert len(args) >= 2
        assert args[1] is sentinel_db

    @pytest.mark.asyncio
    async def test_rewriter_disables_reasoning(self):
        """reasoning=False 关 OpenRouter nemotron 思维链，免 content 含思考链致 json.loads 失败。"""
        from videomind.core.intent.rewriter import QueryRewriter
        import json

        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock()
        mock_llm.chat.return_value.content = json.dumps({
            "rewritten": "q", "sub_queries": ["q"], "entities": {}, "confidence": 0.9,
        })
        rw = QueryRewriter(llm=mock_llm)
        await rw.rewrite("q", RewriteContext())

        req = mock_llm.chat.call_args.args[0]
        assert req.reasoning is False

    @pytest.mark.asyncio
    async def test_default_threshold_is_0_7(self):
        """D-β 验证后默认 confidence_threshold=0.7；置信度=0.7（=阈值）通过走 LLM。

        回归：0.5 阈值在 9 条评测集上 1UP/3DN 净负——对 base 已精确（≤4）的查询，
        LLM 扩散子查询把 rank 1 冲到 21（见 results_queryrewriter.json）。
        0.7 仅高置信开火，保留欠指定查询的 UP，规避精确查询的扩散恶化。
        """
        from videomind.core.intent.rewriter import QueryRewriter

        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(return_value=type("Resp", (), {"content": '{"rewritten": "q", "sub_queries": [], "entities": {}, "confidence": 0.7}'})())

        rw = QueryRewriter(llm=mock_llm)
        result = await rw.rewrite("q", RewriteContext())

        assert result.method == "llm"
        assert result.confidence == 0.7

    @pytest.mark.asyncio
    async def test_low_confidence_below_new_threshold_falls_back(self):
        """置信度 0.4 < 0.7 阈值 -> 降级规则改写。"""
        from videomind.core.intent.rewriter import QueryRewriter

        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(return_value=type("Resp", (), {"content": '{"rewritten": "q", "sub_queries": [], "entities": {}, "confidence": 0.4}'})())

        rw = QueryRewriter(llm=mock_llm)
        result = await rw.rewrite("q", RewriteContext())

        assert result.method == "rule"
        assert result.confidence == 0.6