"""Agent Loop 数据结构测试."""

from __future__ import annotations

import pytest
from videomind.core.agent_loop.types import (
    AgentState,
    AgentPlan,
    SubTask,
    AnalysisResult,
    Conclusion,
    Evidence,
    CriticResult,
)


class TestAgentState:
    """AgentState 数据类测试."""

    def test_agent_state_defaults(self) -> None:
        """创建 AgentState 时所有字段默认值正确."""
        state = AgentState(goal="分析视频内容")
        assert state.goal == "分析视频内容"
        assert state.plan is None
        assert state.result is None
        assert state.critique is None
        assert state.round == 0
        assert isinstance(state.trace_id, str)
        assert len(state.trace_id) > 0


class TestSubTask:
    """SubTask 数据类测试."""

    def test_subtask_with_time_range(self) -> None:
        """SubTask 可选字段 time_range_hint 和 required_evidence_type 正确存储."""
        st = SubTask(
            id="s1",
            description="查找关键帧",
            required_evidence_type="frame",
            time_range_hint=(10, 30),
        )
        assert st.id == "s1"
        assert st.description == "查找关键帧"
        assert st.required_evidence_type == "frame"
        assert st.time_range_hint == (10, 30)

    def test_subtask_without_time_range(self) -> None:
        """SubTask 不传 time_range_hint 时默认为 None."""
        st = SubTask(
            id="s2",
            description="摘要生成",
            required_evidence_type="text",
        )
        assert st.time_range_hint is None


class TestConclusion:
    """Conclusion 数据类测试."""

    def test_conclusion_confidence_range(self) -> None:
        """confidence 应为 0.0-1.0 之间的值."""
        c = Conclusion(
            point="测试结论",
            evidence_ids=["e1", "e2"],
            confidence=0.85,
        )
        assert 0.0 <= c.confidence <= 1.0
        assert c.point == "测试结论"
        assert c.evidence_ids == ["e1", "e2"]

    def test_conclusion_confidence_boundary(self) -> None:
        """confidence 边界值 0.0 和 1.0 正确接受."""
        c_min = Conclusion(
            point="最低置信度",
            evidence_ids=["e1"],
            confidence=0.0,
        )
        c_max = Conclusion(
            point="最高置信度",
            evidence_ids=["e1"],
            confidence=1.0,
        )
        assert c_min.confidence == 0.0
        assert c_max.confidence == 1.0


class TestCriticResult:
    """CriticResult 数据类测试."""

    def test_critic_result_failed_has_required_timestamps(self) -> None:
        """passed=False 时应给出 required_timestamps 列表."""
        cr = CriticResult(
            passed=False,
            feedback="证据不足",
            required_timestamps=[10000, 25000, 40000],
            coverage_score=0.3,
            structure_ok=True,
            evidence_verified=False,
            hallucination_risk=0.6,
        )
        assert cr.passed is False
        assert cr.feedback == "证据不足"
        assert cr.required_timestamps == [10000, 25000, 40000]
        assert cr.coverage_score == 0.3
        assert cr.structure_ok is True
        assert cr.evidence_verified is False
        assert cr.hallucination_risk == 0.6

    def test_critic_result_passed_no_timestamps(self) -> None:
        """passed=True 时 required_timestamps 可以为空列表."""
        cr = CriticResult(
            passed=True,
            feedback="一切正常",
            coverage_score=0.95,
            structure_ok=True,
            evidence_verified=True,
            hallucination_risk=0.05,
        )
        assert cr.passed is True
        assert cr.required_timestamps == []
        assert cr.feedback == "一切正常"


class TestEvidence:
    """Evidence 数据类测试."""

    def test_evidence_fields(self) -> None:
        """Evidence 所有字段正确赋值."""
        ev = Evidence(
            id="ev1",
            timestamp_ms=5000,
            source="frame",
            content="帧中包含关键目标",
            chunk_id="chunk_001",
        )
        assert ev.id == "ev1"
        assert ev.timestamp_ms == 5000
        assert ev.source == "frame"
        assert ev.content == "帧中包含关键目标"
        assert ev.chunk_id == "chunk_001"


class TestAnalysisResult:
    """AnalysisResult 数据类测试."""

    def test_analysis_result_empty_defaults(self) -> None:
        """AnalysisResult 默认字段初始化为空列表."""
        result = AnalysisResult(title="分析结果")
        assert result.title == "分析结果"
        assert result.conclusions == []
        assert result.evidence == []
        assert result.suggestions == []


class TestAgentPlan:
    """AgentPlan 数据类测试."""

    def test_agent_plan_fields(self) -> None:
        """AgentPlan 正确存储 tasks 和 reasoning."""
        plan = AgentPlan(
            tasks=[SubTask(id="s1", description="任务1", required_evidence_type="text")],
            reasoning="需要先提取文本再分析",
        )
        assert len(plan.tasks) == 1
        assert plan.tasks[0].id == "s1"
        assert plan.reasoning == "需要先提取文本再分析"