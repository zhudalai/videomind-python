"""Prometheus 指标定义 + FastAPI 指标端点。

对应 docs/OBSERVABILITY.md §Metrics。

指标命名约定：`videomind_<subsystem>_<name>`，单位用 `_seconds` / `_bytes` / `_total` 后缀。
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, make_asgi_app

# ──────────────────────────── HTTP 请求指标 ────────────────────────────

HTTP_REQUEST_TOTAL = Counter(
    "videomind_http_requests_total",
    "HTTP 请求总数",
    ["method", "path", "status"],
)

HTTP_REQUEST_DURATION = Histogram(
    "videomind_http_request_duration_seconds",
    "HTTP 请求耗时（秒）",
    ["method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)

# ──────────────────────────── 管线阶段指标 ────────────────────────────

PIPELINE_STAGE_TOTAL = Counter(
    "videomind_pipeline_stage_total",
    "管线阶段执行总数",
    ["stage", "status"],  # status: started/succeeded/failed/retried
)

PIPELINE_STAGE_DURATION = Histogram(
    "videomind_pipeline_stage_duration_seconds",
    "管线阶段执行耗时（秒）",
    ["stage"],
    buckets=(1, 5, 10, 30, 60, 120, 300, 600, 1200, 1800),
)

# ──────────────────────────── GPU 利用率指标 ────────────────────────────

GPU_UTILIZATION = Gauge(
    "videomind_gpu_utilization_percent",
    "GPU 利用率百分比（0-100）",
    ["device_id", "stage"],  # stage: asr/ocr/embedding/llm
)

GPU_MEMORY_USED = Gauge(
    "videomind_gpu_memory_used_bytes",
    "GPU 显存占用（字节）",
    ["device_id"],
)

GPU_MEMORY_TOTAL = Gauge(
    "videomind_gpu_memory_total_bytes",
    "GPU 显存总量（字节）",
    ["device_id"],
)

# ──────────────────────────── 队列深度指标 ────────────────────────────

QUEUE_DEPTH = Gauge(
    "videomind_queue_depth",
    "Celery 队列待处理任务数",
    ["queue"],  # gpu / cpu
)

# ──────────────────────────── Celery 任务指标 ────────────────────────────

CELERY_TASK_TOTAL = Counter(
    "videomind_celery_task_total",
    "Celery 任务执行总数",
    ["task_name", "status"],  # status: success/failure/retry
)

CELERY_TASK_DURATION = Histogram(
    "videomind_celery_task_duration_seconds",
    "Celery 任务执行耗时（秒）",
    ["task_name"],
    buckets=(1, 5, 10, 30, 60, 120, 300, 600, 1200, 1800),
)

CELERY_TASK_RETRIES = Counter(
    "videomind_celery_task_retries_total",
    "Celery 任务重试总数",
    ["task_name"],
)

# ──────────────────────────── RAG 检索指标 ────────────────────────────

RAG_RETRIEVAL_TOTAL = Counter(
    "videomind_rag_retrieval_total",
    "RAG 检索请求总数",
    ["channel", "status"],  # channel: vector/bm25/sql; status: success/empty/error
)

RAG_RETRIEVAL_LATENCY = Histogram(
    "videomind_rag_retrieval_latency_seconds",
    "RAG 检索延迟（秒）",
    ["channel"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5),
)

RAG_RERANK_LATENCY = Histogram(
    "videomind_rag_rerank_latency_seconds",
    "RAG 重排延迟（秒）",
    ["reranker"],  # deterministic/cross_encoder
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0),
)

RAG_ANSWER_LATENCY = Histogram(
    "videomind_rag_answer_latency_seconds",
    "RAG 生成回答延迟（秒）",
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

# ──────────────────────────── LLM 调用指标 ────────────────────────────

LLM_CALL_TOTAL = Counter(
    "videomind_llm_call_total",
    "LLM 调用总数",
    ["provider", "model", "status"],  # status: success/error/timeout
)

LLM_CALL_LATENCY = Histogram(
    "videomind_llm_call_latency_seconds",
    "LLM 调用延迟（秒）",
    ["provider", "model"],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
)

LLM_TOKEN_USAGE = Counter(
    "videomind_llm_token_usage_total",
    "LLM Token 使用量",
    ["provider", "model", "type"],  # type: prompt/completion/total
)

# ──────────────────────────── 导出 ASGI 应用 ────────────────────────────

def get_metrics_app():
    """返回挂载到 /metrics 的 ASGI 应用。"""
    return make_asgi_app()