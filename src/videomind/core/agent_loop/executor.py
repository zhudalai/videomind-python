"""证据执行器 —— 跨视频真实 RAG 检索 + LLM 结论聚合 + 幻觉过滤."""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from videomind.core.agent_loop.types import (
    AgentState, AnalysisResult, Conclusion, Evidence,
)
from videomind.core.model_gateway.types import ChatRequest

if TYPE_CHECKING:
    from typing import Protocol

    class LLMProtocol(Protocol):
        async def chat(self, request: Any, db: AsyncSession) -> Any: ...

    class RetrieverProtocol(Protocol):
        """检索器最小接口：单视频查询返回命中原始片段 dict 列表."""

        async def search(
            self, query: str, media_id: uuid.UUID, *, top_k: int, recall_k: int | None = None,
        ) -> list[dict]: ...

    class ChatRequestProtocol(Protocol):
        """LLM 聊天请求最小接口（避免动态类型创建）."""
        messages: list[dict[str, str]]
        temperature: float

logger = structlog.get_logger(__name__)
# D-α 召回深度分层：宽召回/RRF 窗口 vs rerank 精排后喂 LLM 条数。
# recall_k=None 退化到 top_k（旧行为，向后兼容）。
RECALL_TOP_K = 60
LLM_CONTEXT_TOP_K = 12
MAX_SUGGESTIONS = 5


class _RagRetriever:
    """默认检索器：复用 rag_pipeline.search，开自有 AsyncSession。"""

    async def search(
        self, query: str, media_id: uuid.UUID, *,
        top_k: int = LLM_CONTEXT_TOP_K, recall_k: int | None = RECALL_TOP_K,
    ) -> list[dict]:
        from videomind.core.rag.pipeline import search as rag_search
        from videomind.infrastructure.storage.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            res = await rag_search(db, query, media_id, top_k=top_k, recall_k=recall_k)
        return [
            {"id": e.id, "chunk_id": e.chunk_id, "content": e.content,
             "source_type": e.source_type, "score": e.score,
             "start_ms": e.start_ms, "end_ms": e.end_ms}
            for e in res["evidence"]
        ]


class Executor:
    """跨视频检索真实证据，喂 LLM 产结论，再过滤幻觉引用。

    Args:
        llm: LLM 客户端（``async chat(request)``）。
        retriever: 检索器；None 时用默认 ``_RagRetriever``。测试可注入 mock。
    """

    def __init__(self, llm: Any, retriever: "RetrieverProtocol | None" = None) -> None:
        self._llm = llm
        self._retriever = retriever if retriever is not None else _RagRetriever()

    async def execute(self, state: AgentState, db: AsyncSession) -> AnalysisResult:
        plan = state.plan
        if plan is None:
            logger.info("Executor 收到空 plan，返回空 AnalysisResult")
            return AnalysisResult(title="")

        meta_by_id = {vm.media_id: vm.filename for vm in state.video_meta}
        all_evidence: dict[str, Evidence] = {}
        all_conclusions: list[Conclusion] = []
        all_suggestions: list[str] = []
        title = ""

        for task in plan.tasks:
            query = task.search_query or task.description
            round_hits: list[Evidence] = []

            # 并发检索各 media（M 轴 async gather：各 _RagRetriever.search 开独立 DB
            # session，纯 async safe；墙钟从 O(N×M×L) 降为 O(N×L)，实测 ~3x 提速）
            async def _search_one_mid(mid: str) -> tuple[str, list[Evidence]]:
                try:
                    mid_uuid = uuid.UUID(mid)
                except ValueError:
                    logger.warning("Executor 跳过无效 UUID", mid=mid)
                    return mid, []
                try:
                    hits = await self._retriever.search(
                        query, mid_uuid, top_k=LLM_CONTEXT_TOP_K, recall_k=RECALL_TOP_K,
                    )
                except Exception:
                    logger.warning("Executor 检索失败 media=%s", mid, exc_info=True)
                    return mid, []
                evs = []
                for h in hits:
                    ev = Evidence(
                        id=h["id"],
                        chunk_id=h.get("chunk_id", ""),
                        content=h.get("content", ""),
                        source_type=h.get("source_type", ""),
                        score=float(h.get("score", 0.0)),
                        start_ms=h.get("start_ms"),
                        end_ms=h.get("end_ms"),
                        media_id=mid,
                        media_title=meta_by_id.get(mid, ""),
                    )
                    evs.append(ev)
                return mid, evs

            gathered = await asyncio.gather(
                *[_search_one_mid(mid) for mid in state.media_ids]
            )
            for _mid, evs in gathered:
                for ev in evs:
                    all_evidence[ev.id] = ev
                    round_hits.append(ev)

            context_block = "\n".join(
                f"[{ev.id}] (来源: {ev.media_title}) {ev.content}" for ev in round_hits
            )
            prompt = self._build_prompt(task, context_block)
            try:
                chat_req = ChatRequest(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                )
                resp = await self._llm.chat(chat_req, db)

                try:
                    data = json.loads(resp.content)
                except json.JSONDecodeError:
                    logger.warning("Executor LLM 返回非 JSON，跳过 task=%s", task.id)
                    continue
            except Exception:
                logger.warning("Executor LLM 失败 task=%s", task.id, exc_info=True)
                continue

            if not title:
                title = data.get("title", "")
            for c in data.get("conclusions", []):
                all_conclusions.append(Conclusion(
                    point=c.get("point", ""),
                    evidence_ids=c.get("evidence_ids", []),
                    confidence=float(c.get("confidence", 0.5)),
                ))
            all_suggestions.extend(data.get("suggestions", []))

        state.retrieved_evidence_ids = set(all_evidence.keys())

        # 幻觉过滤：仅保留 evidence_ids 全部命中真实检索集的 conclusion
        real_ids = set(all_evidence.keys())
        final_conclusions = [c for c in all_conclusions if set(c.evidence_ids) <= real_ids]

        if not title:
            title = state.goal[:64] if state.goal else (
                plan.tasks[0].description[:30] if plan.tasks else "分析结果")

        return AnalysisResult(
            title=title,
            conclusions=final_conclusions,
            evidence=list(all_evidence.values()),
            suggestions=list(dict.fromkeys(all_suggestions))[:MAX_SUGGESTIONS],
        )

    @staticmethod
    def _build_prompt(task: Any, context_block: str) -> str:
        # Sanitize user-controlled content to reduce prompt injection risk
        safe_description = task.description.replace("{", "{{").replace("}", "}}")
        safe_context = context_block.replace("{", "{{").replace("}", "}}")
        return (
            "你只能基于以下真实检索到的证据回答，严禁编造新的证据 ID。\n"
            f"子任务：{safe_description}\n\n"
            f"证据上下文：\n{safe_context or '(无命中证据)'}\n\n"
            '返回 JSON：{"title": "...", '
            '"conclusions": [{"point": "...", "evidence_ids": ["EID_..."], "confidence": 0.0}], '
            '"suggestions": ["..."]}\n'
            "请用与用户输入（分析目标）相同的语言回答。"
        )