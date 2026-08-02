"""Celery 应用实例 —— 任务队列入口。

对应 docs/TASK-ORCHESTRATION.md Celery 配置 + docs/VIDEO-PIPELINE.md 管线任务。
设计要点：
1. 两个队列：
   - `gpu`：GPU 密集型任务（ASR/OCR/**Embedding/索引**），concurrency=1，单卡串行
   - `cpu`：CPU 密集型任务（下载/转码），concurrency=4，可横向扩容
2. 结果后端 Redis DB2，Broker Redis DB1（避免与缓存 DB0 争用）。
3. 任务路由：`task_routes` 自动按 queue 分发。
"""

from __future__ import annotations

from celery import Celery
from celery.signals import task_prerun, task_postrun, task_failure, task_retry, worker_init

from videomind.config import get_settings
from videomind.observability.metrics import (
    CELERY_TASK_DURATION,
    CELERY_TASK_TOTAL,
    CELERY_TASK_RETRIES,
)
from videomind.observability.tracing import init_tracing, inject_trace_context, extract_trace_context

_settings = get_settings()

celery_app = Celery(
    "videomind",
    broker=_settings.celery_broker_url,
    backend=_settings.celery_result_backend,
    include=[
        "videomind.application.task_orchestration.tasks",
        "videomind.application.task_orchestration.celery",
    ],
)

# ──────────────────────────── Celery 配置 ────────────────────────────

celery_app.conf.update(
    # 序列化
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # 时区
    timezone="Asia/Shanghai",
    enable_utc=True,
    # 结果过期
    result_expires=86400,  # 24h
    # 队列路由
    task_routes={
        "videomind.tasks.download_video_task": {
            "queue": "cpu"
        },
        "videomind.tasks.transcode_video_task": {
            "queue": "cpu"
        },
        "videomind.tasks.asr_task": {
            "queue": "gpu"
        },
        "videomind.tasks.ocr_task": {
            "queue": "gpu"
        },
        "videomind.tasks.index_task": {
            "queue": "gpu"
        },
        "videomind.tasks.pipeline_task": {
            "queue": "cpu"
        },
    },
    # Worker 预取
    worker_prefetch_multiplier=1,
    # 任务确认
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # 重试策略（由任务内部控制，Celery 只做基础 retry）
    task_default_retry_delay=60,
    task_max_retries=3,
)

# ──────────────────────────── 信号：启动/关闭钩子 ────────────────────────────


@celery_app.task(bind=True, ignore_result=True)
def debug_task(self):
    """调试用任务。"""
    print(f"Request: {self.request!r}")


# ──────────────────────────── 任务指标收集 ────────────────────────────

# Disabled due to Celery 5.6.3 fast_trace_task compatibility issue
# _task_start_times: dict[str, float] = {}
#
#
# @task_prerun.connect
# def _task_prerun(task_id, task, *args, **kwargs):
#     """任务开始：记录开始时间 + 注入 trace context。"""
#     import time
#
#     _task_start_times[task_id] = time.perf_counter()
#
#     # 从 task headers 提取 trace context 并恢复
#     headers = getattr(task.request, "headers", {}) or {}
#     if headers:
#         ctx = extract_trace_context(headers)
#         if ctx:
#             from opentelemetry.context import attach, set_value
#             from opentelemetry.trace import SpanContext, TraceFlags, NonRecordingSpan
#
#             span_ctx = SpanContext(
#                 trace_id=ctx.trace_id,
#                 span_id=ctx.span_id,
#                 is_remote=True,
#                 trace_flags=TraceFlags(TraceFlags.SAMPLED),
#             )
#             span = NonRecordingSpan(span_ctx)
#             token = attach(set_value("span", span))
#             _task_start_times[f"{task_id}_token"] = token
#
#
# @task_postrun.connect
# def _task_postrun(task_id, task, *args, **kwargs):
#     """任务结束：记录耗时 + 计数器。"""
#     import time
#
#     start = _task_start_times.pop(task_id, None)
#     token = _task_start_times.pop(f"{task_id}_token", None)
#
#     if token:
#         from opentelemetry.context import detach
#
#         detach(token)
#
#     if start is None:
#         return
#
#     duration = time.perf_counter() - start
#     task_name = task.name
#
#     CELERY_TASK_DURATION.labels(task_name=task_name).observe(duration)
#     CELERY_TASK_TOTAL.labels(task_name=task_name, status="success").inc()
#
#
# @task_failure.connect
# def _task_failure(task_id, task, *args, **kwargs):
#     """任务失败：记录失败计数。"""
#     task_name = task.name
#     CELERY_TASK_TOTAL.labels(task_name=task_name, status="failure").inc()
#
#
# @task_retry.connect
# def _task_retry(task_id, task, *args, **kwargs):
#     """任务重试：记录重试计数。"""
#     task_name = task.name
#     CELERY_TASK_RETRIES.labels(task_name=task_name).inc()
#     CELERY_TASK_TOTAL.labels(task_name=task_name, status="retry").inc()


# ──────────────────────────── Worker 启动时初始化追踪 ────────────────────────────

def _init_worker_tracing(**kwargs):
    """Worker 主进程启动时初始化 OpenTelemetry 追踪。"""
    init_tracing("videomind-worker")


async def _dispose_db_pool_async() -> None:
    """prefork 子进程启动时清空继承自父进程的 asyncpg 连接池。

    Celery prefork 在 Windows 上用 spawn：父进程在 import 链里已创建 async engine，
    子进程 spawn 时复制了引用，但底层 asyncpg connection 的 socket 已失效，
    首次使用会抛 ``'NoneType' object has no attribute 'send'`` 触发任务 retry。
    在 ``worker_process_init`` 里 dispose 一下，子进程下次取连接会重建新池，
    避免"首次 ASR 任务 → retry 一次 → 才成功"的冷启动抖动。
    """
    from videomind.infrastructure.storage.database import dispose_engine
    await dispose_engine()


def _on_worker_process_init(**kwargs) -> None:
    """Prefork 子进程初始化钩子：重置 DB 连接池 + 初始化追踪。"""
    import asyncio

    try:
        asyncio.run(_dispose_db_pool_async())
    except RuntimeError:
        # 已有 running loop（非 prefork 池，如 gevent/solo）—— 退化为临时 loop 跑
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(_dispose_db_pool_async())
        finally:
            loop.close()
    except Exception as e:  # pragma: no cover - 不阻断 worker 启动
        print(f"[worker_process_init] dispose_engine failed: {e!r}", flush=True)

    init_tracing("videomind-worker")


# 绑定到 worker 启动信号
from celery.signals import worker_init, worker_process_init
worker_init.connect(_init_worker_tracing)
worker_process_init.connect(_on_worker_process_init)


# 导出供外部 import
__all__ = ["celery_app"]