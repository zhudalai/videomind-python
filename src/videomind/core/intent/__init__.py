"""意图路由模块.

提供查询改写、意图分发和多通道检索配额等核心数据结构。
"""

from __future__ import annotations

from videomind.core.intent.router import DEFAULT_CHANNEL_WEIGHTS, DEFAULT_INTENT_TREE, IntentRouter
from videomind.core.intent.types import (
    ChannelQuota,
    IntentRouteResult,
    RewriteContext,
    RewriteResult,
)

__all__ = [
    "ChannelQuota",
    "DEFAULT_CHANNEL_WEIGHTS",
    "DEFAULT_INTENT_TREE",
    "IntentRouteResult",
    "IntentRouter",
    "RewriteContext",
    "RewriteResult",
]