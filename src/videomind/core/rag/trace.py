"""RAG 检索链路追踪 DB 写入。

提供 record_rag_trace 协程函数，将一次完整的 RAG 检索链路保存到 PostgreSQL，
供评测/调试审计使用。
"""

from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from videomind.infrastructure.storage.models import RagTrace


async def record_rag_trace(
    db: AsyncSession,
    *,
    query: str,
    rewritten_queries: list[str] | None = None,
    intent_path: dict | None = None,
    retrieval_channels: list[dict] | None = None,
    fused_results: list[dict] | None = None,
    expanded_results: list[dict] | None = None,
    reranked_results: list[dict] | None = None,
    final_context: dict | None = None,
    latency_ms: int | None = None,
    token_usage: dict | None = None,
    task_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> RagTrace:
    """记录一次完整的 RAG 检索链路，供评测/调试审计。

    参数:
        db: SQLAlchemy AsyncSession。
        query: 用户原始查询文本（必填）。
        rewritten_queries: 查询改写后的变体列表。
        intent_path: 意图识别路径（如 {"intent": "summarize", "confidence": 0.92}）。
        retrieval_channels: 各通道检索结果（如 [{"bm25": 3, "vector": 4}]）。
        fused_results: 多通道融合后的排序结果列表。
        expanded_results: 查询扩展后的补充结果列表。
        reranked_results: 确定性重排后的最终排序列表。
        final_context: 最终提供给 LLM 的上下文（文本+元信息）。
        latency_ms: 检索总耗时（毫秒）。
        token_usage: Token 用量统计（prompt/completion/total）。
        task_id: 关联的 analysis_task.id。
        user_id: 关联的 user.id。

    Returns:
        RagTrace: 已添加到 session 的追踪记录（flush 后 id/created_at 由 DB 填充）。
    """
    trace = RagTrace(
        query=query,
        rewritten_queries=rewritten_queries,
        intent_path=intent_path,
        retrieval_channels=retrieval_channels,
        fused_results=fused_results,
        expanded_results=expanded_results,
        reranked_results=reranked_results,
        final_context=final_context,
        latency_ms=latency_ms,
        token_usage=token_usage,
        task_id=task_id,
        user_id=user_id,
    )
    db.add(trace)
    await db.flush()
    return trace