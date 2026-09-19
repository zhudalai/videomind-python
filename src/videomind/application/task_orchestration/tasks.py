"""Celery 任务定义 —— 视频入库管线 5 个阶段任务 + 总管线任务。

对应 docs/VIDEO-PIPELINE.md §2 管线各阶段 + docs/TASK-ORCHESTRATION.md Celery 任务编排。
设计要点：
1. **阶段任务**：download → transcode → asr → ocr → index，各自独立可重试
2. **总管线任务**：pipeline_task 串联各阶段，维护 IngestionContext 状态上下文
3. **GPU 锁**：asr/ocr/index 用 `GPUResourceManager` 独占 GPU
4. **进度广播**：每阶段完成 `broadcast_progress(media_id, stage, pct)`
5. **幂等/断点续传**：每阶段先查 DB 状态，已完成直接跳过
"""

from __future__ import annotations

import shutil
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx
from celery import Celery, chain, group
from celery.utils.log import get_task_logger

from videomind.application.task_orchestration.celery import celery_app
from videomind.application.task_orchestration.gpu import get_gpu_manager
from videomind.application.task_orchestration.broadcast import (
    broadcast_progress,
    stage_to_progress,
)
from videomind.config import get_settings
from videomind.core.errors import NonRetryableError, RetryableError
from videomind.observability.instrumentation import trace_stage, traced_span


logger = get_task_logger(__name__)


async def _set_media_status(media_id_str: str, status: str) -> None:
    """把 media_file.status 落库到 PG，让 HTTP/SSE 轮询能看到推进。

    download→downloaded 由 download_video_task 直接写（附带元数据），
    其余 stage（transcode/asr/ocr）只更新 status；index→ready 由 Indexer.index 内部写。
    仅在向前推进时写，避免回退或覆盖终态（ready/failed）。
    失败终态不走这里（会被"只向前"守卫挡住）——见 _mark_media_failed。
    """
    from videomind.infrastructure.storage import models as m
    from videomind.infrastructure.storage.database import db_session

    _advance = {
        "pending": 0, "downloading": 1, "downloaded": 2,
        "transcoding": 3, "transcoded": 4,
        "asr": 5, "asr_done": 6,
        "ocr": 7, "ocr_done": 8, "indexing": 9, "ready": 10,
    }
    async with db_session() as db:
        media = await db.get(m.MediaFile, uuid.UUID(media_id_str))
        if media is None:
            return
        cur = _advance.get(media.status, 0)
        nxt = _advance.get(status, 0)
        # 只允许向前推进；ready/failed 后不再覆盖
        if nxt > cur and media.status not in ("ready", "failed"):
            media.status = status
            await db.commit()


async def _mark_media_failed(media_id_str: str, error_message: str, *, stage: str = "") -> None:
    """管线阶段最终失败（重试耗尽）→ 无条件写失败终态 + error_message。

    与 _set_media_status 的"只向前推进"守卫相反：failed 是终态，
    旧实现把它放进 _advance 映射（-1）导致 `nxt > cur` 永假、永远写不进去
    （死代码），media 卡在中间态、轮询端永远看不到失败原因。
    """
    from videomind.infrastructure.storage import models as m
    from videomind.infrastructure.storage.database import db_session

    async with db_session() as db:
        media = await db.get(m.MediaFile, uuid.UUID(media_id_str))
        if media is None or media.status in ("ready", "failed"):
            return
        media.status = "failed"
        media.error_message = f"[{stage}] {error_message}" if stage else error_message
        await db.commit()


def _cleanup_local_source(ctx: IngestionContext) -> None:
    """转码成功后清理本地原始视频（URL 下载目录 / 上传 materialize 工作目录）。

    为什么选在 transcode 成功点清：后续 asr/ocr/index 只从 MinIO 拿中间产物，
    原始视频本地使命已尽；而失败重试时本地文件还得在（重试直接复用，不重新下载），
    所以只在成功路径清。旧实现临时文件全不删（仓库曾积 2 个 144MB 残留）。
    防误删：仅当父目录名含 media_id[:8]（本媒体专属目录——download 子目录与
    上传 materialize 工作目录两条路径都满足）才整目录删除，否则只删文件本身。
    清理失败仅告警——磁盘残留可运维兜底，不该让已成功的转码任务回滚失败。
    """
    dl = ctx.download_result or {}
    local_path = dl.get("local_path")
    if not local_path:
        return
    p = Path(local_path)
    try:
        if not p.exists():
            return
        if ctx.media_id[:8] in p.parent.name:
            shutil.rmtree(p.parent, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)
    except OSError:
        logger.warning("transcode 后清理本地源文件失败（忽略）: %s", local_path)


