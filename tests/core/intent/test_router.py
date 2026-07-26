"""意图树形路由器测试."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from videomind.core.intent.types import ChannelQuota, IntentRouteResult


class TestIntentRouterKeywordOnly:
    """仅关键词模式（不依赖 LLM）的测试."""

    def test_keyword_match_finds_leaf_intents(self):
        """基于 DEFAULT_INTENT_TREE 的关键词匹配."""
        from videomind.core.intent.router import IntentRouter
        router = IntentRouter(llm=None)  # llm=None 仅用关键词

        result = router._keyword_match("这个视频讲了什么内容")
        # 应匹配 video_qa.summary ("摘要"/"总结"/"概括")，video_qa.detail ("内容")
        assert len(result) > 0
        assert "video_qa.summary" in result or any("video_qa" in k for k in result)

    def test_no_keyword_match_returns_all_default(self):
        """无关键词命中时返回空 dict."""
        from videomind.core.intent.router import IntentRouter
        router = IntentRouter(llm=None)

        result = router._keyword_match("xyz123无关词")
        assert result == {}


class TestIntentRouterRoute:
    """route() 接口测试（关键词 + LLM 融合）."""

    @pytest.mark.asyncio
    async def test_route_with_keywords_only(self):
        """仅关键词模式 route 接口应返回合法 IntentRouteResult."""
        from videomind.core.intent.router import IntentRouter
        router = IntentRouter(llm=None)

        r: IntentRouteResult = await router.route("视频内容分析", ["分析视频"])
        assert r.primary_intent in r.intent_weights
        assert 0 < sum(r.intent_weights.values()) <= 1.1
        assert r.channel_quota.vector + r.channel_quota.bm25 + r.channel_quota.sql <= 180

    @pytest.mark.asyncio
    async def test_route_with_llm_fusion(self):
        """LLM 融合模式: route 应融合 LLM 分数与关键词分数."""
        from videomind.core.intent.router import IntentRouter
        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock()
        mock_llm.chat.return_value.content = json.dumps({
            "video_qa.summary": 0.8,
            "video_analysis.compare": 0.4,
        })

        router = IntentRouter(llm=mock_llm)
        r = await router.route("对比两个视频的摘要", ["子问题1"])

        assert len(r.intent_weights) > 0
        assert r.primary_intent is not None
        assert r.channel_quota is not None

    @pytest.mark.asyncio
    async def test_route_LLM_failure_graceful(self):
        """LLM 调用失败不应抛异常，关键词仍能匹配."""
        from videomind.core.intent.router import IntentRouter
        mock_llm = AsyncMock()
        mock_llm.chat = AsyncMock(side_effect=RuntimeError("timeout"))

        router = IntentRouter(llm=mock_llm)
        r = await router.route("视频内容分析", ["分析视频"])
        # LLM 失败不应抛异常，关键词仍能匹配
        assert r.primary_intent is not None
        assert len(r.intent_weights) > 0