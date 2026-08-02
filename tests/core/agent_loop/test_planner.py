"""Planner 任务规划器测试."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from videomind.core.agent_loop.types import AgentState


@pytest.mark.asyncio
async def test_planner_parses_valid_json() -> None:
    """Planner 正确解析 LLM 返回的有效 JSON 并生成 AgentPlan."""
    from videomind.core.agent_loop.planner import Planner

    mock_response = Mock()
    mock_response.content = json.dumps(
        {
            "tasks": [
                {"description": "提取视频主题", "evidence_type": "text"},
                {
                    "description": "分析关键时间点事件",
                    "evidence_type": "audio",
                    "time_range": [10000, 30000],
                },
            ],
            "reasoning": "需要先了解总体内容再深入细节",
        }
    )

    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock(return_value=mock_response)

    planner = Planner(mock_llm)
    state = AgentState(goal="分析这个视频")
    mock_db = AsyncMock(spec=AsyncSession)
    plan = await planner.plan(state, mock_db)

    assert len(plan.tasks) == 2
    assert plan.reasoning == "需要先了解总体内容再深入细节"

    assert plan.tasks[0].id == "task_1"
    assert plan.tasks[0].description == "提取视频主题"
    assert plan.tasks[0].required_evidence_type == "text"
    assert plan.tasks[0].time_range_hint is None

    assert plan.tasks[1].id == "task_2"
    assert plan.tasks[1].description == "分析关键时间点事件"
    assert plan.tasks[1].required_evidence_type == "audio"
    assert plan.tasks[1].time_range_hint == (10000, 30000)


@pytest.mark.asyncio
async def test_planner_limit_five_tasks() -> None:
    """Planner 限制最多 5 个子任务，超出的丢弃."""
    from videomind.core.agent_loop.planner import Planner

    mock_response = Mock()
    mock_response.content = json.dumps(
        {
            "tasks": [{"description": f"task_{i}"} for i in range(8)],
            "reasoning": "太多子任务",
        }
    )

    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock(return_value=mock_response)

    planner = Planner(mock_llm)
    mock_db = AsyncMock(spec=AsyncSession)
    plan = await planner.plan(AgentState(goal="测试"), mock_db)

    assert len(plan.tasks) == 5
    assert plan.tasks[0].description == "task_0"
    assert plan.tasks[4].description == "task_4"


@pytest.mark.asyncio
async def test_planner_malformed_json_return_empty_plan() -> None:
    """LLM 返回非 JSON 时返回空 Plan，不抛出异常."""
    from videomind.core.agent_loop.planner import Planner

    mock_response = Mock()
    mock_response.content = "not json"

    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock(return_value=mock_response)

    planner = Planner(mock_llm)
    mock_db = AsyncMock(spec=AsyncSession)
    state = AgentState(goal="测试")
    plan = await planner.plan(state, mock_db)

    assert len(plan.tasks) == 0
    assert plan.reasoning == ""


@pytest.mark.asyncio
async def test_planner_extracts_search_query_and_reads_video_list() -> None:
    """Planner 把视频文件名塞进 prompt，并解析每子任务的 search_query."""
    from videomind.core.agent_loop.planner import Planner
    from videomind.core.agent_loop.types import AgentState, VideoMeta

    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock()
    mock_llm.chat.return_value.content = json.dumps({
        "tasks": [{"description": "找商业模式", "evidence_type": "text",
                   "search_query": "商业模式 价格 斜率"}],
        "reasoning": "拆一个子任务",
    })
    planner = Planner(mock_llm)
    state = AgentState(
        goal="分析视频",
        media_ids=["m1", "m2"],
        video_meta=[VideoMeta(media_id="m1", filename="a.mp4"),
                    VideoMeta(media_id="m2", filename="b.mp4")],
    )
    mock_db = AsyncMock(spec=AsyncSession)
    plan = await planner.plan(state, mock_db)

    assert plan.tasks[0].search_query == "商业模式 价格 斜率"
    # prompt 里含两个视频文件名
    sent = mock_llm.chat.call_args.args[0]
    sent_text = sent.messages[0]["content"]
    assert "a.mp4" in sent_text and "b.mp4" in sent_text