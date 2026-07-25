"""VideoMind Observability Package."""

from __future__ import annotations

from videomind.observability.metrics import (
    CELERY_TASK_DURATION,
    CELERY_TASK_TOTAL,
    CELERY_TASK_RETRIES,
    GPU_MEMORY_TOTAL,
    GPU_MEMORY_USED,
    GPU_UTILIZATION,
    HTTP_REQUEST_DURATION,
    HTTP_REQUEST_TOTAL,
    LLM_CALL_LATENCY,
    LLM_CALL_TOTAL,
    LLM_TOKEN_USAGE,
    PIPELINE_STAGE_DURATION,
    PIPELINE_STAGE_TOTAL,
    QUEUE_DEPTH,
    RAG_ANSWER_LATENCY,
    RAG_RETRIEVAL_LATENCY,
    RAG_RETRIEVAL_TOTAL,
    RAG_RERANK_LATENCY,
    get_metrics_app,
)
from videomind.observability.tracing import (
    get_logger,
    get_tracer,
    init_tracing,
    inject_trace_context,
    extract_trace_context,
    traced_span,
)


def setup_observability(service_name: str = "videomind") -> None:
    """初始化完整的可观测性栈：Tracing + Metrics。

    在 FastAPI 应用启动时调用一次即可。
    """
    init_tracing(service_name)


__all__ = [
    # Metrics
    "HTTP_REQUEST_TOTAL",
    "HTTP_REQUEST_DURATION",
    "PIPELINE_STAGE_TOTAL",
    "PIPELINE_STAGE_DURATION",
    "GPU_UTILIZATION",
    "GPU_MEMORY_USED",
    "GPU_MEMORY_TOTAL",
    "QUEUE_DEPTH",
    "CELERY_TASK_TOTAL",
    "CELERY_TASK_DURATION",
    "CELERY_TASK_RETRIES",
    "RAG_RETRIEVAL_TOTAL",
    "RAG_RETRIEVAL_LATENCY",
    "RAG_RERANK_LATENCY",
    "RAG_ANSWER_LATENCY",
    "LLM_CALL_TOTAL",
    "LLM_CALL_LATENCY",
    "LLM_TOKEN_USAGE",
    "get_metrics_app",
    # Tracing
    "init_tracing",
    "get_tracer",
    "get_logger",
    "inject_trace_context",
    "extract_trace_context",
    "traced_span",
    # Setup
    "setup_observability",
]