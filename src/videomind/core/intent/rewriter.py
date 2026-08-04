"""查询改写模块：LLM 改写 + 规则兜底."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import structlog

from videomind.core.intent.types import RewriteContext, RewriteResult
from videomind.core.model_gateway.types import ChatRequest

logger = structlog.get_logger(__name__)


class RuleRewriter:
    """规则兜底改写器."""

    SYNONYMS = {
        "视频": ["影片", "录像", "片子"],
        "内容": ["讲了什么", "说什么", "主题"],
        "总结": ["摘录", "概括", "大义"],
        "原话": ["逐字", "文字记录", "转录"],
        "画面": ["视频画面", "画面内容", "画面里"],
    }

    def rewrite(self, query: str, _context: RewriteContext | None = None) -> RewriteResult:
        expanded = query
        for k, vs in self.SYNONYMS.items():
            if k in query:
                expanded += " " + " ".join(vs)

        sub_queries = list(set(re.split(r"[和,，]", query)))
        sub_queries = [q.strip() for q in sub_queries if len(q.strip()) >= 2]
        if len(sub_queries) == 1:
            sub_queries = [query]
        sub_queries = sub_queries[:4]  # 最多 4 个

        return RewriteResult(
            original=query,
            rewritten=expanded,
            sub_queries=sub_queries,
            method="rule",
            confidence=0.6,
        )


class QueryRewriter:
    """LLM 查询改写（优先）+ 规则兜底（fallback）。

    Args:
        llm: LLM 客户端（需实现 async chat(ChatRequest) -> ChatResponse）。
        confidence_threshold: LLM 置信度阈值，低于此值降级规则改写（D-β 默认 0.5，原 0.7）。
    """

    def __init__(self, llm, confidence_threshold: float = 0.5):
        self._llm = llm
        self._threshold = confidence_threshold
        self._rule_rw = RuleRewriter()

    async def rewrite(self, query: str, context: RewriteContext, *, db: Any = None) -> RewriteResult:
        prompt = (
            "你是视频理解助手的查询改写器。用户查询改写和拆解。\n\n"
            f"用户查询：{query}\n"
            f"视频标题：{context.video_title or '未知'}\n"
            f"视频时长：{context.duration_sec or '未知'} 秒\n\n"
            "任务："
            "1. 补充同义词、专业术语变体\n"
            "2. 实体归一化：标准化人名/地名/机构\n"
            "3. 子问题拆解：复杂查询拆为 2-4 个子问题\n"
            "4. 保留原始查询语义\n\n"
            '无论输入为任意语言都以JSON格式输出：'
            '{"rewritten": "...", "sub_queries": [...], "entities": {...}, "confidence": 0.0}'
        )

        try:
            # db 透传给 RoutingLLMService.chat(req, db) 做计费入库；
            # None 时 accounting 静默跳过入库（LLM 照调），故评测/无 session 路径不阻断。
            # reasoning=False 关 OpenRouter reasoning 模型思维链：nemotron-3-ultra 等
            # 默认吐思考链 + 末尾 JSON，json.loads(整段) 必失败全 fallback rule。
            # 关思维链让模型直接吐纯 JSON，下游 json.loads 可解析（D-β 阈值方能生效）。
            resp = await self._llm.chat(ChatRequest(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=512,
                reasoning=False,
            ), db)
            data = json.loads(resp.content)
            result = RewriteResult(
                original=query,
                rewritten=data.get("rewritten", query),
                sub_queries=data.get("sub_queries", [query]),
                entities=data.get("entities", {}),
                method="llm",
                confidence=data.get("confidence", 0.5),
            )
            if result.confidence >= self._threshold:
                return result
        except Exception:
            logger.warning("LLM rewrite failed, falling back to rule", exc_info=True)

        return self._rule_rw.rewrite(query)