# ──────────────────────────── 状态上下文 ────────────────────────────


@dataclass
class IngestionContext:
    """管线任务在各阶段间传递的上下文（JSON 可序列化）。"""

    media_id: str
    source_url: str
    user_id: str | None = None
    # 上传场景：skip_download=True 时，pipeline_task 直接进 transcode（绕开 yt-dlp），
    # materialize_local_for_upload_task 负责把 MinIO 上传文件拉到本地 transcode 工作目录。
    skip_download: bool = False
    source_type: str = "url"          # 'url' | 'upload'
    content_hash: str | None = None   # 上传场景必填
    minio_bucket: str | None = None   # 上传场景必填
    minio_object: str | None = None   # 上传场景必填
    upload_filename: str | None = None
    # 阶段产出
    download_result: dict[str, Any] | None = None  # {local_path, content_hash, meta}
    transcode_result: dict[str, Any] | None = None  # {audio_minio, keyframes_minio, meta}
    transcription_id: str | None = None
    asr_chunks_count: int = 0
    ocr_frames_count: int = 0
    chunk_count: int = 0
    # 运行时
    current_stage: str = "claimed"
    progress_pct: int = 5
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "IngestionContext":
        # Filter out unknown fields for forward compatibility
        import dataclasses
        known_fields = {f.name for f in dataclasses.fields(cls)}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)


# ──────────────────────────── 基础 Task 类 ────────────────────────────


class BaseVideoTask(celery_app.Task):
    """视频任务基类：统一异常处理、重试、进度广播。

    重试策略（二分法，见 videomind.core.errors）：
    - **只重试暂时性异常**：RetryableError + 标准库瞬态基类 + httpx 传输层故障。
      ConnectionError/TimeoutError 本身是 OSError 子类，显式列出以自文档。
    - **确定性失败不重试**：NonRetryableError / ValueError（契约违规、格式损坏）
      直接走 on_failure → 落库终态。旧实现 `autoretry_for=(Exception,)` 对 404
      视频、私享视频也盲重 3 次，纯浪费算力且拖慢"失败可见"时间。
    """

    # 重试策略
    autoretry_for = (
        RetryableError,
        ConnectionError,
        TimeoutError,
        OSError,
        httpx.TransportError,  # 传输层（拒连/DNS/读写超时）恒为暂时性
    )
    retry_backoff = True
    retry_backoff_max = 600
    retry_jitter = True

    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """最终失败（重试耗尽 / 不可重试异常）→ 落库终态 + 广播错误进度。

        旧实现只发 Redis pub/sub 不落库：worker 重启或 Redis 清空后失败状态丢失，
        media 永卡中间态，HTTP/SSE 轮询端永远看不到失败原因（bug）。
        """
        import anyio

        ctx = args[0] if args else {}
        media_id = ctx.get("media_id") if isinstance(ctx, dict) else None
        if not media_id:
            return

        stage = self.name.rsplit(".", 1)[-1]  # 例：download_video_task
        error_message = f"Task {self.name} failed: {exc}"

        async def _fail():
            # 先落库再广播：轮询端收到 pub/sub 时 DB 已有终态可对账
            await _mark_media_failed(media_id, error_message, stage=stage)
            await broadcast_progress(media_id, "failed", -1, error_message)

        try:
            anyio.run(_fail)
        except Exception:
            # 落库/广播自身失败不能掩盖原始任务异常（on_failure 抛错会盖住 exc）
            logger.exception("on_failure: 终态落库/广播失败 media_id=%s", media_id)

    def on_retry(self, exc, task_id, args, kwargs, einfo):
        """重试时广播重试进度。"""
        import anyio

        ctx = args[0] if args else {}
        media_id = ctx.get("media_id") if isinstance(ctx, dict) else None
        if media_id:
            anyio.run(
                broadcast_progress,
                media_id,
                "retry",
                stage_to_progress(ctx.get("current_stage", "retry")),
                f"Retrying: {exc}",
            )


