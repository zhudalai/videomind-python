"""RoutingLLMService 路由编排单元测试。

测试覆盖：
1. chat 正常通过 → 返回响应 + 标记成功 + 计费
2. 熔断 OPEN → AllProvidersFailedError
3. Provider 失败 → 标记失败 + 错误计费 + AllProvidersFailedError
4. stream_chat 熔断检查 + 成功路径
5. close 代理到 provider.close()
"""

from __future__ import annotations

from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from videomind.core.model_gateway.types import ChatChunk, ChatRequest, ChatResponse, TokenUsage


# ----------------------------------------------------------------
# 辅助工具
# ----------------------------------------------------------------


async def _async_gen(items: list):
    """将列表转换为 AsyncIterator，用于 mock stream_chat。"""
    for item in items:
        yield item


def _make_request(**kwargs) -> ChatRequest:
    """构造最小 ChatRequest。"""
    defaults: dict = {
        "messages": [{"role": "user", "content": "hi"}],
    }
    defaults.update(kwargs)
    return ChatRequest(**defaults)


def _make_response(**kwargs) -> ChatResponse:
    """构造测试用 ChatResponse。"""
    defaults: dict = {
        "content": "Hello world",
        "model": "test-model",
        "usage": TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        "latency_ms": 100,
    }
    defaults.update(kwargs)
    return ChatResponse(**defaults)


def _make_db():
    """构造 mock AsyncSession。"""
    return AsyncMock()


# ----------------------------------------------------------------
# RoutingLLMService 测试
# ----------------------------------------------------------------


class TestRoutingLLMServiceChat:
    """RoutingLLMService.chat() 功能测试。"""

    @pytest.mark.asyncio
    async def test_chat_success_passthrough(self):
        """chat 成功路径：provider 正常返回 → 熔断标记成功 + 计费记录。"""
        from videomind.core.model_gateway.router import RoutingLLMService

        # 构造 mock 依赖
        provider = MagicMock()
        provider.chat = AsyncMock(return_value=_make_response())

        health = MagicMock()
        health.allow_call = AsyncMock(return_value=True)
        health.mark_success = AsyncMock()
        health.mark_failure = AsyncMock()

        accounting = MagicMock()
        accounting.record = AsyncMock()

        db = _make_db()

        svc = RoutingLLMService(provider, "opencode:deepseek", health, accounting)

        response = await svc.chat(_make_request(), db)

        # 响应内容正确传递
        assert response.content == "Hello world"
        assert response.model == "test-model"

        # 熔断器标记成功
        health.mark_success.assert_called_once_with("opencode:deepseek")
        health.mark_failure.assert_not_called()

        # 计费记录
        accounting.record.assert_called_once()
        call_args = accounting.record.call_args
        assert call_args[0][2] is response   # response
        assert call_args[0][3] == "opencode:deepseek"  # model_id
        assert call_args[0][4] == "success"  # status

    @pytest.mark.asyncio
    async def test_chat_sets_provider_on_response(self):
        """chat 成功后应在 response 上设置 provider 字段为 model_id。"""
        from videomind.core.model_gateway.router import RoutingLLMService

        provider = MagicMock()
        provider.chat = AsyncMock(return_value=_make_response(provider=""))

        health = MagicMock()
        health.allow_call = AsyncMock(return_value=True)
        health.mark_success = AsyncMock()

        accounting = MagicMock()
        accounting.record = AsyncMock()

        svc = RoutingLLMService(provider, "opencode:deepseek", health, accounting)

        response = await svc.chat(_make_request(), _make_db())

        assert response.provider == "opencode:deepseek"

    @pytest.mark.asyncio
    async def test_chat_circuit_open_raises(self):
        """熔断打开时 chat 应抛出 AllProvidersFailedError，不调用 provider。"""
        from videomind.core.model_gateway.router import AllProvidersFailedError, RoutingLLMService

        provider = MagicMock()
        provider.chat = AsyncMock()

        health = MagicMock()
        health.allow_call = AsyncMock(return_value=False)

        svc = RoutingLLMService(provider, "broken-model", health, MagicMock())

        with pytest.raises(AllProvidersFailedError) as exc_info:
            await svc.chat(_make_request(), _make_db())

        assert "Circuit OPEN" in str(exc_info.value)
        assert "broken-model" in str(exc_info.value)

        # 确保没有调用 provider
        provider.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_chat_provider_error_marks_failure_and_records(self):
        """Provider 抛异常时：标记熔断失败 + 错误计费 + 抛出 AllProvidersFailedError。"""
        from videomind.core.model_gateway.router import AllProvidersFailedError, RoutingLLMService

        provider = MagicMock()
        provider.chat = AsyncMock(side_effect=ConnectionError("API unreachable"))

        health = MagicMock()
        health.allow_call = AsyncMock(return_value=True)
        health.mark_failure = AsyncMock()
        health.mark_success = AsyncMock()

        accounting = MagicMock()
        accounting.record = AsyncMock()

        svc = RoutingLLMService(provider, "unstable-model", health, accounting)

        with pytest.raises(AllProvidersFailedError) as exc_info:
            await svc.chat(_make_request(), _make_db())

        assert "API unreachable" in str(exc_info.value)
        assert "unstable-model" in str(exc_info.value)

        # 熔断标记失败（不标记成功）
        health.mark_failure.assert_called_once_with("unstable-model")
        health.mark_success.assert_not_called()

        # 计费记录错误调用
        accounting.record.assert_called_once()
        record_args = accounting.record.call_args[0]
        # record(db, request, response, model_id, status, error_code)
        assert record_args[3] == "unstable-model"
        assert record_args[4] == "error"
        assert record_args[5] == "ConnectionError"

    @pytest.mark.asyncio
    async def test_chat_error_accounting_failure_is_masked(self):
        """计费记录自身失败时不影响原始异常的传播。"""
        from videomind.core.model_gateway.router import AllProvidersFailedError, RoutingLLMService

        provider = MagicMock()
        provider.chat = AsyncMock(side_effect=TimeoutError("Request timed out"))

        health = MagicMock()
        health.allow_call = AsyncMock(return_value=True)
        health.mark_failure = AsyncMock()

        accounting = MagicMock()
        accounting.record = AsyncMock(side_effect=RuntimeError("DB dead"))

        svc = RoutingLLMService(provider, "model-x", health, accounting)

        with pytest.raises(AllProvidersFailedError) as exc_info:
            await svc.chat(_make_request(), _make_db())

        assert "Request timed out" in str(exc_info.value)
        # 计费记录被调用但失败了，不应中断原始异常传播
        accounting.record.assert_called_once()
        health.mark_failure.assert_called_once()


