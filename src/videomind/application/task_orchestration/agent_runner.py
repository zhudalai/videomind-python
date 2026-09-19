"""Agent 分析运行体 —— Celery worker 进程内驱动 AgentLoop 的执行器。

对应方向 1.4（Agent 分析迁 Celery）：原 `routes/agent.py` 的 BackgroundTasks
运行体平迁至此（interface 层不再承载长任务执行——API 进程重启不丢任务、
重活不占 HTTP worker）。HTTP 接口与前端轮询不变：AnalysisTask 状态机持久化
在 PG，运行位置对客户端透明。

职责：跑 AgentLoop（Planner→Executor→Critic 闭环），边跑边更新
AnalysisTask 行（status / current_round / final_result_json / started_at /
completed_at / error_message），逐阶段落 AgentCheckpoint，Critic 通过落
AgentResult。任意异常 → status=failed 终态（业务层自兜底，Celery 无需重试）。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from videomind.infrastructure.storage import models as m
from videomind.infrastructure.storage.database import AsyncSessionLocal


async def _load_video_meta(media_ids: list[uuid.UUID]) -> list[Any]:
    """加载各 video 的 filename/duration_ms，供 Planner/Executor 上下文渲染来源."""
    from videomind.core.agent_loop.types import VideoMeta

    meta: list[VideoMeta] = []
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(m.MediaFile).where(m.MediaFile.id.in_(media_ids))
        )
        for media in result.scalars().all():
            meta.append(VideoMeta(
                media_id=str(media.id), filename=media.filename,
                duration_ms=media.duration_ms))
    return meta


async def run_agent_analysis(
    *,
    task_id: uuid.UUID,
    goal: str,
    media_ids: list[uuid.UUID],
    max_rounds: int,
) -> None:
    """跑 AgentLoop 并把状态/结果/断点写回 DB。

    每个阶段（planning/executing/critic_check）落一条 AgentCheckpoint；
    Critique 通过即落 AgentResult 并把 status 置 completed。任意异常 → status=failed。
    """
    from videomind.core.agent_loop.factory import get_agent_loop
    from videomind.core.agent_loop.types import AgentState

    try:
        loop = get_agent_loop()
        video_meta = await _load_video_meta(media_ids)

        async with AsyncSessionLocal() as session:
            task = await session.get(m.AnalysisTask, task_id)
            assert task is not None
            task.status = "planning"
            task.started_at = datetime.now(timezone.utc)
            await session.commit()

        state = AgentState(
            goal=goal,
            media_ids=[str(x) for x in media_ids],
            video_meta=video_meta,
        )

        # 使用单一数据库会话贯穿整个 AgentLoop
        async with AsyncSessionLocal() as session:
            for round_num in range(1, max_rounds + 1):
                state.round = round_num
                t = await session.get(m.AnalysisTask, task_id)
                if t:
                    t.status = "planning"
                    t.current_round = round_num
                    await session.commit()
                state.plan = await loop._planner.plan(state, session)
                await _checkpoint(task_id, round_num, "planning", state)

                t = await session.get(m.AnalysisTask, task_id)
                if t:
                    t.status = "executing"
                    await session.commit()
                state.result = await loop._executor.execute(state, session)
                await _checkpoint(task_id, round_num, "executing", state)

                t = await session.get(m.AnalysisTask, task_id)
                if t:
                    t.status = "critic_check"
                    await session.commit()
                state.critique = await loop._critic.critique(state, session)
                await _checkpoint(task_id, round_num, "critic_check", state)

                if state.critique.passed:
                    break

            result = state.result
            t = await session.get(m.AnalysisTask, task_id)
            assert t is not None
            if result and result.title:
                ar = m.AgentResult(
                    task_id=task_id, title=result.title,
                    conclusions_json=[_dataclass_to_dict(c) for c in result.conclusions],
                    evidence_json=[_dataclass_to_dict(e) for e in result.evidence],
                    suggestions_json=list(result.suggestions) or None,
                    critic_passed=bool(state.critique and state.critique.passed),
                    critic_feedback=state.critique.feedback if state.critique else None,
                    total_rounds=state.round, token_usage=None,
                )
                session.add(ar)
                t.final_result_json = {"title": result.title,
                    "conclusions": len(result.conclusions), "evidence": len(result.evidence),
                    "suggestions": len(result.suggestions)}
                t.status = "completed"
            else:
                t.status = "failed"
                t.error_message = "AgentLoop 未产生有效结果（空 AnalysisResult / 零命中）"
            t.completed_at = datetime.now(timezone.utc)
            await session.commit()

    except Exception as exc:  # noqa: BLE001
        async with AsyncSessionLocal() as session:
            t = await session.get(m.AnalysisTask, task_id)
            if t:
                t.status = "failed"
                t.error_message = f"{type(exc).__name__}: {exc}"
                t.completed_at = datetime.now(timezone.utc)
                await session.commit()


async def _checkpoint(
    task_id: uuid.UUID,
    round_num: int,
    phase: str,
    state: Any,
) -> None:
    """落一条 AgentCheckpoint（每轮每阶段各一次）。

    uq_acp_task_round_phase 唯一约束保证同一 (task, round, phase) 不重复；
    本调用方已按 planning→executing→critic_check 各写一次，无冲突。
    """
    try:
        async with AsyncSessionLocal() as session:
            cp = m.AgentCheckpoint(
                task_id=task_id,
                round=round_num,
                phase=phase,
                agent_state_json={
                    "goal": state.goal,
                    "round": state.round,
                    "has_plan": state.plan is not None,
                    "has_result": state.result is not None,
                    "has_critique": state.critique is not None,
                },
                video_context_ref={
                    "media_ids": list(state.media_ids),
                    "retrieved_evidence_count": len(state.retrieved_evidence_ids),
                    "round": state.round,
                },
                plan_json=_dataclass_to_dict(state.plan) if state.plan else None,
                critique_json=_dataclass_to_dict(state.critique) if state.critique else None,
                result_json=_dataclass_to_dict(state.result) if state.result else None,
                feedback=state.critique.feedback if state.critique else None,
            )
            session.add(cp)
            await session.commit()
    except Exception:
        # 断点写入失败不应阻断主循环
        pass


def _dataclass_to_dict(obj: Any) -> dict | None:
    """把 dataclass 转 dict（递归处理嵌套 dataclass / list）。"""
    if obj is None:
        return None
    if isinstance(obj, list):
        return [_dataclass_to_dict(x) for x in obj]
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _dataclass_to_dict(getattr(obj, k)) for k in obj.__dataclass_fields__}
    return obj


__all__ = ["run_agent_analysis"]