# ──────────────────────────── 阶段任务 ────────────────────────────


@celery_app.task(
    bind=True,
    base=BaseVideoTask,
    name="videomind.tasks.download_video_task",
    max_retries=3,
    default_retry_delay=60,
)
def download_video_task(self, context: dict) -> dict:
    """阶段 1：把"待处理源视频"落到本地工作目录。

    两条分支：
      a) URL 路径（默认）：yt-dlp 拉视频 → MinIO → 写 ctx.download_result（带媒体元数据）
      b) 上传路径（skip_download=True）：把 MinIO 中已上传文件拉到本地 → 写 ctx.download_result

    输入: context.media_id, context.source_url, context.skip_download,
          context.minio_bucket/object_key/content_hash（仅 upload 路径用）
    产出: context.download_result = {local_path, content_hash, minio_object}
          以及可选的 duration_ms/width/height/fps（仅 URL 路径）
    """
    import anyio
    from videomind.infrastructure.storage.database import db_session
    from videomind.infrastructure.storage import models as m
    from videomind.infrastructure.media.minio import get_minio_client

    ctx = IngestionContext.from_dict(context)
    ctx.current_stage = "downloading"
    ctx.progress_pct = 10
    anyio.run(broadcast_progress, ctx.media_id, "downloading", 10, "开始准备源文件")

    async def _run() -> IngestionContext:
        if ctx.skip_download:
            return await _materialize_uploaded_for_transcode(ctx)

        # URL 路径走 yt-dlp
        from videomind.core.video_pipeline.download import get_downloader

        with trace_stage("download", ctx.media_id):
            with traced_span("pipeline.download", attributes={"media_id": ctx.media_id}):
                downloader = get_downloader()
                result = await downloader.download(ctx.source_url, subdir=ctx.media_id[:8])

                # 上传原始视频到 MinIO
                minio = get_minio_client()
                await minio.ensure_bucket()
                object_key = f"{result.content_hash}/original.mp4"
                await minio.upload_file(object_key, result.local_path)

                # 更新 media_file 元数据
                async with db_session() as db:
                    media = await db.get(m.MediaFile, uuid.UUID(ctx.media_id))
                    if media:
                        media.status = "downloaded"
                        media.duration_ms = result.duration_ms
                        media.width = result.width
                        media.height = result.height
                        media.fps = result.fps
                        media.minio_object = object_key
                        media.file_size = result.local_path.stat().st_size
                        # yt-dlp 抓到的视频标题持久化进 meta_json，供前端卡片展示真实标题
                        meta = dict(media.meta_json or {})
                        meta["title"] = result.title
                        media.meta_json = meta
                        await db.commit()

                ctx.download_result = {
                    "local_path": str(result.local_path),
                    "content_hash": result.content_hash,
                    "duration_ms": result.duration_ms,
                    "width": result.width,
                    "height": result.height,
                    "fps": result.fps,
                    "minio_object": object_key,
                    "title": result.title,
                }
                ctx.current_stage = "downloaded"
                ctx.progress_pct = 20
                await broadcast_progress(ctx.media_id, "downloaded", 20, "下载完成")
                return ctx

    async def _materialize_uploaded_for_transcode(ctx: IngestionContext) -> IngestionContext:
        """上传路径：把 MinIO 中的对象拉到 transcode 工作目录。

        - 元数据 (duration_ms/width/height/fps) 由 transcode_video_task 用 ffmpeg probe 重新填，
          本任务只负责"把文件搬到本地"和"标记 media_file.status='downloaded'"。
        """
        from pathlib import Path
        from videomind.config import get_settings

        settings = get_settings()
        bucket = ctx.minio_bucket or settings.minio_bucket
        object_key = ctx.minio_object
        content_hash = ctx.content_hash
        if not (object_key and content_hash):
            raise NonRetryableError(
                "skip_download=True 但 minio_object/content_hash 缺失；上传 endpoint 必须填齐"
            )

        # 工作目录：{系统临时目录}/transcode_{media_id[:8]}/original.mp4（与 download 同布局，transcode 兼容）
        # 不硬编码 /tmp——Windows/容器下 tempfile.gettempdir() 才是正确落点；目录由 transcode
        # 成功后 _cleanup_local_source 统一清理
        workdir = Path(tempfile.gettempdir()) / f"transcode_{ctx.media_id[:8]}"
        workdir.mkdir(parents=True, exist_ok=True)
        local_path = workdir / "original.mp4"

        with trace_stage("materialize_upload", ctx.media_id):
            with traced_span("pipeline.materialize_upload", attributes={"media_id": ctx.media_id, "object_key": object_key}):
                minio = get_minio_client()
                await minio.download_file(object_key, local_path)

                async with db_session() as db:
                    media = await db.get(m.MediaFile, uuid.UUID(ctx.media_id))
                    if media:
                        media.status = "downloaded"
                        media.minio_bucket = bucket
                        media.minio_object = object_key
                        media.file_size = local_path.stat().st_size
                        await db.commit()

                ctx.download_result = {
                    "local_path": str(local_path),
                    "content_hash": content_hash,
                    "minio_object": object_key,
                    # duration_ms/width/height/fps 由 transcode 阶段 ffmpeg probe 填
                }
                ctx.current_stage = "downloaded"
                ctx.progress_pct = 20
                await broadcast_progress(ctx.media_id, "downloaded", 20, "已就绪上传文件")
                return ctx

    return anyio.run(_run).to_dict()


