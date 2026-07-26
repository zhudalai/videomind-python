"""证据执行器 —— 逐子任务 LLM 分析 + 证据聚合."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog

from videomind.core.agent_loop.types import (
    AgentState, AnalysisResult, Conclusion, Evidence,
)

if TYPE_CHECKING:
    from typing import Protocol

    class LLMProtocol(Protocol):
        """执行器所需的 LLM 最小接口."""

        async def chat(self, request: Any) -> Any: ...

logger = structlog.get_logger(__name__)
MAX_SUGGESTIONS = 5


class Executor:
    """执行 LLM 子任务分析，生成结构化结论和证据。

    Args:
        llm: LLM 客户端（需实现 ``async chat(ChatRequest) -> ChatResponse``）。
    """

    def __init__(self, llm: Any) -> None:
        """初始化执行器。

        Args:
            llm: 可调用的 LLM 客户端。
        """
        self._llm = llm

    async def execute(self, state: AgentState) -> AnalysisResult:
        """遍历计划中的每个子任务，调用 LLM 生成分析结果。

        Args:
            state: 当前代理状态（主要使用 ``plan.tasks`` 字段）。

        Returns:
            聚合所有子任务分析结论、证据和建议的 AnalysisResult。
            若 plan 为空或解析失败，返回空 AnalysisResult。
        """
        plan = state.plan
        if plan is None:
            logger.info("Executor 收到空 plan，返回空 AnalysisResult")
            return AnalysisResult(title="")

        all_conclusions: list[Conclusion] = []
        all_evidence: list[Evidence] = []
        all_suggestions: list[str] = []
        title = ""

        for task in plan.tasks:
            prompt = (
                f"子任务：{task.description}\n"
                "返回 JSON：{title, conclusions: [{point, evidence_ids, confidence}], "
                "suggestions: [...], evidence: [{id, timestamp_ms, source, content}]}"
            )
            try:
                resp = await self._llm.chat(
                    type("ChatRequest", (),
                         {"messages": [{"role": "user", "content": prompt}],
                          "temperature": 0.1}),
                )
                data = json.loads(resp.content)
            except Exception:
                logger.warning("Executor LLM failed for task %s", task.id, exc_info=True)
                continue

            if not title:
                title = data.get("title", "")

            for c in data.get("conclusions", []):
                all_conclusions.append(Conclusion(
                    point=c.get("point", ""),
                    evidence_ids=c.get("evidence_ids", []),
                    confidence=float(c.get("confidence", 0.5)),
                ))

            for e in data.get("evidence", []):
                all_evidence.append(Evidence(
                    id=e.get("id", ""),
                    timestamp_ms=int(e.get("timestamp_ms", 0)),
                    source=e.get("source", "text"),
                    content=e.get("content", ""),
                    chunk_id=e.get("chunk_id", ""),
                ))

            all_suggestions.extend(data.get("suggestions", []))

        if not title:
            title = plan.tasks[0].description[:30] if plan.tasks else "分析结果"

        return AnalysisResult(
            title=title,
            conclusions=all_conclusions,
            evidence=all_evidence,
            suggestions=list(dict.fromkeys(all_suggestions))[:MAX_SUGGESTIONS],
        )