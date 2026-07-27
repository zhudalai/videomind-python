"""Agent 分析路由 —— 目标驱动多轮分析入口。

POST /api/agent/analyze                    → 发起分析任务（异步运行）
GET  /api/agent/tasks/{task_id}             → 查询任务状态
GET  /api/agent/tasks/{task_id}/result      → 获取最终结构化结果
GET  /api/agent/tasks/{task_id}/checkpoints → 获取断点恢复数据（每轮每阶段）

后端复用既有 core/agent_loop（Planner→Executor→Critic 闭环）+ 持久化模型
AnalysisTask / AgentCheckpoint / AgentResult（见 infrastructure/storage/models.py）。

运行模型：FastAPI BackgroundTasks 在同一进程内异步跑 AgentLoop，边跑边更新
AnalysisTask 行（status / current_round / final_result_json / started_at /
completed_at / error_message），并写 AgentCheckpoint 与最终 AgentResult。
前端用 task_id 轮询状态机直到 completed/failed。不依赖额外 Celery worker，
足以端到端跑通；后续可平迁到 Celery 而不动接口。
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from videomind.infrastructure.storage import models as m
from videomind.infrastructure.storage.database import AsyncSessionLocal, get_db

router = APIRouter(tags=["agent"], prefix="/agent")


# ──────────────────────────── Pydantic Schema ────────────────────────────


class AnalyzeRequest(BaseModel):
    """发起 Agent 分析任务。"""

    goal: str = Field(..., min_length=1, max_length=5000, description="分析目标")
    media_id: uuid.UUID = Field(..., description="目标视频 ID（分析对象）")
    user_id: uuid.UUID = Field(..., description="发起用户 ID（auth 实现前由前端传入）")
    max_rounds: int = Field(2, ge=1, le=5, description="Critique 不通过时的最大轮数")


class AnalyzeResponse(BaseModel):
    task_id: uuid.UUID
    status: str
    goal: str
    max_rounds: int
    created_at: datetime


class TaskStatusResponse(BaseModel):
    task_id: uuid.UUID
    status: str
    current_round: int
    max_rounds: int
    goal: str
    final_result_json: dict | None
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class AgentResultResponse(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID
    title: str
    conclusions_json: list[Any]
    evidence_json: list[Any]
    suggestions_json: list[Any] | None
    critic_passed: bool
    critic_feedback: str | None
    total_rounds: int
    token_usage: dict | None
    cost_usd: float | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ──────────────────────────── Routes ────────────────────────────


@router.post("/analyze", response_model=AnalyzeResponse, status_code=202)
async def analyze(req: AnalyzeRequest, background_tasks: BackgroundTasks) -> AnalyzeResponse:
    """发起 Agent 分析任务，立即返回 task_id；后台异步运行 AgentLoop。

    幂等：同 (media_id, goal_hash) 已有进行中/已完成任务时直接复用，避免重复启跑。
    """
    goal_hash = hashlib.sha256(req.goal.encode()).hexdigest()

    async with AsyncSessionLocal() as session:
        # 校验 media 存在
        media = await session.get(m.MediaFile, req.media_id)
        if not media:
            raise HTTPException(status_code=404, detail="media not found")

        # 幂等：复用未失败的同目标任务
        existing = await session.execute(
            select(m.AnalysisTask)
            .where(
                m.AnalysisTask.media_id == req.media_id,
                m.AnalysisTask.goal_hash == goal_hash,
                m.AnalysisTask.status.notin_(["failed"]),
            )
            .order_by(m.AnalysisTask.created_at.desc())
            .limit(1)
        )
        task = existing.scalar_one_or_none()
        if task is None:
            task = m.AnalysisTask(
                user_id=req.user_id,
                media_id=req.media_id,
                goal=req.goal,
                goal_hash=goal_hash,
                status="pending",
                max_rounds=req.max_rounds,
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)

        # 仅新任务才调度后台运行
        if task.status == "pending":
            background_tasks.add_task(
                _run_agent_loop,
                task_id=task.id,
                goal=req.goal,
                media_id=req.media_id,
                max_rounds=req.max_rounds,
            )

        return AnalyzeResponse(
            task_id=task.id,
            status=task.status,
            goal=task.goal,
            max_rounds=task.max_rounds,
            created_at=task.created_at,
        )


@router.get("/tasks/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> TaskStatusResponse:
    task = await db.get(m.AnalysisTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    return TaskStatusResponse(
        task_id=task.id,
        status=task.status,
        current_round=task.current_round,
        max_rounds=task.max_rounds,
        goal=task.goal,
        final_result_json=task.final_result_json,
        error_message=task.error_message,
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
    )


@router.get("/tasks/{task_id}/result", response_model=AgentResultResponse)
async def get_task_result(task_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> AgentResultResponse:
    task = await db.get(m.AnalysisTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    if task.status != "completed":
        raise HTTPException(status_code=409, detail=f"task not completed (status={task.status})")

    result = await db.execute(
        select(m.AgentResult).where(m.AgentResult.task_id == task_id).limit(1)
    )
    agent_result = result.scalar_one_or_none()
    if not agent_result:
        raise HTTPException(status_code=404, detail="result not found")
    return AgentResultResponse.model_validate(agent_result)


@router.get("/tasks/{task_id}/checkpoints", response_model=list[dict])
async def get_task_checkpoints(task_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> list[dict]:
    task = await db.get(m.AnalysisTask, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")

    result = await db.execute(
        select(m.AgentCheckpoint)
        .where(m.AgentCheckpoint.task_id == task_id)
        .order_by(m.AgentCheckpoint.created_at)
    )
    checkpoints = result.scalars().all()
    return [
        {
            "id": str(c.id),
            "round": c.round,
            "phase": c.phase,
            "trace_id": c.trace_id,
            "agent_state_json": c.agent_state_json,
            "plan_json": c.plan_json,
            "critique_json": c.critique_json,
            "result_json": c.result_json,
            "feedback": c.feedback,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in checkpoints
    ]


# ──────────────────────────── 后台运行器 ────────────────────────────


async def _run_agent_loop(
    *,
    task_id: uuid.UUID,
    goal: str,
    media_id: uuid.UUID,
    max_rounds: int,
) -> None:
    """后台协程：跑 AgentLoop 并把状态/结果/断点写回 DB。

    每个阶段（planning/executing/critic_check）落一条 AgentCheckpoint；
    Critique 通过即落 AgentResult 并把 status 置 completed。任意异常 → status=failed。
    """
    from videomind.core.agent_loop.factory import get_agent_loop
    from videomind.core.agent_loop.types import AgentState

    try:
        # 取 LLM 单例（model_gateway 装配）；缺失/失败不影响任务记录，仅导致分析无果。
        loop = get_agent_loop()

        async with AsyncSessionLocal() as session:
            task = await session.get(m.AnalysisTask, task_id)
            assert task is not None
            task.status = "planning"
            task.started_at = datetime.now(timezone.utc)
            await session.commit()

        # AgentLoop 内部已按 max_rounds=2 跑闭环；这里用入口 state 透传 media 上下文。
        # 注意：core.loop 的 MAX_ROUNDS=2 为硬编码上限，与请求 max_rounds 取较小者。
        state = AgentState(goal=goal)
        effective_rounds = min(max_rounds, 2)
        for round_num in range(1, effective_rounds + 1):
            state.round = round_num
            # planning
            async with AsyncSessionLocal() as session:
                t = await session.get(m.AnalysisTask, task_id)
                if t:
                    t.status = "planning"
                    t.current_round = round_num
                    await session.commit()
            state.plan = await loop._planner.plan(state)
            await _checkpoint(task_id, round_num, "planning", state)

            # executing
            async with AsyncSessionLocal() as session:
                t = await session.get(m.AnalysisTask, task_id)
                if t:
                    t.status = "executing"
                    await session.commit()
            state.result = await loop._executor.execute(state)
            await _checkpoint(task_id, round_num, "executing", state)

            # critic_check（critique 算完再落断点，critique_json 非空）
            async with AsyncSessionLocal() as session:
                t = await session.get(m.AnalysisTask, task_id)
                if t:
                    t.status = "critic_check"
                    await session.commit()
            state.critique = await loop._critic.critique(state)
            await _checkpoint(task_id, round_num, "critic_check", state)

            if state.critique.passed:
                break

        result = state.result if state.result else None

        async with AsyncSessionLocal() as session:
            t = await session.get(m.AnalysisTask, task_id)
            assert t is not None
            if result and result.title:
                # 落 AgentResult
                ar = m.AgentResult(
                    task_id=task_id,
                    title=result.title,
                    conclusions_json=[_dataclass_to_dict(c) for c in result.conclusions],
                    evidence_json=[_dataclass_to_dict(e) for e in result.evidence],
                    suggestions_json=list(result.suggestions) if result.suggestions else None,
                    critic_passed=bool(state.critique.passed) if state.critique else False,
                    critic_feedback=state.critique.feedback if state.critique else None,
                    total_rounds=state.round,
                    token_usage=None,
                )
                session.add(ar)
                t.final_result_json = {
                    "title": result.title,
                    "conclusions": len(result.conclusions),
                    "evidence": len(result.evidence),
                    "suggestions": len(result.suggestions),
                }
                t.status = "completed"
            else:
                t.status = "failed"
                t.error_message = "AgentLoop 未产生有效结果（空 AnalysisResult）"
            t.completed_at = datetime.now(timezone.utc)
            await session.commit()

    except Exception as exc:  # noqa: BLE001 —— 后台运行器需捕获一切以保证状态落地
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
