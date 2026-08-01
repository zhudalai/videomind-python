"""Executor 跨多视频真实检索 + 来源标注 + 幻觉过滤 + 零命中 测试."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

UUID_A = "00000000-0000-0000-0000-000000000001"
UUID_B = "00000000-0000-0000-0000-000000000002"


def _hit(eid: str, content: str, source_type: str = "asr") -> dict:
    return {"id": eid, "chunk_id": "c_" + eid, "content": content,
            "source_type": source_type, "score": 0.9,
            "start_ms": 0, "end_ms": 1000}


def _state(media_ids, videos, plan_tasks):
    from videomind.core.agent_loop.types import AgentState, AgentPlan, VideoMeta, SubTask
    return AgentState(
        goal="g",
        media_ids=media_ids,
        video_meta=[VideoMeta(media_id=mid, filename=name) for mid, name in videos],
        plan=AgentPlan(tasks=[SubTask(id="t1", description="d",
                                      required_evidence_type="text", search_query="q")
                              for _ in range(plan_tasks)], reasoning="r"),
    )


@pytest.mark.asyncio
async def test_executor_cross_video_retrieval_tags_media_title() -> None:
    from videomind.core.agent_loop.executor import Executor

    mock_llm = AsyncMock(); mock_llm.chat = AsyncMock()
    mock_llm.chat.return_value.content = json.dumps({
        "title": "T",
        "conclusions": [{"point": "p", "evidence_ids": ["EID_a1"], "confidence": 0.8}],
        "suggestions": [],
    })
    mock_retriever = AsyncMock()
    mock_retriever.search = AsyncMock(side_effect=[
        [_hit("EID_a1", "a1", "asr")],
        [_hit("EID_b1", "b1", "ocr")],
    ])
    executor = Executor(mock_llm, retriever=mock_retriever)
    state = _state([UUID_A, UUID_B], [(UUID_A, "a.mp4"), (UUID_B, "b.mp4")], 1)
    result = await executor.execute(state)

    assert len(result.evidence) == 2
    assert {e.media_title for e in result.evidence} == {"a.mp4", "b.mp4"}
    assert {e.media_id for e in result.evidence} == {UUID_A, UUID_B}
    assert result.conclusions[0].evidence_ids == ["EID_a1"]
    assert state.retrieved_evidence_ids == {"EID_a1", "EID_b1"}


@pytest.mark.asyncio
async def test_executor_filters_hallucinated_eids() -> None:
    from videomind.core.agent_loop.executor import Executor

    mock_llm = AsyncMock(); mock_llm.chat = AsyncMock()
    mock_llm.chat.return_value.content = json.dumps({
        "title": "T",
        "conclusions": [
            {"point": "real", "evidence_ids": ["EID_a1"], "confidence": 0.8},
            {"point": "fake", "evidence_ids": ["EID_FAKE_99"], "confidence": 0.5},
        ],
        "suggestions": [],
    })
    mock_retriever = AsyncMock()
    mock_retriever.search = AsyncMock(return_value=[_hit("EID_a1", "a1")])
    executor = Executor(mock_llm, retriever=mock_retriever)
    state = _state([UUID_A], [(UUID_A, "a.mp4")], 1)
    result = await executor.execute(state)

    assert len(result.conclusions) == 1
    assert result.conclusions[0].point == "real"


@pytest.mark.asyncio
async def test_executor_zero_hits_does_not_crash() -> None:
    from videomind.core.agent_loop.executor import Executor

    mock_retriever = AsyncMock()
    mock_retriever.search = AsyncMock(return_value=[])
    executor = Executor(AsyncMock().chat and AsyncMock(), retriever=mock_retriever)
    # chat 不设 return_value -> MagicMock -> json.loads 抛错 -> continue
    executor._llm = AsyncMock(); executor._llm.chat = AsyncMock()
    state = _state([UUID_A], [(UUID_A, "a.mp4")], 1)
    result = await executor.execute(state)

    assert len(result.evidence) == 0
    assert len(result.conclusions) == 0