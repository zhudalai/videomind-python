"""RAG 检索/对话路由 —— 暴露 core/rag pipeline 为 HTTP。

POST /api/rag/search  → 混合检索（多 media 合并），返回带 evidence_id 的证据
POST /api/rag/chat    → 检索 + LLM 生成自然语言答案 + 证据引用

复用 core/rag/pipeline.search（单 media 全链路）+ model_gateway 生成回答。
"""

from __future__ import annotations

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
    for media_id in targets:
        media = await db.get(m.MediaFile, media_id)
        if not media:
            raise HTTPException(status_code=404, detail=f"media {media_id} not found")
        if media.status != "ready":
            raise HTTPException(status_code=409, detail=f"media {media_id} 未就绪（status={media.status}）")

        result = await rag_pipeline.search(db, req.query, media_id, top_k=req.top_k)
        for ev in result.get("evidence", []):
            all_evidence.append(
                {
                    "chunk_id": ev.chunk_id,
                    "media_id": str(media_id),
                    "content": ev.content,
                    "score": ev.score,
                    "start_ms": ev.start_ms,
                    "end_ms": ev.end_ms,
                    "evidence_id": ev.id,
                }
            )

    all_evidence.sort(key=lambda x: x["score"], reverse=True)
    return all_evidence[: req.top_k]


@router.post("/chat", response_model=RagChatResponse)
async def rag_chat(req: RagChatRequest, db: AsyncSession = Depends(get_db)) -> RagChatResponse:
    """检索 + LLM 生成：先 search 取证据，再调 model_gateway 生成带引用的回答。"""
    targets = req.media_ids or []
    if not targets:
        raise HTTPException(status_code=400, detail="media_ids 不能为空")

    # 1. 检索证据（与 /search 同逻辑，不过滤 top_k）
    evidence_items: list[dict[str, Any]] = []
    for media_id in targets:
        media = await db.get(m.MediaFile, media_id)
        if not media:
            raise HTTPException(status_code=404, detail=f"media {media_id} not found")
        if media.status != "ready":
            raise HTTPException(status_code=409, detail=f"media {media_id} 未就绪")

        result = await rag_pipeline.search(db, req.query, media_id, top_k=req.top_k)
        for ev in result.get("evidence", []):
            evidence_items.append(
                {
                    "chunk_id": ev.chunk_id,
                    "media_id": str(media_id),
                    "content": ev.content,
                    "score": ev.score,
                    "start_ms": ev.start_ms,
                    "end_ms": ev.end_ms,
                    "evidence_id": ev.id,
                }
            )
    evidence_items.sort(key=lambda x: x["score"], reverse=True)
    top = evidence_items[: req.top_k]

    # 2. 拼证据 context，调 LLM 生成回答（生成失败则回退证据拼接）
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
