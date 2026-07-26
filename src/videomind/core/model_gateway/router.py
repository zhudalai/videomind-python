"""LLM 路由编排网关。

当前支持单 provider 部署（deepseek-v4-flash-free），但接口支持多候选降级链。
每次调用经过：熔断检查 → provider 调用 → 计费记录。
"""

from __future__ import annotations

from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from videomind.core.model_gateway.accounting import TokenAccounting
from videomind.core.model_gateway.circuit import ModelHealthStore
from videomind.core.model_gateway.http_client import OpenAICompatibleClient
from videomind.core.model_gateway.types import ChatChunk, ChatRequest, ChatResponse, TokenUsage


class AllProvidersFailedError(Exception):
    """所有候选 Provider 全部失败。

    当熔断器拒绝调用或 provider 调用失败时抛出此异常。
    """


class RoutingLLMService:
    """LLM 路由网关：单 provider 链 + 熔断 + 计费。

    当前单 provider 部署（deepseek-v4-flash-free），但接口支持多候选降级链。

    Attributes:
        _provider: OpenAI 兼容客户端。
        _model_id: 模型标识符，格式 "provider:model"。
        _health: 熔断器存储。
        _accounting: Token 计费模块。
    """

    def __init__(
        self,
        provider: OpenAICompatibleClient,
        model_id: str,
        health: ModelHealthStore,
        accounting: TokenAccounting,
    ) -> None:
        """初始化路由网关。

        Args:
            provider: OpenAI 兼容 HTTP 客户端。
            model_id: 模型标识符，如 "opencode:deepseek"。
            health: 三态熔断器。
            accounting: Token 计费入库服务。
        """
        self._provider = provider
        self._model_id = model_id
        self._health = health
        self._accounting = accounting

    # ----------------------------------------------------------------
    # chat
    # ----------------------------------------------------------------

    async def chat(self, request: ChatRequest, db: AsyncSession) -> ChatResponse:
        """执行一次 Chat Completions 调用，带熔断和计费。

        Args:
            request: Chat 请求参数。
            db: SQLAlchemy AsyncSession，用于计费入库。

        Returns:
            ChatResponse 含模型响应内容和用量统计。

        Raises:
            AllProvidersFailedError: 熔断 OPEN 或 provider 调用失败。
        """
        if not await self._health.allow_call(self._model_id):
            raise AllProvidersFailedError(
                f"Circuit OPEN for {self._model_id}"
            )

        try:
            response = await self._provider.chat(request)
            response.provider = self._model_id
            await self._health.mark_success(self._model_id)
            await self._accounting.record(
                db, request, response, self._model_id, "success"
            )
            return response
        except Exception as e:
            await self._health.mark_failure(self._model_id)
            error_code = type(e).__name__

            # 计费记录失败不阻断异常传播
            try:
                dummy = ChatResponse(
                    content="",
                    model=self._model_id,
                    usage=TokenUsage(),
                )
                dummy.latency_ms = 0
                await self._accounting.record(
                    db, request, dummy, self._model_id, "error", error_code
                )
            except Exception:
                pass

            raise AllProvidersFailedError(
                f"Provider {self._model_id} failed: {e}"
            ) from e

    # ----------------------------------------------------------------
    # stream_chat
    # ----------------------------------------------------------------

    async def stream_chat(
        self, request: ChatRequest, db: AsyncSession
    ) -> AsyncIterator[ChatChunk]:
        """执行流式 Chat Completions，带熔断检查。

        Args:
            request: Chat 请求参数。
            db: AsyncSession，流式场景暂不使用（计费在 provider 层处理）。

        Yields:
            ChatChunk: 增量响应分片。

        Raises:
            AllProvidersFailedError: 熔断 OPEN 或流式传输中断。
        """
        if not await self._health.allow_call(self._model_id):
            raise AllProvidersFailedError(
                f"Circuit OPEN for {self._model_id}"
            )

        try:
            async for chunk in self._provider.stream_chat(request):
                yield chunk
            await self._health.mark_success(self._model_id)
        except Exception as e:
            await self._health.mark_failure(self._model_id)
            raise AllProvidersFailedError(f"Stream failed: {e}") from e

    # ----------------------------------------------------------------
    # close
    # ----------------------------------------------------------------

    async def close(self) -> None:
        """关闭内部 HTTP 客户端，释放连接。"""
        await self._provider.close()


__all__ = ["AllProvidersFailedError", "RoutingLLMService"]