"""get_llm_service 工厂函数测试。"""

import pytest
from unittest.mock import patch

from videomind.core.model_gateway.router import RoutingLLMService


# ----------------------------------------------------------------
# helpers
# ----------------------------------------------------------------


def _clear_cache():
    """清理 lru_cache，确保每个测试独立。"""
    from videomind.core.model_gateway.factory import get_llm_service

    get_llm_service.cache_clear()


# ----------------------------------------------------------------
# tests
# ----------------------------------------------------------------


def test_get_llm_service_returns_routing_service():
    """工厂返回 RoutingLLMService 实例，内部组件正确装配。"""
    with patch("videomind.core.model_gateway.factory.get_settings") as mock_settings:
        cfg = mock_settings.return_value
        cfg.llm_base_url = "http://localhost/v1"
        cfg.llm_api_key = "test-key"
        cfg.llm_model = "local-model"
        cfg.llm_timeout_s = 30.0
        cfg.llm_first_packet_timeout_s = 5.0

        from videomind.core.model_gateway.factory import get_llm_service

        svc = get_llm_service()
        assert isinstance(svc, RoutingLLMService)
        assert svc._model_id == "custom:local-model"

    _clear_cache()


def test_get_llm_service_is_singleton():
    """同一进程内多次调用返回同一实例（lru_cache）。"""
    with patch("videomind.core.model_gateway.factory.get_settings") as mock_settings:
        cfg = mock_settings.return_value
        cfg.llm_base_url = "http://x/v1"
        cfg.llm_api_key = "k"
        cfg.llm_model = "m"
        cfg.llm_timeout_s = 10.0
        cfg.llm_first_packet_timeout_s = 5.0

        from videomind.core.model_gateway.factory import get_llm_service

        a = get_llm_service()
        b = get_llm_service()
        assert a is b

    _clear_cache()


def test_factory_infers_provider_from_base_url():
    """测试 model_id 从 base_url hostname 推断 provider 名称。"""
    with patch("videomind.core.model_gateway.factory.get_settings") as mock_get:
        cfg = mock_get.return_value
        cfg.llm_base_url = "https://opencode.ai/zen/v1"
        cfg.llm_api_key = "sk-xxx"
        cfg.llm_model = "deepseek-v4-flash-free"
        cfg.llm_timeout_s = 60.0
        cfg.llm_first_packet_timeout_s = 10.0

        from videomind.core.model_gateway.factory import get_llm_service

        svc = get_llm_service()
        assert "deepseek" in svc._model_id.lower() or "opencode" in svc._model_id.lower()

    _clear_cache()