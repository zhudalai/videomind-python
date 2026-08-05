"""Executor 证据执行器测试."""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


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
    mock_db = AsyncMock(spec=AsyncSession)
    result = await executor.execute(state, mock_db)

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
    mock_db = AsyncMock(spec=AsyncSession)
    result = await executor.execute(state, mock_db)

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
    mock_db = AsyncMock(spec=AsyncSession)
    result = await executor.execute(state, mock_db)

    # 优雅降级，不抛异常
    assert len(result.conclusions) == 0
    assert len(result.suggestions) == 0


# ---------------------------------------------------------------------------
# P2-5 D-α：召回水位分层常量 + _RagRetriever 透传
# ---------------------------------------------------------------------------


def test_executor_recall_depth_constants():
    """RECALL_TOP_K=60 / LLM_CONTEXT_TOP_K=12 符合 D-α 设计。"""
    from videomind.core.agent_loop.executor import RECALL_TOP_K, LLM_CONTEXT_TOP_K

    assert RECALL_TOP_K == 60
    assert LLM_CONTEXT_TOP_K == 12


@pytest.mark.asyncio
async def test_executor_passes_recall_depth_to_retriever():
    """executor.execute 调用 _retriever.search 时传 top_k=LLM_CONTEXT_TOP_K, recall_k=RECALL_TOP_K。"""
    from videomind.core.agent_loop.executor import Executor, RECALL_TOP_K, LLM_CONTEXT_TOP_K
    from videomind.core.agent_loop.types import AgentState, AgentPlan, SubTask, VideoMeta

    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock()
    mock_llm.chat.return_value.content = json.dumps({"title": "T", "conclusions": [], "suggestions": []})
    mock_retriever = AsyncMock()
    mock_retriever.search = AsyncMock(return_value=[])
    MID = "00000000-0000-0000-0000-000000000001"
    executor = Executor(mock_llm, retriever=mock_retriever)
    state = AgentState(
        goal="g",
        media_ids=[MID],
        video_meta=[VideoMeta(media_id=MID, filename="a.mp4")],
        plan=AgentPlan(
            tasks=[SubTask(id="task_1", description="d", required_evidence_type="text")],
            reasoning="r",
        ),
    )
    mock_db = AsyncMock(spec=AsyncSession)
    await executor.execute(state, mock_db)

    assert mock_retriever.search.await_count == 1
    kwargs = mock_retriever.search.call_args.kwargs
    assert kwargs["top_k"] == LLM_CONTEXT_TOP_K
    assert kwargs["recall_k"] == RECALL_TOP_K


@pytest.mark.asyncio
async def test_rag_retriever_forwards_recall_k(monkeypatch):
    """_RagRetriever.search 将 recall_k 透传给 pipeline.search。"""
    from videomind.core.agent_loop.executor import _RagRetriever

    mock_db = AsyncMock()

    class _FakeSession:
        async def __aenter__(self):
            return mock_db

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("videomind.infrastructure.storage.database.AsyncSessionLocal", _FakeSession)
    captured = {}

    async def fake_search(db, query, media_id, *, top_k, recall_k=None, **kw):
        captured["top_k"] = top_k
        captured["recall_k"] = recall_k
        return {"context": [], "evidence": [], "raw_hits": []}

    monkeypatch.setattr("videomind.core.rag.pipeline.search", fake_search)

    await _RagRetriever().search("q", uuid.uuid4(), top_k=12, recall_k=60)
    assert captured["top_k"] == 12
    assert captured["recall_k"] == 60