"""LLM Service 工厂 —— 从 config 装配 RoutingLLMService。

职责：读取 Settings 配置 → 创建 OpenAICompatibleClient、ModelHealthStore、
      TokenAccounting → 装配 RoutingLLMService → lru_cache 单例返回。
"""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlparse

from videomind.config import get_settings
from videomind.core.model_gateway.accounting import TokenAccounting
from videomind.core.model_gateway.circuit import ModelHealthStore
from videomind.core.model_gateway.http_client import OpenAICompatibleClient
from videomind.core.model_gateway.router import RoutingLLMService


def _infer_provider(base_url: str) -> str:
    """从 base_url hostname 取 provider 缩写。

    Args:
        base_url: LLM API 基地址。

    Returns:
        provider 名称，如 "opencode"、"openrouter"、"custom"。
    """
    try:
        host = urlparse(base_url).hostname or "custom"
        low = host.lower()
        if "opencode" in low:
            return "opencode"
        if "openrouter" in low:
            return "openrouter"
        if "deepseek" in low:
            return "deepseek"
        if host.endswith(".com") or host.endswith(".ai"):
            parts = host.split(".")
            return parts[-3] if len(parts) >= 3 else parts[0]
    except Exception:
        pass
    return "custom"


@lru_cache
def get_llm_service() -> RoutingLLMService:
    """装配并返回 RoutingLLMService 单例。

    读取 Settings 中的 LLM 配置（llm_base_url / llm_api_key / llm_model / llm_timeout_s），
    创建 OpenAI 兼容 HTTP 客户端、内存熔断器、Token 计费模块，装配为路由网关。

    Returns:
        RoutingLLMService: 可直接用于 chat / stream_chat 调用的路由网关单例。
    """
    s = get_settings()
    provider_name = _infer_provider(s.llm_base_url)
    model_id = f"{provider_name}:{s.llm_model}"

    client = OpenAICompatibleClient(
        base_url=s.llm_base_url,
        api_key=s.llm_api_key,
        default_model=s.llm_model,
        timeout=s.llm_timeout_s,
    )
    health = ModelHealthStore(redis=None)
    accounting = TokenAccounting()
    return RoutingLLMService(client, model_id, health, accounting)


__all__ = ["get_llm_service"]