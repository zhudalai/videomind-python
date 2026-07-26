"""意图路由模块数据模型测试."""

from __future__ import annotations

import pytest
from videomind.core.intent.types import (
    RewriteResult,
    RewriteContext,
    ChannelQuota,
    IntentRouteResult,
)


class TestRewriteResult:
    """RewriteResult 数据类测试."""

    def test_rewrite_result_defaults(self) -> None:
        """创建 RewriteResult 时默认值正确."""
        r = RewriteResult(
            original="原查询",
            rewritten="改写查询",
            sub_queries=["q1"],
        )
        assert r.original == "原查询"
        assert r.rewritten == "改写查询"
        assert r.sub_queries == ["q1"]
        assert r.method == "original"
        assert r.confidence == 0.5
        assert r.entities == {}


class TestRewriteContext:
    """RewiteContext 数据类测试."""

    def test_rewrite_context_defaults(self) -> None:
        """可选字段默认为 None."""
        ctx = RewriteContext()
        assert ctx.video_title is None
        assert ctx.duration_sec is None


class TestChannelQuota:
    """ChannelQuota 数据类测试."""

    def test_channel_quota_defaults_to_zero(self) -> None:
        """默认配额都为零."""
        q = ChannelQuota()
        assert q.vector == 0
        assert q.bm25 == 0
        assert q.sql == 0


class TestIntentRouteResult:
    """IntentRouteResult 数据类测试."""

    @pytest.fixture
    def sample_result(self) -> IntentRouteResult:
        """构造一个示例路由结果."""
        return IntentRouteResult(
            intent_weights={"video_qa": 0.6, "video_search": 0.4},
            channel_quota=ChannelQuota(vector=30, bm25=20, sql=10),
            primary_intent="video_qa",
        )

    def test_intent_route_result_primary_intent(
        self, sample_result: IntentRouteResult
    ) -> None:
        """primary_intent 字段正确存储."""
        assert sample_result.primary_intent == "video_qa"
        assert sample_result.intent_weights["video_qa"] == 0.6
        assert sample_result.channel_quota.vector == 30

    def test_intent_weights_dict_is_accessible(
        self, sample_result: IntentRouteResult
    ) -> None:
        """intent_weights 字典可正常访问."""
        assert len(sample_result.intent_weights) == 2
        assert "video_qa" in sample_result.intent_weights
        assert "video_search" in sample_result.intent_weights