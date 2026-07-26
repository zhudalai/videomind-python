"""Token 计费入库模块。

每次 LLM 调用完成后写入：
1. AiCallLog 记录到 PostgreSQL（失败不阻断调用）
2. Prometheus 指标（LLM_CALL_TOTAL / LLM_CALL_LATENCY / LLM_TOKEN_USAGE）
"""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from videomind.core.model_gateway.types import ChatRequest, ChatResponse
from videomind.infrastructure.storage.models import AiCallLog
from videomind.observability.metrics import (
    LLM_CALL_LATENCY,
    LLM_CALL_TOTAL,
    LLM_TOKEN_USAGE,
)

logger = structlog.get_logger(__name__)


class TokenAccounting:
    """Token 计费：写 AiCallLog + Prometheus 指标。

    DB 写入失败不中断调用方 —— 仅记录异常日志并继续。
    """

    async def record(
        self,
        db: AsyncSession,
        request: ChatRequest,
        response: ChatResponse,
        model_id: str,
        status: str = "success",
        error_code: str | None = None,
    ) -> None:
        """记录一次 LLM 调用的计费信息。

        Args:
            db: SQLAlchemy AsyncSession。
            request: 原始 ChatRequest（含 trace_id、task_type）。
            response: LLM 返回的 ChatResponse（含 usage、latency_ms）。
            model_id: 模型标识符，格式 "provider:model" 或单值。
            status: 调用状态（success/error/timeout）。
            error_code: 错误码，仅在 status 非 success 时传入。
        """
        # 解析 provider:model
        if ":" in model_id:
            provider, _, model = model_id.partition(":")
        else:
            provider = model_id
            model = model_id

        log = AiCallLog(
            provider=provider,
            model=model,
            task_type=request.task_type or "chat",
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            total_tokens=response.usage.total_tokens,
            cost_usd=response.usage.cost_usd,
            trace_id=request.trace_id,
            status=status,
            error_code=error_code,
        )

        # DB 写入（失败不阻断调用）
        try:
            db.add(log)
            await db.flush()
        except Exception:
            logger.exception(
                "Failed to write AiCallLog for trace=%s", request.trace_id
            )

        # Prometheus 指标（不受 DB 失败影响）
        LLM_CALL_TOTAL.labels(provider=provider, model=model, status=status).inc()
        LLM_CALL_LATENCY.labels(provider=provider, model=model).observe(
            response.latency_ms / 1000.0
        )
        LLM_TOKEN_USAGE.labels(provider=provider, model=model, type="prompt").inc(
            response.usage.prompt_tokens
        )
        LLM_TOKEN_USAGE.labels(provider=provider, model=model, type="completion").inc(
            response.usage.completion_tokens
        )
        LLM_TOKEN_USAGE.labels(provider=provider, model=model, type="total").inc(
            response.usage.total_tokens
        )


__all__ = ["TokenAccounting"]