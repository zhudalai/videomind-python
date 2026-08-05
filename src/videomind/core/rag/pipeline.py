"""RAG 检索管线顶层入口 —— 编排 hybrid retrieval → rerank → context expansion → evidence → trace。

完整流程：
    查询 chunks → HybridRetriever.search → Reranker.rerank（cross-encoder 优先 / 固定权重兜底）
    → ContextExpander.expand → make_evidence_id → record_rag_trace
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from videomind.core.rag.context import ContextExpander
from videomind.core.rag.evidence import make_evidence_id
from videomind.core.rag.rerank_backend import DeterministicRerankerAdapter, get_reranker
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
    recall_k: int | None = None,
    intent_path: dict | None = None,
) -> dict:
    """完整 RAG 检索管线入口。

    流程：
        1. 获取（必要时构建并缓存）该 media 的 HybridRetriever
        2. retriever.search(query, top_k=recall) 获取初始命中（recall_k 宽召回 / top_k 窄喂下游）
        3. Reranker.rerank 按 config.rerank_provider 重排真实命中（cross-encoder 优先，
           固定权重 DeterministicReranker 兜底；API 失败此处再降级）
        4. ContextExpander.expand 在重排序后追加 ±1 邻居（score=0.0 置尾，不参与重排）
        5. 为每个命中分配 evidence_id（应用 top_k 限制）
        6. record_rag_trace 将链路写入 DB

    Args:
        db: SQLAlchemy 异步会话。
        query: 用户自然语言查询（或经 QueryRewriter 改写后的查询/子查询）。
        media_id: 目标视频 media_id。
        top_k: 喂下游最终条数（rerank+expand 后截断），默认 20。
        recall_k: 召回/RRF 宽窗深度（None 退化到 top_k，向后兼容）。
            D-α 设 60 让宽召回 gold（rank 25/57）进候选池。
        intent_path: 可选的意图/改写路径元数据（来自 intent.pipeline），写入 RagTrace
            供可观测性闭环；None 时 trace 不记该字段（保持向后兼容）。

    Returns:
        {
            "context": [str, ...],       # 按重排得分降序排列的 chunk 原文列表（top_k 条）
            "evidence": [Evidence, ...], # 带 EID 锚点的证据摘要列表（top_k 条）
            "raw_hits": [VectorHit, ...] # 混合检索原始命中（宽召回窗口，≤recall 条）
        }
    """
    # 1+2. 获取（必要时构建并缓存）该 media 的 HybridRetriever，执行混合检索
    # D-α 分层召回：recall_k 宽召回 / top_k 喂下游窄窗口。recall_k=None → recall==top_k（旧行为）。
    retriever = await _get_retriever(db, media_id)
    recall = recall_k if recall_k is not None else top_k
    raw_hits: list[VectorHit] = await retriever.search(query, top_k=recall)

    # 3. 重排序（cross-encoder 优先，固定权重兜底）—— 只重排真实检索命中。
    # 由 get_reranker() 按 config.rerank_provider 选后端（off/api/local），缺配置/缺依赖
    # 自动降级 DeterministicRerankerAdapter。API 后端调用失败时此处再 try/except 降级，
    # 重排链路总有可用结果（呼应 [[rag-pipeline-rerank-expand-order]] 不变式）。
    # 传 dataclasses.replace 拷贝互相隔离：Determine/API/Local rerank 都不应污染 raw_hits
    # 的原始检索分（写进 trace 的 fused_results 必须保真），双保险。
    rerank_input = [replace(h) for h in raw_hits]
    reranker = get_reranker()
    try:
        reranked: list[VectorHit] = await reranker.rerank(query, rerank_input)
    except Exception:
        # 远程后端超时/限流/5xx → 降级固定权重，不让重排阻断检索
        reranked = await DeterministicRerankerAdapter().rerank(query, rerank_input)

    # 4. 上下文扩展 —— 在重排序后追加 ±1 邻居。
    # 邻居以 score=0.0 追加到重排序列末尾，不参与 rerank（旧实现 expand→rerank 会让
    # neighbor 靠 position(0.3)+source(0.2) 抢占真实命中排序）。expand 基于重排后的序，
    # 邻居插入位置由其关联真实命中的位置决定。
    expander = ContextExpander()
    expanded: list[VectorHit] = await expander.expand(db, media_id, reranked)

    # 5. 分配 evidence_id（应用 top_k 限制）+ score min-max 归一化
    # evidence.score 归一到 [0,1]，修复混合尺度 bug：cross-encoder（Nemotron raw 中文 ~0.16）
    # 与降级 Deterministic（线性加权 ~0.5-0.95）跨 query/media 合并时尺度不一，降级高分挤掉真实
    # 高相关 hit；前端按 evidence.score*100 显示百分比也需 [0,1]。
    # min-max 仅对真实命中（rerank 给的分 > 0）归一；邻居（score=0.0 置尾）不纳入统计保持 0.0，
    # 跨 query 合并时邻居 0.0 自然垫底不与真命中竞争。raw_hits 不动、trace 记归一前原值保真。
    expanded_top = expanded[:top_k]
    real_scores = [h.score for h in expanded_top if h.score > 0]
    lo = min(real_scores) if real_scores else 0.0
    hi = max(real_scores) if real_scores else 0.0
    span = hi - lo

    evidence_list: list[Evidence] = []
    context_list: list[str] = []
    for i, hit in enumerate(expanded_top):
        eid = make_evidence_id(uuid.UUID(hit.chunk_id), i)
        norm = hit.score
        if hit.score > 0 and real_scores:
            # 真实命中 min-max 归一；span≈0（所有真命中同分）退化到 1.0 保留高分概念
            norm = 1.0 if span < 1e-9 else (hit.score - lo) / span
        evidence_list.append(
            Evidence(
                id=eid,
                chunk_id=hit.chunk_id,
                content=hit.content,
                score=norm,
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
            intent_path=intent_path,
        )
    except Exception:
        pass  # trace 记录失败不应阻断检索

    # D-α：raw_hits 返回全召回窗口（≤recall），供 run_eval 看宽召回下 gold 的 RRF rank。
    # recall_k=None 时 recall==top_k，raw_hits[:top_k] 与旧行为一致（向后兼容）。
    return {
        "context": context_list,
        "evidence": evidence_list,
        "raw_hits": raw_hits[:recall],
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


__all__ = ["Evidence", "search", "invalidate_retriever_cache"]


# ──────────────────────────── HybridRetriever 进程内缓存 ────────────────────────────
# media_id → HybridRetriever 单例。避免每次检索都 select(Chunk) 拉全部 content 到
# 内存并重建 BM25 索引（长视频 1167 文档 jieba 分词开销显著）。ready 视频 chunks
# 不再变化，故无 TTL；新 chunk 写入后须调 invalidate_retriever_cache(media_id) 失效。
_RETRIEVER_CACHE: dict[uuid.UUID, HybridRetriever] = {}


async def _get_retriever(db: AsyncSession, media_id: uuid.UUID) -> HybridRetriever:
    """按 media_id 获取（必要时构建并缓存）HybridRetriever。

    首次调用从 DB 拉该 media 全部 Chunk 并构造 HybridRetriever（含 BM25 索引），
    存入进程缓存；后续调用直接命中缓存，跳过 DB 全量拉取与索引重建。

    Args:
        db: SQLAlchemy 异步会话。
        media_id: 目标视频 media_id。

    Returns:
        该 media 的 HybridRetriever 实例（缓存单例）。
    """
    cached = _RETRIEVER_CACHE.get(media_id)
    if cached is not None:
        return cached
    result = await db.execute(select(Chunk).where(Chunk.media_id == media_id))
    chunks: list[Chunk] = list(result.scalars().all())
    retriever = HybridRetriever(media_id, chunks)
    _RETRIEVER_CACHE[media_id] = retriever
    return retriever


def invalidate_retriever_cache(media_id: uuid.UUID | None = None) -> None:
    """失效 HybridRetriever 缓存。

    视频处理管线写入新 chunk 后须调用以让后续检索重建索引；
    media_id 为 None 时清空全部缓存（慎用）。

    Args:
        media_id: 指定失效的 video；None 则清空所有。
    """
    if media_id is None:
        _RETRIEVER_CACHE.clear()
    else:
        _RETRIEVER_CACHE.pop(media_id, None)