"""意图路由管线顶层入口 + 工厂单例。

管线流程：用户查询 → QueryRewriter 改写/拆解 → IntentRouter 分类 → IntentRouteResult。
"""

from __future__ import annotations

from functools import lru_cache

from videomind.core.intent.rewriter import QueryRewriter
from videomind.core.intent.router import IntentRouter
from videomind.core.intent.types import IntentRouteResult, RewriteContext
from videomind.core.model_gateway.factory import get_llm_service


class _IntentService:
    """意图路由服务聚合 —— QueryRewriter + IntentRouter 装配体。

    Attributes:
        rewriter: 查询改写器。
        router: 意图分类路由器。
    """

    __slots__ = ("rewriter", "router")

    def __init__(self, rewriter: QueryRewriter, router: IntentRouter) -> None:
        self.rewriter = rewriter
        self.router = router


@lru_cache
def get_intent_service() -> _IntentService:
    """创建意图服务单例：QueryRewriter + IntentRouter 装配。

    从 Model Gateway 获取 LLM 实例，注入到改写器和路由器中。

    Returns:
        _IntentService: 可执行 route_query 的意图服务单例。
    """
    llm = get_llm_service()
    rw = QueryRewriter(llm=llm)
    rt = IntentRouter(llm=llm)
    return _IntentService(rw, rt)


async def route_query(query: str, context: RewriteContext | None = None) -> IntentRouteResult:
    """意图路由管线顶层入口。

    完整链路：用户查询 → QueryRewriter.rewrite → IntentRouter.route → IntentRouteResult。

    Args:
        query: 用户自然语言查询。
        context: 可选的视频上下文（标题、时长等），为 None 时使用空上下文。

    Returns:
        IntentRouteResult 含意图权重、通道配额和主意图。
    """
    svc = get_intent_service()
    ctx = context or RewriteContext()
    rewritten = await svc.rewriter.rewrite(query, ctx)
    return await svc.router.route(query, rewritten.sub_queries)


__all__ = ["get_intent_service", "route_query"]