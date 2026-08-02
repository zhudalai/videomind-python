"""AgentLoop 主循环 + 工厂函数测试."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from videomind.core.agent_loop.types import (
    AgentPlan,
    AnalysisResult,
    AgentState,
    Conclusion,
    CriticResult,
    SubTask,
)


@pytest.mark.asyncio
async def test_agent_loop_single_round_passes() -> None:
    """Critic 通过 -> 1 轮完成."""
    from videomind.core.agent_loop.loop import AgentLoop

    mock_planner = AsyncMock()
    mock_planner.plan = AsyncMock(return_value=AgentPlan(
        tasks=[SubTask(
            id="t1", description="d1", required_evidence_type="text",
        )],
        reasoning="reason",
    ))

    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock(return_value=type("AnalysisResult", (), {
        "title": "OK", "conclusions": [
            Conclusion(point="p", evidence_ids=["EID_01"]),
        ], "suggestions": ["s1"],
    })())

    mock_critic = AsyncMock()
    mock_critic.critique = AsyncMock(return_value=CriticResult(
        passed=True, feedback="good", coverage_score=0.9, structure_ok=True,
        evidence_verified=True, hallucination_risk=0.1,
    ))

    loop = AgentLoop(mock_planner, mock_executor, mock_critic)
    mock_db = AsyncMock(spec=AsyncSession)
    result = await loop.run("分析视频", mock_db)

    assert result.title == "OK"
    assert len(result.conclusions) == 1
    assert mock_planner.plan.call_count == 1
    assert mock_critic.critique.call_count == 1


@pytest.mark.asyncio
async def test_agent_loop_two_rounds_max() -> None:
    """Critic 两次不过，max 2 rounds 后返回当前结果."""
    from videomind.core.agent_loop.loop import AgentLoop

    mock_planner = AsyncMock()
    plan = AgentPlan(
        tasks=[SubTask(id="t1", description="d1", required_evidence_type="text")],
        reasoning="r",
    )
    mock_planner.plan = AsyncMock(return_value=plan)

    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock(return_value=AnalysisResult(
        title="Final", conclusions=[], evidence=[], suggestions=[],
    ))

    mock_critic = AsyncMock()
    mock_critic.critique = AsyncMock(side_effect=[
        CriticResult(passed=False, feedback="不行", coverage_score=0.3),
        CriticResult(passed=True, coverage_score=0.9),
    ])

    loop = AgentLoop(mock_planner, mock_executor, mock_critic)
    mock_db = AsyncMock(spec=AsyncSession)
    result = await loop.run("goal", mock_db)

    assert result.title == "Final"
    assert mock_planner.plan.call_count == 2
    assert mock_critic.critique.call_count == 2


@pytest.mark.asyncio
async def test_get_agent_loop_is_singleton() -> None:
    """get_agent_loop 返回同一个实例（lru_cache 单例）."""
    with patch("videomind.core.agent_loop.factory.get_llm_service") as mock_llm:
        mock_llm.return_value = AsyncMock()
        from videomind.core.agent_loop.factory import get_agent_loop
        get_agent_loop.cache_clear()
        a = get_agent_loop()
        b = get_agent_loop()
        assert a is b
        get_agent_loop.cache_clear()


@pytest.mark.asyncio
async def test_agent_loop_respects_max_rounds_one() -> None:
    """max_rounds=1 时，即便 Critic 不通过也只跑 1 轮."""
    from videomind.core.agent_loop.loop import AgentLoop

    mock_planner = AsyncMock()
    mock_planner.plan = AsyncMock(return_value=AgentPlan(
        tasks=[SubTask(id="t1", description="d", required_evidence_type="text")], reasoning="r"))
    mock_executor = AsyncMock()
    mock_executor.execute = AsyncMock(return_value=AnalysisResult(
        title="F", conclusions=[], evidence=[], suggestions=[]))
    mock_critic = AsyncMock()
    mock_critic.critique = AsyncMock(return_value=CriticResult(
        passed=False, feedback="no", coverage_score=0.3))

    loop = AgentLoop(mock_planner, mock_executor, mock_critic)
    mock_db = AsyncMock(spec=AsyncSession)
    await loop.run("goal", mock_db, max_rounds=1)

    assert mock_planner.plan.call_count == 1
    assert mock_critic.critique.call_count == 1