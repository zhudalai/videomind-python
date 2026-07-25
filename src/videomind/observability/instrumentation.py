"""可观测性插桩工具：为管线阶段提供便捷的追踪/指标上下文。"""

from __future__ import annotations

from contextlib import contextmanager

from videomind.observability.metrics import PIPELINE_STAGE_DURATION, PIPELINE_STAGE_TOTAL
from videomind.observability.tracing import traced_span


@contextmanager
def trace_stage(stage: str, media_id: str):
    """管线阶段级追踪上下文：自动记录 Prometheus 指标 + OpenTelemetry Span。

    用法：
        with trace_stage("asr", media_id):
            # 执行 ASR 逻辑
            ...
    """
    # Prometheus 计数器：stage started
    PIPELINE_STAGE_TOTAL.labels(stage=stage, status="started").inc()

    start = __import__("time").perf_counter()
    span_cm = traced_span(f"pipeline.{stage}", attributes={"media_id": media_id, "stage": stage})

    with span_cm as span:
        try:
            yield span
            # 成功
            duration = __import__("time").perf_counter() - start
            PIPELINE_STAGE_DURATION.labels(stage=stage).observe(duration)
            PIPELINE_STAGE_TOTAL.labels(stage=stage, status="succeeded").inc()
        except Exception as e:
            # 失败
            duration = __import__("time").perf_counter() - start
            PIPELINE_STAGE_DURATION.labels(stage=stage).observe(duration)
            PIPELINE_STAGE_TOTAL.labels(stage=stage, status="failed").inc()
            if span and span.is_recording():
                span.record_exception(e)
            raise