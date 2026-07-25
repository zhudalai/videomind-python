"""视频管线进度 SSE 路由。

GET /api/videos/pipeline/{media_id}/progress  → SSE 流式推送进度

对应 docs/TASK-ORCHESTRATION.md SSE 进度推送 + docs/FRONTEND.md 前端订阅。
"""

from __future__ import annotations

import uuid
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sse_starlette import EventSourceResponse

from videomind.application.task_orchestration.broadcast import progress_stream

router = APIRouter(tags=["video"], prefix="/videos")


@router.get("/pipeline/{media_id}/progress")
async def pipeline_progress(
    media_id: uuid.UUID,
    request: Request,
) -> StreamingResponse:
    """SSE 端点：订阅指定视频的管线进度。

    前端用法：
        const evt = new EventSource(`/api/videos/pipeline/${mediaId}/progress`);
        evt.onmessage = (e) => {
            const {stage, progress_pct, message} = JSON.parse(e.data);
            // 更新 UI
        };
    """

    async def event_generator() -> AsyncGenerator[dict, None]:
        async for event in progress_stream(str(media_id)):
            # sse_starlette.EventSourceResponse 会自动加 `data: ` 前缀 + `\n\n`，
            # 这里只需 yield dict payload，否则会出现双前缀 `data: data: {...}`。
            yield {"event": "progress", "data": event.to_json()}
            # 完成/失败时断开
            if event.progress_pct >= 100 or event.progress_pct < 0:
                break

    return EventSourceResponse(event_generator())