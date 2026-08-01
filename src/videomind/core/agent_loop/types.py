"""Agent Loop 数据结构模块。

定义代理循环所需的 8 个核心数据类型：
AgentState, AgentPlan, SubTask, AnalysisResult,
Conclusion, Evidence, CriticResult, VideoMeta。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
from uuid import uuid4


@dataclass
class SubTask:
    """单个子任务。

    Attributes:
        id: 子任务唯一标识
        description: 任务描述
        required_evidence_type: 所需证据类型 ("frame", "text", "audio", "sql")
        time_range_hint: 可选的时间范围提示 (start_ms, end_ms)
        search_query: 检索友好查询词；为空则 Executor 退化用 description
    """

    id: str
    description: str
    required_evidence_type: Literal["frame", "text", "audio", "sql"]
    time_range_hint: tuple[int, int] | None = None
    search_query: str = ""


@dataclass
class VideoMeta:
    """单个目标视频元信息（注入 Planner/Executor 上下文）。"""

    media_id: str
    filename: str
    duration_ms: int | None = None


@dataclass
class Evidence:
    """证据条目（承载真实 RAG 检索结果 + 来源视频）。

    新流程使用 chunk_id/content/source_type/score/start_ms/end_ms/media_id/media_title。
    timestamp_ms/source 为退役字段（保留默认值以兼容旧 verifier 测试），新代码不写入/不渲染。
    """

    id: str
    chunk_id: str = ""
    content: str = ""
    source_type: str = ""                 # asr | ocr | mixed（来自 RAG）
    score: float = 0.0
    start_ms: int | None = None
    end_ms: int | None = None
    media_id: str = ""                    # 来源视频 UUID 字符串
    media_title: str = ""                 # 来源视频文件名（前端直渲染）
    # 退役字段（保留默认，兼容旧 verifier 测试；新流程不写入）
    timestamp_ms: int = 0
    source: Literal["frame", "text", "audio", "sql"] = "text"


@dataclass
class Conclusion:
    """分析结论。

    Attributes:
        point: 结论要点
        evidence_ids: 支撑该结论的证据 ID 列表
        confidence: 置信度 (0.0-1.0)
    """

    point: str
    evidence_ids: list[str] = field(default_factory=list)
    confidence: float = 0.5


@dataclass
class AnalysisResult:
    """分析执行结果。

    Attributes:
        title: 分析结果标题
        conclusions: 结论列表
        evidence: 所用证据列表
        suggestions: 建议列表
    """

    title: str
    conclusions: list[Conclusion] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)


@dataclass
class AgentPlan:
    """规划结果。

    Attributes:
        tasks: 子任务列表
        reasoning: 规划推理说明
    """

    tasks: list[SubTask] = field(default_factory=list)
    reasoning: str = ""


@dataclass
class CriticResult:
    """评审结果。

    Attributes:
        passed: 是否通过评审
        feedback: 评审反馈信息
        required_timestamps: 需要关注的时间戳列表（不通过时给出）
        coverage_score: 覆盖度评分 (0.0-1.0)
        structure_ok: 结构是否合理
        evidence_verified: 证据是否已验证
        hallucination_risk: 幻觉风险 (0.0-1.0)
    """

    passed: bool = False
    feedback: str = ""
    required_timestamps: list[int] = field(default_factory=list)
    coverage_score: float = 0.0
    structure_ok: bool = False
    evidence_verified: bool = False
    hallucination_risk: float = 0.0


@dataclass
class AgentState:
    """代理状态总容器。

    Attributes:
        goal: 当前目标
        media_ids: 目标视频 media_id 列表
        video_meta: 目标视频元信息列表
        retrieved_evidence_ids: 已检索证据 ID 集合（Executor 填，Critic 读）
        plan: 规划结果（可选）
        result: 分析执行结果（可选）
        critique: 评审结果（可选）
        round: 当前轮次
        trace_id: 追踪标识
    """

    goal: str
    media_ids: list[str] = field(default_factory=list)
    video_meta: list[VideoMeta] = field(default_factory=list)
    retrieved_evidence_ids: set[str] = field(default_factory=set)
    plan: AgentPlan | None = None
    result: AnalysisResult | None = None
    critique: CriticResult | None = None
    round: int = 0
    trace_id: str = field(default_factory=lambda: uuid4().hex)


__all__ = [
    "AgentState",
    "AgentPlan",
    "SubTask",
    "AnalysisResult",
    "Conclusion",
    "Evidence",
    "CriticResult",
    "VideoMeta",
]