@celery_app.task(
    bind=True,
    base=BaseVideoTask,
    name="videomind.tasks.transcode_video_task",
    max_retries=3,
    default_retry_delay=60,
)
def transcode_video_task(self, context: dict) -> dict:
    """阶段 2：转码/抽音/抽帧（FFmpeg）。

    输入: context.download_result.local_path
    产出: context.transcode_result = {audio_minio, keyframes_minio[], scene_changes_ms[], meta}
    """
    import anyio
    from videomind.core.video_pipeline.transcode import get_transcoder
    from videomind.infrastructure.storage.database import db_session
    from videomind.infrastructure.storage import models as m

    ctx = IngestionContext.from_dict(context)
    ctx.current_stage = "transcoding"
    ctx.progress_pct = 30
    anyio.run(broadcast_progress, ctx.media_id, "transcoding", 30, "开始转码")
    anyio.run(_set_media_status, ctx.media_id, "transcoding")

    async def _run() -> IngestionContext:
        dl = ctx.download_result
        if not dl:
            raise NonRetryableError("download_result missing")

        with trace_stage("transcode", ctx.media_id):
            with traced_span("pipeline.transcode", attributes={"media_id": ctx.media_id}):
                transcoder = get_transcoder()
                result = await transcoder.execute(Path(dl["local_path"]), dl["content_hash"])

                ctx.transcode_result = {
                    "audio_minio": result.audio_minio,
                    "keyframes_minio": result.keyframes_minio,
                    "scene_changes_ms": result.scene_changes_ms,
                    "duration_ms": result.duration_ms,
                    "width": result.width,
                    "height": result.height,
                    "fps": result.fps,
                }
                ctx.current_stage = "transcoded"
                ctx.progress_pct = 40
                # ffmpeg probe 完才回填 media_file 元数据（download 路径如 skip 时未填）
                if result.duration_ms is not None:
                    async with db_session() as db:
                        media = await db.get(m.MediaFile, uuid.UUID(ctx.media_id))
                        if media is not None and media.duration_ms is None:
                            media.duration_ms = result.duration_ms
                            media.width = result.width
                            media.height = result.height
                            media.fps = result.fps
                            await db.commit()
                await _set_media_status(ctx.media_id, "transcoded")
                await broadcast_progress(ctx.media_id, "transcoded", 40, "转码完成")
                # 原始视频已上 MinIO、转码产物就绪——本地源文件使命完成，即清
                _cleanup_local_source(ctx)
                return ctx

    return anyio.run(_run).to_dict()


