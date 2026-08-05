"""RAG 检索/对话路由 —— 暴露 core/rag pipeline 为 HTTP。

POST /api/rag/search  → 混合检索（多 media 合并），返回带 evidence_id 的证据
POST /api/rag/chat    → 检索 + LLM 生成自然语言答案 + 证据引用

复用 core/rag/pipeline.search（单 media 全链路）+ model_gateway 生成回答。
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from videomind.core.rag import pipeline as rag_pipeline
from videomind.infrastructure.storage import models as m
from videomind.infrastructure.storage.database import get_db

router = APIRouter(tags=["rag"], prefix="/rag")


# ──────────────────────────── Pydantic Schema ────────────────────────────


class RagSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    media_ids: list[uuid.UUID] | None = Field(None, description="目标视频；空则报错")
    top_k: int = Field(10, ge=1, le=50)


class RagSearchResultItem(BaseModel):
    chunk_id: str
    media_id: str
    content: str
    score: float
    start_ms: int | None = None
    end_ms: int | None = None
    evidence_id: str | None = None


class RagChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    media_ids: list[uuid.UUID] | None = Field(None)
    session_id: uuid.UUID | None = Field(None, description="auth 前多轮记忆尚未持久化，仅供参考")
    top_k: int = Field(10, ge=1, le=50)


class RagChatResponse(BaseModel):
    answer: str
    evidence: list[RagSearchResultItem]
    session_id: str


# ──────────────────────────── Routes ────────────────────────────


@router.post("/search", response_model=list[RagSearchResultItem])
async def rag_search(req: RagSearchRequest, db: AsyncSession = Depends(get_db)) -> list[RagSearchResultItem]:
    """多 media 混合检索：对每个 media 跑 pipeline.search 再合并、按 score 降序取 top_k。"""
    targets = req.media_ids or []
    if not targets:
        raise HTTPException(status_code=400, detail="media_ids 不能为空")

    all_evidence: list[dict[str, Any]] = []

    # 校验所有 media（存在 + ready）
    for media_id in targets:
        media = await db.get(m.MediaFile, media_id)
        if not media:
            raise HTTPException(status_code=404, detail=f"media {media_id} not found")
        if media.status != "ready":
            raise HTTPException(status_code=409, detail=f"media {media_id} 未就绪（status={media.status}）")

    # 并行检索各 media（开独立 session 避免 identity map 冲突）
    from videomind.infrastructure.storage.database import AsyncSessionLocal as _SubSession

    async def _search_mid(mid: uuid.UUID) -> list[dict]:
        async with _SubSession() as sub_db:
            result = await rag_pipeline.search(
                sub_db, req.query, mid, top_k=req.top_k,
            )
        return [
            {
                "chunk_id": ev.chunk_id,
                "media_id": str(mid),
                "content": ev.content,
                "score": ev.score,
                "start_ms": ev.start_ms,
                "end_ms": ev.end_ms,
                "evidence_id": ev.id,
            }
            for ev in result.get("evidence", [])
        ]

    for ev_batch in await asyncio.gather(*[_search_mid(mid) for mid in targets]):
        all_evidence.extend(ev_batch)

    all_evidence.sort(key=lambda x: x["score"], reverse=True)
    return all_evidence[: req.top_k]


@router.post("/chat", response_model=RagChatResponse)
async def rag_chat(req: RagChatRequest, db: AsyncSession = Depends(get_db)) -> RagChatResponse:
    """检索 + LLM 生成：查询改写 → 多路检索证据 → model_gateway 生成带引用的回答。

    意图改写（QueryRewriter）同步接通 intent 管线：将用户查询拆为改写主查询 + 2-4
    子问题，对每个子问题在 target media 上多路检索，合并去重证据后再喂 LLM。
    改写失败（LLM 不可用/低置信度）时 fail-open，回退原始单查询检索，不阻断答疑。
    """
    targets = req.media_ids or []
    if not targets:
        raise HTTPException(status_code=400, detail="media_ids 不能为空")

    # 0. 校验 media（存在 + ready）并预取 title/duration 供改写上下文
    media_ctx: dict[uuid.UUID, dict] = {}
    for media_id in targets:
        media = await db.get(m.MediaFile, media_id)
        if not media:
            raise HTTPException(status_code=404, detail=f"media {media_id} not found")
        if media.status != "ready":
            raise HTTPException(status_code=409, detail=f"media {media_id} 未就绪")
        media_ctx[media_id] = {
            "title": (media.meta_json or {}).get("title") or media.filename,
            "duration_sec": (media.duration_ms // 1000) if media.duration_ms else None,
        }

    # 1. 查询改写（intent QueryRewriter）—— fail-open，失败回退原始 query
    queries: list[str] = [req.query]
    intent_path: dict | None = None
    try:
        from videomind.core.intent.pipeline import get_intent_service
        from videomind.core.intent.types import RewriteContext

        svc = get_intent_service()
        # 选第一个 media 作为改写上下文（多视频取首个即可，改写不依赖完整列表）
        first_ctx = media_ctx[targets[0]]
        rewritten = await svc.rewriter.rewrite(
            req.query,
            RewriteContext(
                video_title=first_ctx["title"],
                duration_sec=first_ctx["duration_sec"],
            ),
        )
        # sub_queries 含原始查询的拆解；置信度低于阈值的 llm 结果由 RuleRewriter 兜底
        if rewritten.sub_queries:
            queries = rewritten.sub_queries
        intent_path = {
            "method": rewritten.method,
            "confidence": rewritten.confidence,
            "rewritten": rewritten.rewritten,
            "sub_queries": rewritten.sub_queries,
        }
    except Exception:
        # 改写失败不阻断检索；queries 保持 [req.query]
        pass

    # 2. 多路检索证据（N query × M media），按 (chunk_id, media_id) 去重，取最高分
    #    M 轴并发 async gather：各 search 开独立 DB session 避免 identity map 写冲突
    dedup: dict[tuple[str, str], dict[str, Any]] = {}
    for q in queries:
        from videomind.infrastructure.storage.database import AsyncSessionLocal as _SubSession

        async def _search_mid(mid: uuid.UUID, q: str = q) -> tuple[uuid.UUID, dict]:
            async with _SubSession() as sub_db:
                result = await rag_pipeline.search(
                    sub_db, q, mid, top_k=req.top_k, intent_path=intent_path,
                )
            return mid, result

        for mid, result in await asyncio.gather(
            *[_search_mid(mid) for mid in targets]
        ):
            for ev in result.get("evidence", []):
                key = (ev.chunk_id, str(mid))
                prev = dedup.get(key)
                if prev is None or ev.score > prev["score"]:
                    dedup[key] = {
                        "chunk_id": ev.chunk_id,
                        "media_id": str(mid),
                        "content": ev.content,
                        "score": ev.score,
                        "start_ms": ev.start_ms,
                        "end_ms": ev.end_ms,
                        "evidence_id": ev.id,
                    }
    evidence_items = sorted(dedup.values(), key=lambda x: x["score"], reverse=True)
    top = evidence_items[: req.top_k]

    # 3. 拼证据 context，调 LLM 生成回答（生成失败则回退证据拼接）
    answer = ""
    try:
        from videomind.core.model_gateway.factory import get_llm_service
        from videomind.core.model_gateway.types import ChatRequest

        context_block = "\n\n".join(
            f"[{e['evidence_id']}] {e['content']}" for e in top if e.get("evidence_id")
        )
        if not context_block:
            context_block = "（无可用证据片段）"

        messages = [
            {
                "role": "system",
                "content": (
                    "你是 VideoMind 视频问答助手。基于给定的视频证据片段回答用户问题。"
                    "在回答中用 [EID_xxx_xx] 形式内联引用证据。"
                    "若证据不足以回答，如实说明。"
                    "请用与用户输入相同的语言回答。"
                ),
            },
            {
                "role": "user",
                "content": f"问题：{req.query}\n\n证据片段：\n{context_block}",
            },
        ]
        llm = get_llm_service()
        resp = await llm.chat(ChatRequest(messages=messages, temperature=0.3), db)
        answer = resp.content
    except Exception as exc:  # noqa: BLE001 —— LLM 失败不阻断，回退证据摘要仍可交付
        answer = f"（LLM 生成失败：{type(exc).__name__}，以下为检索证据摘要）\n\n" + "\n\n".join(
            f"[{e['evidence_id']}] {e['content'][:200]}" for e in top if e.get("evidence_id")
        )

    return RagChatResponse(
        answer=answer,
        evidence=[RagSearchResultItem(**e) for e in top],
        session_id=(str(req.session_id) if req.session_id else str(uuid.uuid4())),
    )
