"""Agent 分析路由 —— 目标驱动多轮分析入口（多视频）。

POST /api/agent/analyze                    → 发起分析任务（异步运行，支持多视频）
GET  /api/agent/tasks/{task_id}             → 查询任务状态
GET  /api/agent/tasks/{task_id}/result      → 获取最终结构化结果
GET  /api/agent/tasks/{task_id}/checkpoints → 获取断点恢复数据（每轮每阶段）

后端复用既有 core/agent_loop（Planner→Executor→Critic 闭环）+ 持久化模型
AnalysisTask / AgentCheckpoint / AgentResult（见 infrastructure/storage/models.py）。

运行模型（1.4 已迁 Celery）：dispatch agent_analyze_task 到 cpu 队列，worker 进程
内跑 agent_runner.run_agent_analysis，边跑边更新 AnalysisTask 行
（status / current_round / final_result_json / started_at / completed_at /
error_message），并写 AgentCheckpoint 与最终 AgentResult。前端用 task_id 轮询
状态机直到 completed/failed（接口/轮询与迁移前完全不变）。相比旧 BackgroundTasks
方案：API 进程重启不丢任务、重活不占 HTTP worker。运行体见
application/task_orchestration/agent_runner.py。
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from videomind.infrastructure.storage import models as m
from videomind.infrastructure.storage.database import AsyncSessionLocal, get_db

router = APIRouter(tags=["agent"], prefix="/agent")
log = logging.getLogger(__name__)


# ──────────────────────────── Pydantic Schema ────────────────────────────


class AnalyzeRequest(BaseModel):
    """发起 Agent 分析任务（多视频）。"""

    goal: str = Field(..., min_length=1, max_length=5000, description="分析目标")
    media_ids: list[uuid.UUID] = Field(
        ..., min_length=1, max_length=4,
        description="目标视频列表（≥1，≤4）。max_length=2 不够覆盖三方对比任务，"
                    "放宽至 4，匹配 Execution 并发检索不到 10ms 增量。")
    user_id: uuid.UUID = Field(..., description="发起用户 ID（auth 实现前由前端传入）")
    max_rounds: int = Field(2, ge=1, le=3,
        description="最大轮数；默认 2、上限 3。3 多一轮让 Critic 反馈能被真吸收，"
                    "适合跨视频对比任务")

    @field_validator("media_ids", mode="after")
    @classmethod
    def _unique_media_ids(cls, v: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(set(v)) != len(v):
            raise ValueError("media_ids must not contain duplicates")
        return v


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
    conclusions_json: list
    evidence_json: list
    suggestions_json: list | None
    critic_passed: bool
    critic_feedback: str | None
    total_rounds: int
    token_usage: dict | None
    cost_usd: float | None
    created_at: datetime

    model_config = {"from_attributes": True}


# ──────────────────────────── Routes ────────────────────────────


@router.post("/analyze", response_model=AnalyzeResponse, status_code=202)
async def analyze(req: AnalyzeRequest) -> AnalyzeResponse:
    """发起 Agent 分析任务，立即返回 task_id；Celery cpu 队列异步运行 AgentLoop。

    幂等：同 (media_ids_hash, goal_hash) 已有未失败任务时复用，不重复启跑。
    """
    goal_hash = hashlib.sha256(req.goal.encode()).hexdigest()
    media_ids_hash = hashlib.sha256(
        ",".join(sorted(str(x) for x in req.media_ids)).encode()
    ).hexdigest()

    async with AsyncSessionLocal() as session:
        # 逐个校验 media：存在 + ready（与 /rag 一致）
        for mid in req.media_ids:
            media = await session.get(m.MediaFile, mid)
            if not media:
                raise HTTPException(status_code=404, detail=f"media {mid} not found")
            if media.status != "ready":
                raise HTTPException(
                    status_code=409, detail=f"media {mid} 未就绪（status={media.status}）")

        # 幂等：复用未失败的同 (media_ids_hash, goal_hash) 任务
        existing = await session.execute(
            select(m.AnalysisTask)
            .where(
                m.AnalysisTask.media_ids_hash == media_ids_hash,
                m.AnalysisTask.goal_hash == goal_hash,
                m.AnalysisTask.status.notin_(["failed"]),
            )
            .order_by(m.AnalysisTask.created_at.desc())
            .limit(1)
        )
        task = existing.scalar_one_or_none()
        if task is None:
            primary = req.media_ids[0]
            task = m.AnalysisTask(
                user_id=req.user_id, media_id=primary, media_ids_hash=media_ids_hash,
                goal=req.goal, goal_hash=goal_hash, status="pending",
                max_rounds=req.max_rounds,
            )
            session.add(task)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                # 并发创建：重新查询已存在的任务
                existing = await session.execute(
                    select(m.AnalysisTask)
                    .where(
                        m.AnalysisTask.media_ids_hash == media_ids_hash,
                        m.AnalysisTask.goal_hash == goal_hash,
                        m.AnalysisTask.status.notin_(["failed"]),
                    )
                    .order_by(m.AnalysisTask.created_at.desc())
                    .limit(1)
                )
                task = existing.scalar_one_or_none()
                if task is None:
                    raise HTTPException(status_code=500, detail="race condition: task creation failed")
            else:
                await session.refresh(task)
                # 关联表：每条 media 一行，position=index
                for pos, mid in enumerate(req.media_ids):
                    session.add(m.AnalysisTaskMedia(
                        task_id=task.id, media_id=mid, position=pos))
                await session.commit()

        # 仅新任务才调度运行（1.4：BackgroundTasks → Celery cpu 队列；
        # uuid 一律转 str——Celery 参数走 JSON 序列化）
        if task.status == "pending":
            from videomind.application.task_orchestration.tasks import agent_analyze_task

            try:
                agent_analyze_task.apply_async(
                    args=[{
                        "task_id": str(task.id),
                        "goal": req.goal,
                        "media_ids": [str(x) for x in req.media_ids],
                        "max_rounds": req.max_rounds,
                    }],
                    queue="cpu",
                )
            except Exception as e:  # noqa: BLE001
                # dispatch 失败不回滚任务行：保留 pending 终态可由监控/手动重发
                # （与 video.py 管线 dispatch 的兜底策略一致）
                log.warning(
                    "Agent 任务 dispatch 失败，task=%s 已落库 pending 可手动重发: %s",
                    task.id, e,
                )

        return AnalyzeResponse(
            task_id=task.id, status=task.status, goal=task.goal,
            max_rounds=task.max_rounds, created_at=task.created_at,
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