@celery_app.task(
    bind=True,
    base=BaseVideoTask,
    name="videomind.tasks.asr_task",
    max_retries=3,
    default_retry_delay=120,
)
def asr_task(self, context: dict) -> dict:
    """阶段 3：语音识别。

    GPU 独占（GPUResourceManager.acquire("asr")）。
    输入: context.transcode_result.audio_minio
    产出: context.transcription_id, context.asr_chunks_count
    """
    import anyio
    from videomind.core.video_pipeline.asr import get_asr, save_transcription
    from videomind.application.task_orchestration.gpu import get_gpu_manager
    from videomind.infrastructure.storage.database import db_session
    from videomind.infrastructure.media.minio import get_minio_client
    from videomind.infrastructure.storage import models as m

    ctx = IngestionContext.from_dict(context)
    ctx.current_stage = "asr"
    ctx.progress_pct = 50
    anyio.run(broadcast_progress, ctx.media_id, "asr", 50, "开始语音识别")
    anyio.run(_set_media_status, ctx.media_id, "asr")

    async def _run() -> IngestionContext:
        tc = ctx.transcode_result
        if not tc:
            raise NonRetryableError("transcode_result missing")

        # 下载音频到本地临时文件（系统临时目录；不再硬编码 /tmp——Windows 不落地）
        minio = get_minio_client()
        audio_local = Path(tempfile.gettempdir()) / f"{ctx.media_id}_audio.ogg"
        await minio.download_file(tc["audio_minio"], str(audio_local))

        try:
            # GPU 独占跑 ASR
            gpu = get_gpu_manager()
            async with await gpu.acquire(self.request.id, "asr"):
                with trace_stage("asr", ctx.media_id):
                    with traced_span("pipeline.asr", attributes={"media_id": ctx.media_id, "task_id": self.request.id}):
                        asr_engine = get_asr()
                        asr_result = await asr_engine.transcribe(audio_local, uuid.UUID(ctx.media_id))

            # 可选：LLM 加标点（方案 2）—— Whisper 系列在中文不产标点
            # 接在 transcribe 后、入库前；独立于 GPU 锁，GPU 释放后再跑（不占 GPU）
            # 失败/改字自动回退裸原文（punctuate.py 守护），不阻塞主流程
            from videomind.config import get_settings
            _s = get_settings()
            if _s.asr_punctuate and asr_result.full_text:
                from videomind.core.video_pipeline.punctuate import punctuate_result
                from videomind.core.model_gateway.http_client import OpenAICompatibleClient
                _pc = OpenAICompatibleClient(
                    base_url=_s.asr_punctuate_base_url,
                    api_key=_s.asr_punctuate_api_key,
                    default_model=_s.asr_punctuate_model,
                    timeout=180.0,
                )
                try:
                    asr_result = await punctuate_result(
                        asr_result, _pc, _s.asr_punctuate_model,
                        max_tokens=_s.asr_punctuate_max_tokens,
                        temperature=_s.asr_punctuate_temperature,
                    )
                finally:
                    await _pc.close()

            # 入库
            async with db_session() as db:
                trans, chunks = await save_transcription(db, uuid.UUID(ctx.media_id), asr_result)
                await db.commit()
                ctx.transcription_id = str(trans.id)
                ctx.asr_chunks_count = len(chunks)

            ctx.current_stage = "asr_done"
            ctx.progress_pct = 60
            await _set_media_status(ctx.media_id, "asr_done")
            await broadcast_progress(ctx.media_id, "asr", 60, "语音识别完成")
            return ctx
        finally:
            # 音频临时文件用完即清；失败重试会重新从 MinIO 拉取，残留只占磁盘
            audio_local.unlink(missing_ok=True)

    return anyio.run(_run).to_dict()


