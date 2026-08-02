"""Critic 评审器测试."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from videomind.core.agent_loop.types import AgentState, AnalysisResult


@pytest.mark.asyncio
async def test_critic_passes_with_perfect_result() -> None:
    """LLM 返回高质量评审结果，且真实命中校验通过。"""
    from videomind.core.agent_loop.critic import Critic
    from videomind.core.agent_loop.verifier import EvidenceVerifier
    from videomind.core.agent_loop.types import Conclusion, Evidence

    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock()
    mock_llm.chat.return_value.content = json.dumps({
        "passed": True, "feedback": "很好", "required_timestamps": [],
        "coverage_score": 0.95, "structure_ok": True,
        "evidence_verified": True, "hallucination_risk": 0.05,
    })
    critic = Critic(mock_llm, EvidenceVerifier())
    state = AgentState(
        goal="分析视频",
        retrieved_evidence_ids={"EID_real"},
        result=AnalysisResult(title="T",
            evidence=[Evidence(id="EID_real", chunk_id="c1", content="x")],
            conclusions=[Conclusion(point="ok", evidence_ids=["EID_real"])]),
    )
    mock_db = AsyncMock(spec=AsyncSession)
    result = await critic.critique(state, mock_db)
    assert result.passed is True
    assert result.coverage_score == 0.95
    assert result.evidence_verified is True


@pytest.mark.asyncio
async def test_critic_fails_with_low_coverage() -> None:
    """LLM 返回低分评审结果，CriticResult 反映不通过状态。"""
    from videomind.core.agent_loop.critic import Critic
    from videomind.core.agent_loop.verifier import EvidenceVerifier

    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock()
    mock_llm.chat.return_value.content = json.dumps({
        "passed": False,
        "feedback": "证据不足",
        "required_timestamps": [12000],
        "coverage_score": 0.3,
        "structure_ok": False,
        "evidence_verified": False,
        "hallucination_risk": 0.6,
    })

    critic = Critic(mock_llm, EvidenceVerifier())
    state = AgentState(
        goal="分析",
        result=AnalysisResult(title="T"),
    )
    mock_db = AsyncMock(spec=AsyncSession)
    result = await critic.critique(state, mock_db)

    assert result.passed is False
    assert result.required_timestamps == [12000]


@pytest.mark.asyncio
async def test_critic_LLM_error_graceful() -> None:
    """LLM 调用失败时优雅降级，返回默认不通过结果，不抛异常。"""
    from videomind.core.agent_loop.critic import Critic
    from videomind.core.agent_loop.verifier import EvidenceVerifier

    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock(side_effect=RuntimeError("timeout"))

    critic = Critic(mock_llm, EvidenceVerifier())
    state = AgentState(
        goal="test",
        result=AnalysisResult(title="T"),
    )
    mock_db = AsyncMock(spec=AsyncSession)
    result = await critic.critique(state, mock_db)

    # LLM 失败不应抛异常，返回默认失败结果
    assert result.passed is False