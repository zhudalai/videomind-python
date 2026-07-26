"""TokenAccounting 计费入库单元测试。

测试 record() 的三种场景：
1. 成功写入 AiCallLog + Prometheus 指标
2. 错误状态（timeout）写入
3. DB 写入失败不泄露异常
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from videomind.core.model_gateway.types import ChatRequest, ChatResponse, TokenUsage


# ----------------------------------------------------------------
# 辅助工厂函数
# ----------------------------------------------------------------


def _make_request(**kwargs) -> ChatRequest:
    """构造 ChatRequest，仅提供测试所需的最小字段。"""
    defaults: dict = {
        "messages": [{"role": "user", "content": "hello"}],
        "trace_id": "abcdef1234567890",
        "task_type": "chat",
    }
    defaults.update(kwargs)
    return ChatRequest(**defaults)


def _make_response(**kwargs) -> ChatResponse:
    """构造 ChatResponse，提供合理的默认值。"""
    defaults: dict = {
        "content": "hi",
        "model": "deepseek",
        "usage": TokenUsage(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
            cost_usd=0.0003,
        ),
        "latency_ms": 200,
        "provider": "opencode",
    }
    defaults.update(kwargs)
    return ChatResponse(**defaults)


# ----------------------------------------------------------------
# 测试用例
# ----------------------------------------------------------------


class TestTokenAccountingRecord:
    """TokenAccounting.record() 功能测试。"""

    @pytest.mark.asyncio
    async def test_record_success(self):
        """成功记录：AiCallLog 写入 DB + Prometheus 指标上报。"""
        from videomind.core.model_gateway.accounting import TokenAccounting

        db = MagicMock()
        db.add = MagicMock()
        db.flush = AsyncMock()

        accounting = TokenAccounting()
        request = _make_request()
        response = _make_response()

        with patch(
            "videomind.core.model_gateway.accounting.LLM_CALL_TOTAL"
        ) as mock_call_total, patch(
            "videomind.core.model_gateway.accounting.LLM_CALL_LATENCY"
        ) as mock_latency, patch(
            "videomind.core.model_gateway.accounting.LLM_TOKEN_USAGE"
        ) as mock_token_usage:
            await accounting.record(
                db, request, response,
                model_id="opencode:deepseek",
                status="success",
            )

        # DB 写入验证
        db.add.assert_called_once()
        db.flush.assert_awaited_once()

        log = db.add.call_args[0][0]
        assert log.provider == "opencode"
        assert log.model == "deepseek"
        assert log.task_type == "chat"
        assert log.prompt_tokens == 10
        assert log.completion_tokens == 5
        assert log.total_tokens == 15
        assert log.cost_usd == 0.0003
        assert log.trace_id == request.trace_id
        assert log.status == "success"
        assert log.error_code is None

        # Prometheus 指标验证
        mock_call_total.labels.assert_called_once_with(
            provider="opencode", model="deepseek", status="success"
        )
        mock_call_total.labels.return_value.inc.assert_called_once()

        mock_latency.labels.assert_called_once_with(
            provider="opencode", model="deepseek"
        )
        mock_latency.labels.return_value.observe.assert_called_once_with(0.2)

        # token 指标应按 type=prompt/completion/total 各调用一次
        assert mock_token_usage.labels.call_count == 3
        mock_token_usage.labels.assert_any_call(provider="opencode", model="deepseek", type="prompt")
        mock_token_usage.labels.assert_any_call(provider="opencode", model="deepseek", type="completion")
        mock_token_usage.labels.assert_any_call(provider="opencode", model="deepseek", type="total")

        # 每个 inc 按对应 token 数值调用
        inc_calls = [c.args for c in mock_token_usage.labels.return_value.inc.call_args_list]
        assert inc_calls == [(10,), (5,), (15,)]

    @pytest.mark.asyncio
    async def test_record_error(self):
        """测试错误状态记录：status="timeout" 且有 error_code。"""
        from videomind.core.model_gateway.accounting import TokenAccounting

        db = MagicMock()
        db.add = MagicMock()
        db.flush = AsyncMock()

        accounting = TokenAccounting()
        request = _make_request()
        response = _make_response()

        with patch(
            "videomind.core.model_gateway.accounting.LLM_CALL_TOTAL"
        ), patch(
            "videomind.core.model_gateway.accounting.LLM_CALL_LATENCY"
        ), patch(
            "videomind.core.model_gateway.accounting.LLM_TOKEN_USAGE"
        ):
            await accounting.record(
                db, request, response,
                model_id="anthropic:claude-sonnet-4-20250514",
                status="timeout",
                error_code="GATEWAY_TIMEOUT",
            )

        log = db.add.call_args[0][0]
        assert log.status == "timeout"
        assert log.error_code == "GATEWAY_TIMEOUT"

    @pytest.mark.asyncio
    async def test_record_no_colon_modeled(self):
        """model_id 不带冒号：整个作为 model，provider 也为自身。"""
        from videomind.core.model_gateway.accounting import TokenAccounting

        db = MagicMock()
        db.add = MagicMock()
        db.flush = AsyncMock()

        accounting = TokenAccounting()
        request = _make_request()
        response = _make_response()

        with patch(
            "videomind.core.model_gateway.accounting.LLM_CALL_TOTAL"
        ), patch(
            "videomind.core.model_gateway.accounting.LLM_CALL_LATENCY"
        ), patch(
            "videomind.core.model_gateway.accounting.LLM_TOKEN_USAGE"
        ):
            await accounting.record(
                db, request, response,
                model_id="ollama",
                status="success",
            )

        log = db.add.call_args[0][0]
        assert log.provider == "ollama"
        assert log.model == "ollama"

    @pytest.mark.asyncio
    async def test_record_db_failure_is_idle(self):
        """DB flush 失败时不应抛出异常，Prometheus 指标仍正常上报。"""
        from videomind.core.model_gateway.accounting import TokenAccounting

        db = MagicMock()
        db.add = MagicMock()
        db.flush = AsyncMock(side_effect=Exception("DB down"))

        accounting = TokenAccounting()
        request = _make_request()
        response = _make_response()

        with patch(
            "videomind.core.model_gateway.accounting.LLM_CALL_TOTAL"
        ) as mock_call_total, patch(
            "videomind.core.model_gateway.accounting.LLM_CALL_LATENCY"
        ), patch(
            "videomind.core.model_gateway.accounting.LLM_TOKEN_USAGE"
        ):
            # 应正常返回，不抛异常
            await accounting.record(
                db, request, response,
                model_id="opencode:deepseek",
                status="success",
            )

        # flush 的异常被静默捕获
        db.flush.assert_awaited_once()
        # Prometheus 指标仍应上报（DB 写入失败不阻断指标）
        mock_call_total.labels.return_value.inc.assert_called_once()

    @pytest.mark.asyncio
    async def test_record_default_task_type(self):
        """request.task_type 为 None 时默认使用 "chat"。"""
        from videomind.core.model_gateway.accounting import TokenAccounting

        db = MagicMock()
        db.add = MagicMock()
        db.flush = AsyncMock()

        accounting = TokenAccounting()
        request = _make_request(task_type=None)
        response = _make_response()

        with patch(
            "videomind.core.model_gateway.accounting.LLM_CALL_TOTAL",
        ), patch(
            "videomind.core.model_gateway.accounting.LLM_CALL_LATENCY"
        ), patch(
            "videomind.core.model_gateway.accounting.LLM_TOKEN_USAGE"
        ):
            await accounting.record(
                db, request, response,
                model_id="openai:gpt-4o",
                status="success",
            )

        log = db.add.call_args[0][0]
        assert log.task_type == "chat"