@celery_app.task(
    bind=True,
    base=BaseVideoTask,
    name="videomind.tasks.ocr_task",
    max_retries=2,
    default_retry_delay=60,
)
def ocr_task(self, context: dict) -> dict:
    """阶段 4：关键帧 OCR。

    GPU 独占（GPUResourceManager.acquire("ocr")）。
    输入: context.transcode_result.keyframes_minio[]
    产出: context.ocr_frames_count
    """
    import anyio
    from videomind.core.video_pipeline.ocr import get_ocr, save_frame_ocr
    from videomind.application.task_orchestration.gpu import get_gpu_manager
    from videomind.infrastructure.storage.database import db_session

    ctx = IngestionContext.from_dict(context)
    ctx.current_stage = "ocr"
    ctx.progress_pct = 70
    anyio.run(broadcast_progress, ctx.media_id, "ocr", 70, "开始 OCR 识别")
    anyio.run(_set_media_status, ctx.media_id, "ocr")

    async def _run() -> IngestionContext:
        tc = ctx.transcode_result
        if not tc:
            raise NonRetryableError("transcode_result missing")

        gpu = get_gpu_manager()
        try:
            async with await gpu.acquire(self.request.id, "ocr"):
                with trace_stage("ocr", ctx.media_id):
                    with traced_span("pipeline.ocr", attributes={"media_id": ctx.media_id, "task_id": self.request.id}):
                        ocr_engine = get_ocr()
                        results = await ocr_engine.recognize_frames(
                            uuid.UUID(ctx.media_id), tc["keyframes_minio"]
                        )
        except NotImplementedError as e:
            # 已知跳过条件（非程序错误）：paddle PIR/oneDNN 上游 bug，或 OCR_PROVIDER=api 缺 key。
            # OCR 以空结果降级，pipeline 仍可进入 INDEX（OCR 非关键路径，视频已有 ASR 文本）。具体原因见 e。
            logger.warning("OCR 阶段按已知跳过条件降级: %s", e)
            results = []
        except ImportError as e:
            # 缺本地 OCR 依赖（未 `uv sync --extra ocr` / py3.14 无 paddle wheel；ModuleNotFoundError 亦然）。
            # 不再静默吞——这是"OCR 不起作用且无报错表面化"的根因放大器（见 DECISIONS.md §3.12）：
            # 原先此处一个 `except Exception` 把 ImportError 当普通降级降成 WARNING，与"帧上无文字"
            # 不可区分，掩盖真实依赖问题。现 Error 级暴露 + 给修复路径；OCR 非关键，仍降级进 INDEX。
            logger.error(
                "OCR 缺依赖跳过（已暴露）: %s: %s | 修复：OCR_PROVIDER=api 走 ocr.space，"
                "或 `uv sync --extra ocr`（需 py≤3.13 装 paddle）。",
                type(e).__name__, e,
            )
            results = []
        except Exception as e:
            # 未预期的 OCR 错误——Error 级暴露真实原因，不再静默（静默曾掩盖依赖缺失被当 known-skip）。
            # OCR 非关键路径（视频已有 ASR 文本），降级 results=[] 让 pipeline 继续，但不掩盖。
            logger.error("OCR 阶段异常降级（非 known-skip，已暴露）: %s: %s", type(e).__name__, e)
            results = []

        async with db_session() as db:
            if results:
                await save_frame_ocr(db, uuid.UUID(ctx.media_id), results)
            await db.commit()
            ctx.ocr_frames_count = len(results)

        ctx.current_stage = "ocr_done"
        ctx.progress_pct = 75
        await _set_media_status(ctx.media_id, "ocr_done")
        await broadcast_progress(ctx.media_id, "ocr", 75, "OCR 完成（已降级）" if not results else "OCR 完成")
        return ctx

    return anyio.run(_run).to_dict()


@celery_app.task(
    bind=True,
    base=BaseVideoTask,
    name="videomind.tasks.index_task",
    max_retries=3,
    default_retry_delay=60,
)
def index_task(self, context: dict) -> dict:
    """阶段 5：分块 → Embedding → Qdrant + chunk 表双写。

    GPU 独占（GPUResourceManager.acquire("embedding")）。
    输入: context.transcription_id, asr chunks, ocr results
    产出: context.chunk_count
    """
    import anyio
    from videomind.core.video_pipeline.index import get_indexer
    from videomind.application.task_orchestration.gpu import get_gpu_manager
    from videomind.infrastructure.storage.database import db_session
    from videomind.infrastructure.storage import models as m
    from sqlalchemy import select

    ctx = IngestionContext.from_dict(context)
    ctx.current_stage = "indexing"
    ctx.progress_pct = 85
    anyio.run(broadcast_progress, ctx.media_id, "indexing", 85, "开始索引构建")

    async def _run() -> IngestionContext:
        if not ctx.transcription_id:
            raise NonRetryableError("transcription_id missing")

        gpu = get_gpu_manager()
        async with await gpu.acquire(self.request.id, "embedding"):
            indexer = get_indexer()

        async with db_session() as db:
            # 取转写 chunks + OCR 结果
            trans = await db.get(m.Transcription, uuid.UUID(ctx.transcription_id))
            chunks = await db.execute(
                select(m.TranscriptionChunk).where(
                    m.TranscriptionChunk.media_id == uuid.UUID(ctx.media_id)
                ).order_by(m.TranscriptionChunk.chunk_index)
            )
            chunk_list = chunks.scalars().all()

            ocr_results = await db.execute(
                select(m.FrameOCR).where(m.FrameOCR.media_id == uuid.UUID(ctx.media_id))
            )
            ocr_list = ocr_results.scalars().all()

            with trace_stage("indexing", ctx.media_id):
                with traced_span("pipeline.indexing", attributes={"media_id": ctx.media_id, "task_id": self.request.id}):
                    result = await indexer.index(
                        db, uuid.UUID(ctx.media_id), trans, chunk_list, ocr_list
                    )
                    await db.commit()
                    ctx.chunk_count = result.chunk_count

        ctx.current_stage = "completed"
        ctx.progress_pct = 100
        await broadcast_progress(ctx.media_id, "completed", 100, "索引构建完成")
        return ctx

    return anyio.run(_run).to_dict()


