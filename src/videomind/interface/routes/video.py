"""视频提交路由 —— 入库管线入口。

POST /api/videos/upload              → 本地文件上传（multipart/form-data, field=file）
POST /api/videos/pipeline            → 提交视频 URL
GET  /api/videos/pipeline/{id}       → 查询入库状态
GET  /api/videos/pipeline/{id}/progress → SSE 推送进度
GET  /api/videos                     → 视频列表（分页、搜索、筛选）
GET  /api/videos/{id}                → 视频详情
DELETE /api/videos/{id}              → 删除视频
GET  /api/videos/{id}/transcription  → 转录详情
GET  /api/videos/{id}/segments       → 视频片段（分页）
GET  /api/videos/{id}/ocr            → OCR 结果（分页）

对应 docs/VIDEO-PIPELINE.md + docs/TASK-ORCHESTRATION.md。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from pydantic import BaseModel, Field
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from videomind.config import get_settings
from videomind.infrastructure.media.minio import get_minio_client
from videomind.infrastructure.storage.database import (
    AsyncSessionLocal,
    get_db,
)
from videomind.infrastructure.storage import models as m

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


class UploadResponse(BaseModel):
    """上传接口响应：含 media_id / content_hash / 落库 status / 文件大小。

    frontend/lib/api.ts 的 uploadFile() 期望这个 shape；导航到 /videos/{media_id}/progress 即可。
    """

    media_id: uuid.UUID
    content_hash: str
    status: str
    size: int


# ──────────────────────────── Routes ────────────────────────────


@router.post(
    "/upload",
    response_model=UploadResponse,
    status_code=202,
    summary="上传本地视频文件到入库管线",
)
async def upload_video_file(
    file: UploadFile = File(..., description="视频文件，multipart/form-data field='file'"),
) -> Any:
    """上传本地视频 → MinIO → 创建 media_file（status=pending） → 触发 pipeline_task。

    行为：
      1) 流式读上传字节，计算 SHA256 → content_hash
      2) upload_bytes 到 MinIO:  bucket=`videos/<hash>/original.<ext>`
      3) create_media_file_from_upload（content_hash 幂等：同字节二次上传返回同 media_id）
      4) 分发 pipeline_task(media_id, source_url='', skip_download=True,
         content_hash, minio_bucket, minio_object, source_type='upload')

    限制：单文件 max 2 GiB（FastAPI 默认 spool 阈值兜底）；服务端具体 ephemeral 配置
    见 deployment 文档。
    """
    import hashlib
    import io
    import logging

    from videomind.infrastructure.storage.repository import (
        create_media_file_from_upload,
    )
    from videomind.application.task_orchestration.tasks import pipeline_task

    log = logging.getLogger(__name__)
    settings = get_settings()
    bucket = settings.minio_bucket

    # 读完整内容到内存（生产环境可换分块 + SpooledTemporaryFile；2GB 内测试够用）
    content = await file.read()
    size = len(content)
    if size == 0:
        raise HTTPException(status_code=400, detail="上传文件为空")

    content_hash = hashlib.sha256(content).hexdigest()

    # 文件扩展：从 filename 推断；缺失/异常时取 .mp4 兜底
    raw_name = (file.filename or "upload.mp4").strip()
    safe_name = raw_name.replace("/", "_").replace("\\", "_")[:255] or "upload.mp4"
    if "." in safe_name:
        ext = safe_name.rsplit(".", 1)[-1].lower()
    else:
        ext = "mp4"
    if not ext.isalnum():
        ext = "mp4"

    object_key = f"videos/{content_hash}/original.{ext}"
    mime = file.content_type or "video/mp4"

    # 1) 推到 MinIO（幂等：同 content_hash 同 object_key，重复上传是覆盖语义，bytes 一致）
    try:
        minio = get_minio_client()
        await minio.ensure_bucket()
        await minio.upload_bytes(object_key, content, mime)
    except Exception as e:
        log.exception("upload_bytes failed: %s", e)
        raise HTTPException(
            status_code=502,
            detail=f"MinIO upload failed: {type(e).__name__}",
        ) from e

    # 2) 落库 media_file（幂等 by content_hash）
    try:
        async with AsyncSessionLocal() as session:
            media = await create_media_file_from_upload(
                session,
                content_hash=content_hash,
                filename=safe_name,
                mime_type=mime,
                file_size=size,
                bucket=bucket,
                object_key=object_key,
            )
            await session.commit()
    except Exception as e:
        log.exception("create_media_file_from_upload failed: %s", e)
        # 补偿：清理刚上传的对象避免遗留垃圾
        try:
            await minio.delete(object_key)
        except Exception:
            pass
        raise HTTPException(
            status_code=500, detail=f"DB upsert failed: {type(e).__name__}"
        ) from e

    # 3) 触发管线：skip_download=True → download_video_task 走 MinIO materialize 分支
    context = {
        "media_id": str(media.id),
        "source_url": "",
        "user_id": None,
        "skip_download": True,
        "source_type": "upload",
        "content_hash": content_hash,
        "minio_bucket": bucket,
        "minio_object": object_key,
        "upload_filename": safe_name,
    }
    try:
        pipeline_task.apply_async(args=[context], queue="cpu")
    except Exception as e:
        log.warning(
            "Celery dispatch 失败，media=%s 已落库但未入队；前端仍可轮询 status: %s",
            media.id,
            e,
        )
        # 不回滚 media_file，让前端从 progress 页可看到 pending 状态、由监控/manual 重发

    return {
        "media_id": media.id,
        "content_hash": content_hash,
        "status": media.status,
        "size": size,
    }


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


# ──────────────────────────── Video Library Schemas ────────────────────────────


class VideoListRequest(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)
    search: str | None = None
    status: str | None = None
    user_id: uuid.UUID | None = None


class VideoListResponse(BaseModel):
    items: list["MediaFileResponse"]
    total: int
    page: int
    page_size: int
    total_pages: int

    model_config = {"from_attributes": True}


from datetime import datetime


class MediaFileResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID | None
    source_type: str
    source_url: str | None
    filename: str
    mime_type: str
    file_size: int
    duration_ms: int | None
    width: int | None
    height: int | None
    fps: float | None
    status: str
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None

    model_config = {"from_attributes": True}


class VideoDetailResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID | None
    source_type: str
    source_url: str | None
    filename: str
    mime_type: str
    file_size: int
    duration_ms: int | None
    width: int | None
    height: int | None
    fps: float | None
    status: str
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    # Related data (loaded when status=ready)
    transcription: "TranscriptionResponse | None" = None
    segments: "SegmentsListResponse | None" = None
    ocr_results: "OcrResultsListResponse | None" = None

    model_config = {"from_attributes": True}


class TranscriptionResponse(BaseModel):
    id: uuid.UUID
    media_id: uuid.UUID
    full_text: str
    language: str
    model_name: str
    duration_sec: float | None
    chunk_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class VideoSegmentResponse(BaseModel):
    id: uuid.UUID
    segment_index: int
    start_ms: int
    end_ms: int
    transcript: str | None
    ocr_texts: list[str] | None
    evidence_frames: list | None
    token_count: int
    confidence: float | None
    speaker: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class SegmentsListResponse(BaseModel):
    items: list[VideoSegmentResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


class OCRResultResponse(BaseModel):
    """对齐 infrastructure/storage/models.py FrameOCR 真实列集合。

    旧 schema 虚构了 frame_index / timestamp_ms / confidence / bbox，这些列在
    FrameORM 上并不存在，且 ocr_text 写成必填 str —— 但 OCR 对「无文字帧」是合法
    产出。结果导信 ready + 有 frame_ocr 行时 model_validate 抛 ValidationError，
    把整个 GET /videos/{id} 详情带成 500，连累转录 tab 不渲染。
    """

    id: uuid.UUID
    media_id: uuid.UUID
    frame_ms: int               # 关键帧时间戳（毫秒）
    minio_object: str           # 关键帧 MinIO 对象 key
    ocr_text: str | None        # None = 该帧未识别到文字（合法）
    phash: str | None           # 感知哈希（去重用）
    model_name: str | None      # paddle-ocr 等
    status: str                 # pending / completed / failed
    created_at: datetime

    model_config = {"from_attributes": True}


class OcrResultsListResponse(BaseModel):
    items: list[OCRResultResponse]
    total: int
    page: int
    page_size: int
    has_more: bool


# ──────────────────────────── Routes ────────────────────────────


@router.get("", response_model=VideoListResponse)
async def list_videos(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    status: str | None = Query(None),
    user_id: uuid.UUID | None = Query(None),
    db: AsyncSession = Depends(get_db),
) -> VideoListResponse:
    """获取视频列表（分页、搜索、筛选）。"""
    # Build base query
    query = select(m.MediaFile)
    count_query = select(func.count(m.MediaFile.id))

    # Apply filters
    conditions = []
    if search:
        conditions.append(
            or_(
                m.MediaFile.filename.ilike(f"%{search}%"),
                m.MediaFile.source_url.ilike(f"%{search}%"),
            )
        )
    if status:
        conditions.append(m.MediaFile.status == status)
    if user_id:
        conditions.append(m.MediaFile.user_id == user_id)

    if conditions:
        from sqlalchemy import and_
        query = query.where(and_(*conditions))
        count_query = count_query.where(and_(*conditions))

    # Get total count
    total_result = await db.execute(count_query)
    total = total_result.scalar_one()

    # Apply pagination and ordering
    query = query.order_by(m.MediaFile.created_at.desc()).offset((page - 1) * page_size).limit(page_size)

    # Execute
    result = await db.execute(query)
    items = result.scalars().all()

    # Convert to response
    video_items = [
        MediaFileResponse.model_validate(item) for item in items
    ]

    return VideoListResponse(
        items=video_items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    )


@router.get("/{media_id}", response_model=VideoDetailResponse)
async def get_video(
    media_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> VideoDetailResponse:
    """获取视频详情，包含关联数据（当 status=ready 时）。"""
    media = await db.get(m.MediaFile, media_id)
    if not media:
        raise HTTPException(status_code=404, detail="video not found")

    # Build base response
    response = VideoDetailResponse.model_validate(media)

    # Load related data if ready
    if media.status == "ready":
        # Transcription
        trans_result = await db.execute(
            select(m.Transcription).where(m.Transcription.media_id == media_id)
        )
        transcription = trans_result.scalar_one_or_none()
        if transcription:
            response.transcription = TranscriptionResponse.model_validate(transcription)

        # Segments (first page only for detail)
        seg_result = await db.execute(
            select(m.VideoSegment)
            .where(m.VideoSegment.media_id == media_id)
            .order_by(m.VideoSegment.segment_index)
            .limit(50)
        )
        segments = seg_result.scalars().all()
        if segments:
            response.segments = SegmentsListResponse(
                items=[VideoSegmentResponse.model_validate(s) for s in segments],
                total=len(segments),
                page=1,
                page_size=50,
                has_more=len(segments) >= 50,
            )

        # OCR results (first page only for detail)
        ocr_result = await db.execute(
            select(m.FrameOCR)
            .where(m.FrameOCR.media_id == media_id)
            .order_by(m.FrameOCR.frame_ms)
            .limit(50)
        )
        ocr_items = ocr_result.scalars().all()
        if ocr_items:
            response.ocr_results = OcrResultsListResponse(
                items=[OCRResultResponse.model_validate(o) for o in ocr_items],
                total=len(ocr_items),
                page=1,
                page_size=50,
                has_more=len(ocr_items) >= 50,
            )

    return response


@router.delete("/{media_id}", status_code=204)
async def delete_video(
    media_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    """删除视频及其所有关联数据（级联删除由 DB 约束处理）。"""
    media = await db.get(m.MediaFile, media_id)
    if not media:
        raise HTTPException(status_code=404, detail="video not found")

    await db.delete(media)
    await db.commit()
    return Response(status_code=204)


@router.get("/{media_id}/transcription", response_model=TranscriptionResponse)
async def get_transcription(
    media_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> TranscriptionResponse:
    """获取视频完整转录。"""
    # Verify media exists
    media = await db.get(m.MediaFile, media_id)
    if not media:
        raise HTTPException(status_code=404, detail="video not found")

    result = await db.execute(
        select(m.Transcription).where(m.Transcription.media_id == media_id)
    )
    transcription = result.scalar_one_or_none()
    if not transcription:
        raise HTTPException(status_code=404, detail="transcription not found")

    return TranscriptionResponse.model_validate(transcription)


@router.get("/{media_id}/segments", response_model=SegmentsListResponse)
async def get_segments(
    media_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> SegmentsListResponse:
    """获取视频片段列表（分页）。"""
    # Verify media exists
    media = await db.get(m.MediaFile, media_id)
    if not media:
        raise HTTPException(status_code=404, detail="video not found")

    # Count total
    count_result = await db.execute(
        select(func.count(m.VideoSegment.id)).where(m.VideoSegment.media_id == media_id)
    )
    total = count_result.scalar_one()

    # Get page
    result = await db.execute(
        select(m.VideoSegment)
        .where(m.VideoSegment.media_id == media_id)
        .order_by(m.VideoSegment.segment_index)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    segments = result.scalars().all()

    return SegmentsListResponse(
        items=[VideoSegmentResponse.model_validate(s) for s in segments],
        total=total,
        page=page,
        page_size=page_size,
        has_more=page * page_size < total,
    )


@router.get("/{media_id}/ocr", response_model=OcrResultsListResponse)
async def get_ocr_results(
    media_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> OcrResultsListResponse:
    """获取 OCR 识别结果（分页）。"""
    # Verify media exists
    media = await db.get(m.MediaFile, media_id)
    if not media:
        raise HTTPException(status_code=404, detail="video not found")

    # Count total
    count_result = await db.execute(
        select(func.count(m.FrameOCR.id)).where(m.FrameOCR.media_id == media_id)
    )
    total = count_result.scalar_one()

    # Get page
    result = await db.execute(
        select(m.FrameOCR)
        .where(m.FrameOCR.media_id == media_id)
        .order_by(m.FrameOCR.frame_ms)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    ocr_items = result.scalars().all()

    return OcrResultsListResponse(
        items=[OCRResultResponse.model_validate(o) for o in ocr_items],
        total=total,
        page=page,
        page_size=page_size,
        has_more=page * page_size < total,
    )