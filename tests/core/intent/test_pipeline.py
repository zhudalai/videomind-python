"""意图路由管线顶层入口 + 工厂单例 测试."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from videomind.core.intent.types import ChannelQuota, IntentRouteResult, RewriteContext


class TestIntentPipeline:
    """意图路由管线顶层入口测试."""

    @pytest.mark.asyncio
    async def test_route_query_returns_intent_route_result(self):
        """pipeline 顶层入口返回完整的 IntentRouteResult."""
        with patch("videomind.core.intent.pipeline.get_intent_service") as mock_get:
            from videomind.core.intent.pipeline import route_query

            mock_svc = AsyncMock()
            mock_svc.rewriter = AsyncMock()
            mock_svc.rewriter.rewrite = AsyncMock()
            mock_svc.rewriter.rewrite.return_value.method = "rule"
            mock_svc.rewriter.rewrite.return_value.confidence = 0.6
            mock_svc.rewriter.rewrite.return_value.sub_queries = ["子问题1"]

            mock_svc.router = AsyncMock()
            mock_svc.router.route = AsyncMock()
            mock_svc.router.route.return_value = IntentRouteResult(
                intent_weights={"video_qa": 0.8, "video_search": 0.2},
                channel_quota=ChannelQuota(vector=40, bm25=15, sql=5),
                primary_intent="video_qa",
            )

            mock_get.return_value = mock_svc

            result = await route_query("视频讲了什么", RewriteContext())
            assert isinstance(result, IntentRouteResult)
            assert result.primary_intent == "video_qa"

    @pytest.mark.asyncio
    async def test_route_query_default_context(self):
        """不传 RewriteContext 时使用默认空上下文."""
        with patch("videomind.core.intent.pipeline.get_intent_service") as mock_get:
            from videomind.core.intent.pipeline import route_query

            mock_svc = AsyncMock()
            mock_svc.rewriter.rewrite = AsyncMock()
            mock_svc.rewriter.rewrite.return_value.sub_queries = ["q1"]
            mock_svc.rewriter.rewrite.return_value.method = "llm"
            mock_svc.rewriter.rewrite.return_value.confidence = 0.9
            mock_svc.router = AsyncMock()
            mock_svc.router.route = AsyncMock()
            mock_svc.router.route.return_value = IntentRouteResult(
                intent_weights={"video_analysis": 1.0},
                primary_intent="video_analysis",
            )
            mock_get.return_value = mock_svc

            result = await route_query("深度分析视频")  # 不传 context
            assert result.primary_intent == "video_analysis"


class TestIntentFactory:
    """工厂函数测试."""

    def test_get_intent_service_is_singleton(self):
        """单例：多次调用返回同一实例."""
        from videomind.core.intent.pipeline import get_intent_service

        with patch("videomind.core.intent.pipeline.get_llm_service") as mock_llm:
            mock_llm.return_value = AsyncMock()
            get_intent_service.cache_clear()
            a = get_intent_service()
            b = get_intent_service()
            assert a is b
            get_intent_service.cache_clear()