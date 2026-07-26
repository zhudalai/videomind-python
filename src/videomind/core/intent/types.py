"""意图路由模块数据模型。

定义查询改写、意图分类、多通道配额等核心数据结构。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RewriteContext:
    """查询改写上下文信息."""

    video_title: str | None = None
    duration_sec: int | None = None


@dataclass
class RewriteResult:
    """查询改写结果.

    Attributes:
        original: 原始查询字符串
        rewritten: 改写后的主查询
        sub_queries: 拆解后的子问题列表 (2-4 个)
        entities: 实体归一化映射
        method: 改写方法 ("llm", "rule", "original")
        confidence: 置信度 0.0-1.0
    """

    original: str = ""
    rewritten: str = ""
    sub_queries: list[str] = field(default_factory=list)
    entities: dict[str, str] = field(default_factory=dict)
    method: str = "original"
    confidence: float = 0.5


@dataclass
class ChannelQuota:
    """多通道检索配额."""

    vector: int = 0
    bm25: int = 0
    sql: int = 0


@dataclass
class IntentRouteResult:
    """意图路由结果."""

    intent_weights: dict[str, float] = field(default_factory=dict)
    channel_quota: ChannelQuota = field(default_factory=ChannelQuota)
    primary_intent: str = "video_qa"