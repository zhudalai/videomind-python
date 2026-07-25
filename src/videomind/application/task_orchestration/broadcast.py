"""SSE 进度广播器 —— Redis pub/sub 实现多 Worker 进度推送到 API。

对应 docs/TASK-ORCHESTRATION.md SSE 广播 + docs/ARCHITECTURE.md 接口层 SSE。
设计要点：
1. **频道**：`media:{media_id}:progress`（pub/sub）
2. **消息格式**：JSON `{stage, progress_pct, message, timestamp}`
3. **API 端**：FastAPI 路由订阅频道，yield SSE 事件
4. **Worker 端**：任务各阶段完成后 `broadcast_progress(media_id, stage, pct, msg)`

使用示例（Worker）:
    from videomind.application.task_orchestration.broadcast import broadcast_progress
    await broadcast_progress(media_id, "asr", 60, "ASR 完成")

使用示例（API）:
    from videomind.application.task_orchestration.broadcast import progress_stream
    async for event in progress_stream(media_id):
        yield f"data: {event}\\n\\n"
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, asdict
from typing import AsyncGenerator

from videomind.infrastructure.cache.redis import get_redis


@dataclass
class ProgressEvent:
    """进度事件（JSON 可序列化）。"""

    stage: str
    progress_pct: int
    message: str
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if self.timestamp == 0.0:
            self.timestamp = time.time()

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @staticmethod
    def from_json(data: str) -> "ProgressEvent":
        d = json.loads(data)
        return ProgressEvent(**d)


def _channel(media_id: str) -> str:
    return f"media:{media_id}:progress"


async def broadcast_progress(
    media_id: str,
    stage: str,
    progress_pct: int,
    message: str = "",
) -> int:
    """向频道发布进度事件。

    Returns:
        接收者数量（Redis PUBLISH 返回值）。
    """
    r = get_redis()
    event = ProgressEvent(stage=stage, progress_pct=progress_pct, message=message)
    return await r.publish(_channel(media_id), event.to_json())


async def progress_stream(media_id: str) -> AsyncGenerator[ProgressEvent, None]:
    """异步生成器：订阅频道并 yield 进度事件。

    用于 FastAPI SSE 路由：
        @router.get("/videos/{media_id}/progress")
        async def sse_progress(media_id: UUID):
            async def event_gen():
                async for evt in progress_stream(str(media_id)):
                    yield f"data: {evt.to_json()}\\n\\n"
            return StreamingResponse(event_gen(), media_type="text/event-stream")
    """
    r = get_redis()
    pubsub = r.pubsub()
    await pubsub.subscribe(_channel(media_id))

    try:
        async for msg in pubsub.listen():
            if msg["type"] == "message":
                yield ProgressEvent.from_json(msg["data"])
    finally:
        await pubsub.unsubscribe(_channel(media_id))
        await pubsub.aclose()


# 便捷：阶段到进度百分比映射（对应 docs/TASK-ORCHESTRATION.md 阶段序列）
STAGE_PROGRESS = {
    "claimed": 5,
    "downloading": 10,
    "downloaded": 20,
    "transcoding": 30,
    "transcoded": 40,
    "asr": 60,
    "ocr": 75,
    "indexing": 90,
    "completed": 100,
    "failed": -1,
}


def stage_to_progress(stage: str) -> int:
    """阶段名 → 进度百分比。未知阶段返回 0。"""
    return STAGE_PROGRESS.get(stage, 0)