"""任务规划器 —— LLM 驱动将目标拆解为 1-5 个可执行子任务."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog

from videomind.core.agent_loop.types import AgentPlan, AgentState, SubTask

if TYPE_CHECKING:
    from typing import Protocol

    class LLMProtocol(Protocol):
        """:class:`Planner` 所需的 LLM 最小接口."""

        async def chat(self, request: Any) -> Any: ...

logger = structlog.get_logger(__name__)

SYSTEM_PROMPT = (
    "你是视频分析任务规划师。将给定的分析目标拆解为 1-5 个可执行子任务。"
    "每个子任务需指定所需证据类型（frame / text / audio / sql）。"
    "如涉及特定时间段，提供 time_range（毫秒）起止值。"
    "每个子任务还需提供 search_query：面向检索的关键词（如\"商业模式 价格 斜率\"），"
    "而非完整问句——它将直接送入向量检索。"
    "输出 JSON 格式："
    '{"tasks": [{"description": "...", "evidence_type": "...", '
    '"time_range": [start_ms, end_ms], "search_query": "..."}], "reasoning": "..."}'
)

MAX_TASKS = 5


class Planner:
    """任务规划器。

    调用 LLM 将用户目标拆解为可执行子任务清单，
    并通过结构体解析和上限裁剪生成 :class:`AgentPlan`。
    """

    def __init__(self, llm: Any) -> None:
        """初始化规划器。

        Args:
            llm: 可调用的 LLM 客户端，需提供 ``chat(request)`` 异步方法。
        """
        self._llm = llm

    async def plan(self, state: AgentState) -> AgentPlan:
        """根据 AgentState.goal 生成执行计划。

        Args:
            state: 当前代理状态（主要使用 ``goal`` 字段）。

        Returns:
            包含最多 ``MAX_TASKS``（5）个子任务的 AgentPlan。
            发生异常时返回空 AgentPlan。
        """
        video_names = [vm.filename for vm in state.video_meta]
        video_section = (
            f"\n可用视频（共 {len(video_names)} 个）：{', '.join(video_names)}"
            if video_names else "\n可用视频：未提供"
        )
        prompt = f"{SYSTEM_PROMPT}\n\n用户目标：{state.goal}{video_section}"
        request = type("ChatRequest", (), {
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        })()

        try:
            resp = await self._llm.chat(request)
            data = json.loads(resp.content)
        except Exception:
            logger.warning("Planner 调用 LLM 或 JSON 解析失败", exc_info=True)
            return AgentPlan(tasks=[], reasoning="")

        try:
            tasks = [
                SubTask(
                    id=f"task_{i + 1}",
                    description=task.get("description", ""),
                    required_evidence_type=task.get("evidence_type", "text"),
                    time_range_hint=_parse_time_range(task),
                    search_query=task.get("search_query", ""),
                )
                for i, task in enumerate(data.get("tasks", [])[:MAX_TASKS])
            ]
            reasoning = data.get("reasoning", "")
        except Exception:
            logger.warning("Planner 构造 SubTask 失败", exc_info=True)
            return AgentPlan(tasks=[], reasoning="")

        return AgentPlan(tasks=tasks, reasoning=reasoning)


def _parse_time_range(task: dict[str, Any]) -> tuple[int, int] | None:
    """从任务字典中提取合法的 time_range 元组。

    Args:
        task: 包含可选 ``"time_range"`` 键的单个任务字典。

    Returns:
        ``(start_ms, end_ms)`` 或 ``None``。
    """
    tr = task.get("time_range")
    if isinstance(tr, list) and len(tr) == 2:
        try:
            return (int(tr[0]), int(tr[1]))
        except (TypeError, ValueError):
            return None
    return None