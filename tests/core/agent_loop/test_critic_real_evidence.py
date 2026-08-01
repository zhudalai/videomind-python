"""Critic 真实命中校验测试."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from videomind.core.agent_loop.types import (
    AgentState, AnalysisResult, Conclusion, Evidence,
)


def _llm(passed: bool) -> AsyncMock:
    m = AsyncMock(); m.chat = AsyncMock()
    m.chat.return_value.content = json.dumps({
        "passed": passed, "feedback": "f", "required_timestamps": [],
        "coverage_score": 0.9, "structure_ok": True,
        "evidence_verified": True, "hallucination_risk": 0.1,
    })
    return m


@pytest.mark.asyncio
async def test_critic_hard_pass_when_all_hits_real() -> None:
    from videomind.core.agent_loop.critic import Critic
    from videomind.core.agent_loop.verifier import EvidenceVerifier

    ev = [Evidence(id="EID_a1", chunk_id="c1", content="x", media_id="m", media_title="a.mp4")]
    state = AgentState(
        goal="g", retrieved_evidence_ids={"EID_a1"},
        result=AnalysisResult(title="T", evidence=ev,
            conclusions=[Conclusion(point="p", evidence_ids=["EID_a1"], confidence=0.8)]),
    )
    critic = Critic(_llm(True), EvidenceVerifier())
    r = await critic.critique(state)
    assert r.passed is True and r.evidence_verified is True


@pytest.mark.asyncio
async def test_critic_hard_fails_when_conclusion_cites_fake_eid() -> None:
    from videomind.core.agent_loop.critic import Critic
    from videomind.core.agent_loop.verifier import EvidenceVerifier

    ev = [Evidence(id="EID_a1", chunk_id="c1", content="x")]
    state = AgentState(
        goal="g", retrieved_evidence_ids={"EID_a1"},
        result=AnalysisResult(title="T", evidence=ev,
            conclusions=[Conclusion(point="p", evidence_ids=["EID_FAKE_99"])]),
    )
    critic = Critic(_llm(True), EvidenceVerifier())  # LLM 说过，但硬校验应拦
    r = await critic.critique(state)
    assert r.passed is False and r.evidence_verified is False


@pytest.mark.asyncio
async def test_critic_llm_fail_short_circuits() -> None:
    from videomind.core.agent_loop.critic import Critic
    from videomind.core.agent_loop.verifier import EvidenceVerifier

    state = AgentState(goal="g", retrieved_evidence_ids={"EID_a1"},
        result=AnalysisResult(title="T",
            evidence=[Evidence(id="EID_a1")],
            conclusions=[Conclusion(point="p", evidence_ids=["EID_a1"])]))
    critic = Critic(_llm(False), EvidenceVerifier())
    r = await critic.critique(state)
    assert r.passed is False