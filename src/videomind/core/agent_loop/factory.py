"""AgentLoop 工厂 —— 装配 Planner/Executor/Critic/Verifier."""

from __future__ import annotations

from functools import lru_cache

from videomind.core.agent_loop.loop import AgentLoop
from videomind.core.agent_loop.planner import Planner
from videomind.core.agent_loop.executor import Executor
from videomind.core.agent_loop.critic import Critic
from videomind.core.agent_loop.verifier import EvidenceVerifier
from videomind.core.model_gateway.factory import get_llm_service


@lru_cache
def get_agent_loop() -> AgentLoop:
    """装配并返回 AgentLoop 单例。

    从 model_gateway 获取 LLM 服务，组装 Planner/Executor/Critic/Verifier，
    返回完整的 AgentLoop 实例。

    Returns:
        AgentLoop: 装配好的代理循环实例（lru_cache 单例）。
    """
    llm = get_llm_service()
    return AgentLoop(
        planner=Planner(llm),
        executor=Executor(llm),
        critic=Critic(llm, EvidenceVerifier()),
    )