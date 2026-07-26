"""OpenTelemetry 分布式追踪初始化。

对应 docs/OBSERVABILITY.md §Tracing + §Logging。

功能：
1. 初始化 TracerProvider + OTLP Exporter（OTel Collector / Jaeger / Zipkin）
2. 自动插桩：FastAPI、HTTPX、Redis、SQLAlchemy、Celery
3. 提供 `get_tracer()` 获取业务 Tracer
4. 结构化日志：structlog + trace_id 注入
"""

from __future__ import annotations

import logging
import os
from contextvars import ContextVar
from typing import Optional

import structlog
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.propagate import extract, inject
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import SpanKind

from videomind.config import get_settings

# ──────────────────────────── 上下文变量：trace_id 注入日志 ────────────────────────────

_current_span: ContextVar[Optional[trace.Span]] = ContextVar("_current_span", default=None)


def get_current_trace_id() -> Optional[str]:
    """获取当前 Span 的 trace_id（16进制字符串），用于日志关联。"""
    # 先检查自定义 context var（用于 TracedSpan 上下文管理器）
    span = _current_span.get()
    if span and span.get_span_context().trace_id:
        return format(span.get_span_context().trace_id, "032x")
    # 回退到 OpenTelemetry 标准当前 span
    current_span = trace.get_current_span()
    ctx = current_span.get_span_context()
    if ctx and ctx.trace_id:
        return format(ctx.trace_id, "032x")
    return None


# ──────────────────────────── 结构化日志配置 ────────────────────────────

def configure_structlog() -> None:
    """配置 structlog：JSON 输出 + trace_id 自动注入。"""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _inject_trace_id,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def _inject_trace_id(logger, method_name, event_dict):
    """Processor：自动注入 trace_id 到日志字段。"""
    trace_id = get_current_trace_id()
    if trace_id:
        event_dict["trace_id"] = trace_id
    return event_dict


def get_logger(name: str = "videomind"):
    """获取结构化 logger。"""
    return structlog.get_logger(name)


# ──────────────────────────── Tracing 初始化 ────────────────────────────

_tracing_initialized = False


def init_tracing(service_name: str = "videomind") -> None:
    """初始化 OpenTelemetry Tracing（幂等）。"""
    global _tracing_initialized
    if _tracing_initialized:
        return

    settings = get_settings()

    # Resource 属性
    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": "0.1.0",
            "deployment.environment": settings.env,
        }
    )

    # TracerProvider
    provider = TracerProvider(resource=resource)

    # OTLP Exporter —— 显式 opt-in：
    # 仅当显式设置 OTEL_EXPORTER_OTLP_ENDPOINT 时才挂 OTLP exporter，
    # 否则不再连 localhost:4317（无 collector 时会无限重试刷屏）。
    # 自动插桩仍正常工作（spans 不上报而已）。
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    if otlp_endpoint:
        exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
        provider.add_span_processor(BatchSpanProcessor(exporter))

    trace.set_tracer_provider(provider)

    # 自动插桩
    FastAPIInstrumentor().instrument()
    HTTPXClientInstrumentor().instrument()
    RedisInstrumentor().instrument()
    SQLAlchemyInstrumentor().instrument(enable_commenter=True, commenter_options={})
    # CeleryInstrumentor().instrument()  # Disabled due to version incompatibility causing fast_trace_task errors
    LoggingInstrumentor().instrument(set_logging_format=True)

    _tracing_initialized = True


def get_tracer(name: str = "videomind") -> trace.Tracer:
    """获取业务 Tracer。"""
    return trace.get_tracer(name)


# ──────────────────────────── 业务 Span 封装 ────────────────────────────

class TracedSpan:
    """上下文管理器：创建业务 Span 并自动注入 trace_id 到 structlog 上下文。"""

    def __init__(
        self,
        name: str,
        kind: SpanKind = SpanKind.INTERNAL,
        attributes: dict | None = None,
    ):
        self.name = name
        self.kind = kind
        self.attributes = attributes or {}
        self.span: trace.Span | None = None
        self.token = None

    def __enter__(self) -> trace.Span:
        tracer = get_tracer()
        self.span = tracer.start_span(self.name, kind=self.kind, attributes=self.attributes)
        self.token = _current_span.set(self.span)
        return self.span

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self.span:
            if exc_val:
                self.span.record_exception(exc_val)
                self.span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc_val)))
            self.span.end()
        if self.token:
            _current_span.reset(self.token)


def traced_span(
    name: str,
    kind: SpanKind = SpanKind.INTERNAL,
    attributes: dict | None = None,
) -> TracedSpan:
    """便捷函数：`with traced_span("my_op"): ...`"""
    return TracedSpan(name, kind, attributes)


# ──────────────────────────── Celery 任务追踪辅助 ────────────────────────────

def inject_trace_context(carrier: dict) -> None:
    """将当前 trace context 注入 carrier（用于 Celery 任务 headers 传递）。"""
    # 获取当前 span 并设置到上下文中，然后注入
    current_span = trace.get_current_span()
    ctx = trace.set_span_in_context(current_span)
    inject(carrier, context=ctx)


def extract_trace_context(carrier: dict) -> trace.SpanContext | None:
    """从 carrier 提取 trace context（用于 Celery worker 端恢复）。"""
    # 从 carrier 提取上下文
    ctx = extract(carrier)
    # 从提取的上下文中获取当前 span 的上下文
    span = trace.get_current_span(ctx)
    span_ctx = span.get_span_context()
    if span_ctx and span_ctx.is_valid:
        return span_ctx
    return None


# 初始化 structlog
configure_structlog()