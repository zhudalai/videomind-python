"""AgentLoop 主循环 —— Planner -> Executor -> Critic 闭环 (<=max_rounds 轮)."""

from __future__ import annotations

import structlog

from videomind.core.agent_loop.types import AgentState, AnalysisResult

logger = structlog.get_logger(__name__)


class AgentLoop:
    """代理主循环。

    每轮执行 Planner -> Executor -> Critic 管线，
    Critic 通过即提前终止，最多 ``max_rounds`` 轮。
    """

    def __init__(self, planner, executor, critic) -> None:
        """初始化主循环。

        Args:
            planner: 任务规划器
            executor: 证据执行器
            critic: 质量评审器
        """
        self._planner = planner
        self._executor = executor
        self._critic = critic

    async def run(self, goal: str, max_rounds: int = 2) -> AnalysisResult:
        """执行分析主循环。

        Args:
            goal: 用户分析目标
            max_rounds: 最大循环轮数，默认 2

        Returns:
            最终分析结果，若未产生结果则返回空 AnalysisResult
        """
        state = AgentState(goal=goal)

        for round_num in range(1, max_rounds + 1):
            state.round = round_num
            state.plan = await self._planner.plan(state)
            state.result = await self._executor.execute(state)
            state.critique = await self._critic.critique(state)

            if state.critique.passed:
                break

        return state.result if state.result else AnalysisResult(title="")