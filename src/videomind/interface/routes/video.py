"""视频提交路由 —— 入库管线入口。

POST /api/videos/pipeline        → 提交视频 URL / 上传文件
GET  /api/videos/pipeline/{id}   → 查询入库状态
GET  /api/videos/pipeline/{id}/progress → SSE 推送进度

对应 docs/VIDEO-PIPELINE.md + docs/TASK-ORCHESTRATION.md。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from videomind.infrastructure.storage.database import get_db

router = APIRouter(tags=["video"], prefix="/videos")


# ──────────────────────────── Pydantic Schema ────────────────────────────


class PipelineSubmitRequest(BaseModel):
    """提交视频到入库管线。"""

    source_url: str = Field(..., min_length=5, max_length=2048, description="视频 URL")
    user_id: uuid.UUID | None = Field(None, description="用户 ID（Auth 实现前可选）")


class PipelineSubmitResponse(BaseModel):
    """提交后返回的任务/媒体 ID。"""

    media_id: uuid.UUID
    status: str


class PipelineStatusResponse(BaseModel):
    """管线状态查询结果。"""

    media_id: uuid.UUID
    source_url: str | None
    status: str
    stage_progress: dict[str, str] = Field(default_factory=dict)
    error_message: str | None = None


# ──────────────────────────── Routes ────────────────────────────


@router.post("/pipeline", response_model=PipelineSubmitResponse, status_code=202)
async def submit_pipeline(req: PipelineSubmitRequest) -> dict[str, Any]:
    """提交视频 URL 进入入库管线。

    创建 media_file 记录（status=pending），然后分发 Celery pipeline_task 到 cpu 队列。
    """
    from videomind.infrastructure.storage.database import AsyncSessionLocal
    from videomind.infrastructure.storage.repository import create_media_file_pending
    from videomind.application.task_orchestration.tasks import pipeline_task

    async with AsyncSessionLocal() as session:
        media = await create_media_file_pending(
            session,
            source_url=req.source_url,
            user_id=req.user_id,
        )
        await session.commit()

        # Dispatch pipeline task to Celery
        context = {
            "media_id": str(media.id),
            "source_url": req.source_url,
            "user_id": str(req.user_id) if req.user_id else None,
        }
        pipeline_task.apply_async(args=[context], queue="cpu")

        return {"media_id": media.id, "status": media.status}


@router.get("/pipeline/{media_id}", response_model=PipelineStatusResponse)
async def get_pipeline_status(
    media_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Any:
    """查询指定视频的入库管线状态。"""
    from videomind.infrastructure.storage.repository import get_media_file

    media = await get_media_file(db, media_id)
    if not media:
        raise HTTPException(status_code=404, detail="media not found")
    return {
        "media_id": media.id,
        "source_url": media.source_url,
        "status": media.status,
        "stage_progress": {},
        "error_message": media.error_message,
    }