class TestRoutingLLMServiceStream:
    """RoutingLLMService.stream_chat() 功能测试。"""

    @pytest.mark.asyncio
    async def test_stream_chat_success_health_checked(self):
        """流式调用：先检查熔断 → 流式传递 chunks → 标记成功。"""
        from videomind.core.model_gateway.router import RoutingLLMService

        chunks = [
            ChatChunk(delta_content="Hello ", model="test"),
            ChatChunk(delta_content="world", model="test"),
        ]

        provider = MagicMock()
        provider.stream_chat = MagicMock(return_value=_async_gen(chunks))

        health = MagicMock()
        health.allow_call = AsyncMock(return_value=True)
        health.mark_success = AsyncMock()

        svc = RoutingLLMService(provider, "stream-model", health, MagicMock())

        received: list[ChatChunk] = []
        async for chunk in svc.stream_chat(_make_request(), _make_db()):
            received.append(chunk)

        assert len(received) == 2
        assert received[0].delta_content == "Hello "
        assert received[1].delta_content == "world"

        health.allow_call.assert_called_once_with("stream-model")
        health.mark_success.assert_called_once_with("stream-model")

    @pytest.mark.asyncio
    async def test_stream_chat_circuit_open_raises(self):
        """流式调用熔断打开时抛出异常。"""
        from videomind.core.model_gateway.router import AllProvidersFailedError, RoutingLLMService

        provider = MagicMock()

        health = MagicMock()
        health.allow_call = AsyncMock(return_value=False)

        svc = RoutingLLMService(provider, "fused-model", health, MagicMock())

        with pytest.raises(AllProvidersFailedError) as exc_info:
            async for _ in svc.stream_chat(_make_request(), _make_db()):
                pass  # 不应进入循环体

        assert "Circuit OPEN" in str(exc_info.value)
        assert "fused-model" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_stream_chat_error_marks_failure(self):
        """流式传输中途出错：标记熔断失败。"""
        from videomind.core.model_gateway.router import AllProvidersFailedError, RoutingLLMService

        async def _faulty_gen():
            """异步生成器：先产出 chunk 再抛异常。"""
            yield ChatChunk(delta_content="first")
            raise ConnectionResetError("Stream broken")

        provider = MagicMock()
        provider.stream_chat = MagicMock(return_value=_faulty_gen())

        health = MagicMock()
        health.allow_call = AsyncMock(return_value=True)
        health.mark_failure = AsyncMock()
        health.mark_success = AsyncMock()

        svc = RoutingLLMService(provider, "stream-model", health, MagicMock())

        with pytest.raises(AllProvidersFailedError) as exc_info:
            async for chunk in svc.stream_chat(_make_request(), _make_db()):
                pass

        assert "Stream failed" in str(exc_info.value)

        health.mark_failure.assert_called_once_with("stream-model")
        health.mark_success.assert_not_called()


# -----------------------------------------------------------------
# RoutingLLMService 生命周期测试
# -----------------------------------------------------------------


class TestRoutingLLMServiceClose:
    """RoutingLLMService.close() 功能测试。"""

    @pytest.mark.asyncio
    async def test_close_delegates_to_provider(self):
        """close() 应代理到 provider.close()。"""
        from videomind.core.model_gateway.router import RoutingLLMService

        provider = MagicMock()
        provider.close = AsyncMock()

        svc = RoutingLLMService(provider, "m", MagicMock(), MagicMock())

        await svc.close()

        provider.close.assert_awaited_once()