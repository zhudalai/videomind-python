"""Executor 证据执行器测试."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_executor_generates_analysis_result() -> None:
    """Executor 调用 LLM 并正确生成 AnalysisResult（命中真实检索证据）。"""
    from videomind.core.agent_loop.executor import Executor
    from videomind.core.agent_loop.types import AgentState, AgentPlan, SubTask, VideoMeta

    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock()
    mock_llm.chat.return_value.content = json.dumps({
        "title": "视频分析结果",
        "conclusions": [
            {"point": "视频主题是AI技术", "evidence_ids": ["EID_abc12345_01"], "confidence": 0.9},
            {"point": "主讲人介绍了三个要点", "evidence_ids": ["EID_abc12345_02"], "confidence": 0.85},
        ],
        "suggestions": ["想要展开第一点", "可以对比其他观点"],
    })
    mock_retriever = AsyncMock()
    mock_retriever.search = AsyncMock(return_value=[
        {"id": "EID_abc12345_01", "chunk_id": "c1", "content": "内容1", "source_type": "asr", "score": 0.9, "start_ms": 0, "end_ms": 1000},
        {"id": "EID_abc12345_02", "chunk_id": "c2", "content": "内容2", "source_type": "ocr", "score": 0.8, "start_ms": 0, "end_ms": 1000},
    ])
    MID = "00000000-0000-0000-0000-000000000001"
    executor = Executor(mock_llm, retriever=mock_retriever)
    state = AgentState(
        goal="分析视频内容",
        media_ids=[MID],
        video_meta=[VideoMeta(media_id=MID, filename="a.mp4")],
        plan=AgentPlan(tasks=[SubTask(id="task_1", description="提取主题",
                                      required_evidence_type="text")], reasoning="一个任务"),
    )
    result = await executor.execute(state)

    assert result.title == "视频分析结果"
    assert len(result.conclusions) == 2
    assert result.conclusions[0].confidence == 0.9
    assert len(result.suggestions) == 2


@pytest.mark.asyncio
async def test_executor_dedup_suggestions() -> None:
    """Executor 对多条任务的建议进行去重。"""
    from videomind.core.agent_loop.executor import Executor
    from videomind.core.agent_loop.types import AgentState, AgentPlan, SubTask

    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock()
    mock_llm.chat.side_effect = [
        type("Resp", (), {"content": json.dumps({
            "title": "T1", "conclusions": [],
            "suggestions": ["A", "B"],
        })})(),
        type("Resp", (), {"content": json.dumps({
            "title": "T2", "conclusions": [],
            "suggestions": ["B", "C"],
        })})(),
    ]

    executor = Executor(mock_llm)
    state = AgentState(
        goal="test",
        plan=AgentPlan(tasks=[
            SubTask(id="task1", description="t1", required_evidence_type="text"),
            SubTask(id="task2", description="t2", required_evidence_type="text"),
        ], reasoning=""),
    )
    result = await executor.execute(state)

    # 去重: A, B, C -> 3 条
    assert len(result.suggestions) == 3


@pytest.mark.asyncio
async def test_executor_malformed_json_graceful() -> None:
    """LLM 返回无效 JSON 时优雅降级，不抛异常。"""
    from videomind.core.agent_loop.executor import Executor
    from videomind.core.agent_loop.types import AgentState, AgentPlan, SubTask

    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock()
    mock_llm.chat.return_value.content = "not valid json"

    executor = Executor(mock_llm)
    state = AgentState(
        goal="test",
        plan=AgentPlan(tasks=[SubTask(id="t1", description="t1", required_evidence_type="text")], reasoning=""),
    )
    result = await executor.execute(state)

    # 优雅降级，不抛异常
    assert len(result.conclusions) == 0
    assert len(result.suggestions) == 0