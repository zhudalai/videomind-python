"""RAG 检索管线顶层入口 —— 编排 hybrid retrieval → context expansion → rerank → evidence → trace。

完整流程：
    查询 chunks → HybridRetriever.search → ContextExpander.expand
    → DeterministicReranker.rerank → make_evidence_id → record_rag_trace
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from videomind.core.rag.context import ContextExpander
from videomind.core.rag.evidence import make_evidence_id
from videomind.core.rag.rerank import DeterministicReranker
from videomind.core.rag.retriever import HybridRetriever
from videomind.core.rag.trace import record_rag_trace
from videomind.core.rag.vector import VectorHit
from videomind.infrastructure.storage.models import Chunk


@dataclass
class Evidence:
    """带证据 ID 的检索结果片段。

    Attributes:
        id: 证据锚点 ID，格式 EID_{hex8}_{idx+1:02d}。
        chunk_id: 对应 Chunk 的 UUID 字符串。
        content: chunk 文本内容。
        score: 最终重排得分。
        start_ms: 片段起始毫秒（可能为空）。
        end_ms: 片段结束毫秒（可能为空）。
        source_type: 来源类型（asr/ocr/mixed）。
    """

    id: str
    chunk_id: str
    content: str
    score: float
    start_ms: int | None = None
    end_ms: int | None = None
    source_type: str = ""


async def search(
    db: AsyncSession,
    query: str,
    media_id: uuid.UUID,
    *,
    top_k: int = 20,
) -> dict:
    """完整 RAG 检索管线入口。

    流程：
        1. 从 DB 查询 media_id 下所有 Chunk
        2. 构造 HybridRetriever（含 BM25 索引构建）
        3. 调用 retriever.search(query, top_k=top_k) 获取初始命中
        4. ContextExpander.expand 做邻居扩展
        5. DeterministicReranker.rerank 做确定性重排序
        6. 为每个重排命中分配 evidence_id
        7. record_rag_trace 将链路写入 DB

    Args:
        db: SQLAlchemy 异步会话。
        query: 用户自然语言查询。
        media_id: 目标视频 media_id。
        top_k: 检索通道单通道最大返回条数，默认 20。

    Returns:
        {
            "context": [str, ...],       # 按重排得分降序排列的 chunk 原文列表
            "evidence": [Evidence, ...], # 带 EID 锚点的证据摘要列表
            "raw_hits": [VectorHit, ...] # 混合检索原始命中（扩展前）
        }
    """
    # 1. 查询该 media 下所有 chunks
    result = await db.execute(
        select(Chunk).where(Chunk.media_id == media_id)
    )
    chunks: list[Chunk] = list(result.scalars().all())

    # 2. 混合检索
    retriever = HybridRetriever(media_id, chunks)
    raw_hits: list[VectorHit] = await retriever.search(query, top_k=top_k)

    # 3. 上下文扩展
    expander = ContextExpander()
    expanded: list[VectorHit] = await expander.expand(db, media_id, raw_hits)

    # 4. 确定性重排序
    reranker = DeterministicReranker()
    reranked: list[VectorHit] = reranker.rerank(expanded)

    # 5. 分配 evidence_id
    evidence_list: list[Evidence] = []
    context_list: list[str] = []
    for i, hit in enumerate(reranked):
        eid = make_evidence_id(uuid.UUID(hit.chunk_id), i)
        evidence_list.append(
            Evidence(
                id=eid,
                chunk_id=hit.chunk_id,
                content=hit.content,
                score=hit.score,
                start_ms=hit.start_ms,
                end_ms=hit.end_ms,
                source_type=hit.source_type,
            )
        )
        context_list.append(hit.content)

    # 6. 记录 trace
    try:
        await record_rag_trace(
            db,
            query=query,
            fused_results=[_hit_to_dict(h) for h in raw_hits],
            expanded_results=[_hit_to_dict(h) for h in expanded],
            reranked_results=[_hit_to_dict(h) for h in reranked],
            final_context={
                "context": context_list,
                "evidence": [e.id for e in evidence_list],
            },
        )
    except Exception:
        pass  # trace 记录失败不应阻断检索

    return {
        "context": context_list,
        "evidence": evidence_list,
        "raw_hits": raw_hits,
    }


def _hit_to_dict(hit: VectorHit) -> dict:
    """将 VectorHit 转为可序列化的 dict（供 trace 使用）。"""
    return {
        "chunk_id": hit.chunk_id,
        "score": hit.score,
        "content": hit.content,
        "start_ms": hit.start_ms,
        "end_ms": hit.end_ms,
        "source_type": hit.source_type,
        "content_hash": hit.content_hash,
    }


__all__ = ["Evidence", "search"]