# ──────────────────────────── 总管线任务 ────────────────────────────


@celery_app.task(
    bind=True,
    base=BaseVideoTask,
    name="videomind.tasks.pipeline_task",
    max_retries=0,  # 管线任务不自动重试，由阶段任务内部重试
)
def pipeline_task(self, context: dict) -> dict:
    """总管线：串联 download → transcode → asr → ocr → index。

    使用 Celery chain 保证顺序执行，每阶段产出的 context 传给下一阶段。
    """
    import anyio

    ctx = IngestionContext.from_dict(context)
    ctx.current_stage = "pipeline_start"
    ctx.progress_pct = 5

    # 构建 chain：每个任务返回 context 传给下一个
    pipeline_chain = chain(
        download_video_task.s(ctx.to_dict()),
        transcode_video_task.s(),
        asr_task.s(),
        ocr_task.s(),
        index_task.s(),
    )

    # 异步执行 chain（apply_async 返回 AsyncResult）
    # 指定 queue='cpu' 让 pipeline_task 所在队列分发，各子任务按 task_routes 自动路由
    result = pipeline_chain.apply_async(queue="cpu")

    # 这里不阻塞等待，返回 chain id 供前端轮询
    # 实际进度通过 SSE (broadcast_progress) 推送
    return {
        "chain_id": result.id,
        "media_id": ctx.media_id,
        "status": "started",
    }


# ──────────────────────────── Agent 分析任务（1.4）────────────────────────────


@celery_app.task(bind=True, name="videomind.tasks.agent_analyze_task")
def agent_analyze_task(self, context: dict) -> None:
    """Agent 分析（cpu 队列）：包装 agent_runner.run_agent_analysis。

    1.4 从 FastAPI BackgroundTasks 平迁而来——API 进程重启不丢任务、
    重活不占 HTTP worker；HTTP 接口与前端轮询不变（AnalysisTask 状态机在 PG）。

    不继承 BaseVideoTask 的三点理由：
    1. 无 IngestionContext / media 生命周期，on_failure 的 media 落库不适用；
    2. 失败已在 run_agent_analysis 内部落 AnalysisTask 终态（status=failed），
       Celery 层重试只会重复烧 LLM token，无意义；
    3. uuid 不可 JSON 序列化——context 一律转 str 传输（worker 侧还原）。

    context: {"task_id": str, "goal": str, "media_ids": [str], "max_rounds": int}
    """
    import anyio

    from videomind.application.task_orchestration.agent_runner import run_agent_analysis

    async def _run() -> None:
        await run_agent_analysis(
            task_id=uuid.UUID(context["task_id"]),
            goal=context["goal"],
            media_ids=[uuid.UUID(x) for x in context["media_ids"]],
            max_rounds=int(context["max_rounds"]),
        )

    anyio.run(_run)


# ──────────────────────────── 导出 ────────────────────────────


__all__ = [
    "celery_app",
    "IngestionContext",
    "download_video_task",
    "transcode_video_task",
    "asr_task",
    "ocr_task",
    "index_task",
    "pipeline_task",
    "agent_analyze_task",
]