"""意图树形路由器：关键词 + LLM 融合分类 + 通道配额分配."""

from __future__ import annotations

import json
from typing import Any

import structlog

from videomind.core.intent.types import ChannelQuota, IntentRouteResult
from videomind.core.model_gateway.types import ChatRequest

logger = structlog.get_logger(__name__)

# ── 内置意图树 ──
DEFAULT_INTENT_TREE: list[dict[str, Any]] = [
    {"id": "video_qa", "name": "视频内容问答", "weight": 1.0,
     "keywords": ["什么", "怎么", "为什么", "内容", "讲了", "说了"],
     "children": [
         {"id": "video_qa.summary", "name": "视频摘要", "weight": 0.9,
          "keywords": ["摘要", "总结", "概括", "大義"]},
         {"id": "video_qa.detail", "name": "细节追问", "weight": 0.8,
          "keywords": ["第几分钟", "具体", "详细", "原话"]},
         {"id": "video_qa.quote", "name": "原文引用", "weight": 0.7,
          "keywords": ["原话", "引用", "文字记录", "逐字"]},
     ]},
    {"id": "video_search", "name": "视频检索定位", "weight": 0.9,
     "keywords": ["找", "搜", "哪里", "位置", "时间点", "片段"],
     "children": [
         {"id": "video_search.topic", "name": "主题检索", "weight": 0.9,
          "keywords": ["关于", "相关", "主题", "话题"]},
         {"id": "video_search.speaker", "name": "发言人检索", "weight": 0.7,
          "keywords": ["谁说", "发言人", "说话人", "声音"]},
         {"id": "video_search.visual", "name": "画面检索", "weight": 0.6,
          "keywords": ["画面", "画面里", "出现", "字幕"]},
     ]},
    {"id": "video_analysis", "name": "视频深度分析", "weight": 0.8,
     "keywords": ["分析", "对比", "推理", "原因", "影响", "趋势"],
     "children": [
         {"id": "video_analysis.compare", "name": "对比分析", "weight": 0.8,
          "keywords": ["对比", "区别", "差异", "相比"]},
         {"id": "video_analysis.causal", "name": "因果推理", "weight": 0.7,
          "keywords": ["为什么", "导致", "原因", "影响"]},
         {"id": "video_analysis.timeline", "name": "时间线梳理", "weight": 0.7,
          "keywords": ["时间线", "顺序", "过程", "发展"]},
     ]},
    {"id": "video_generation", "name": "视频衍生内容生成", "weight": 0.7,
     "keywords": ["生成", "写", "制作", "脚本", "笔记", "大纲"],
     "children": [
         {"id": "video_generation.script", "name": "脚本改写", "weight": 0.8,
          "keywords": ["脚本", "文案", "口播稿"]},
         {"id": "video_generation.notes", "name": "学习笔记", "weight": 0.7,
          "keywords": ["笔记", "知识点", "大纲", "思维导图"]},
         {"id": "video_generation.highlights", "name": "精华切片", "weight": 0.6,
          "keywords": ["精华", "高光", "切片", "短视频"]},
     ]},
    {"id": "meta_query", "name": "元数据查询", "weight": 0.5,
     "keywords": ["时长", "作者", "上传", "发布", "标题", "标签"]},
]

DEFAULT_CHANNEL_WEIGHTS: dict[str, dict[str, float]] = {
    "video_qa": {"vector": 0.6, "bm25": 0.3, "sql": 0.1},
    "video_search": {"vector": 0.4, "bm25": 0.5, "sql": 0.1},
    "video_analysis": {"vector": 0.7, "bm25": 0.2, "sql": 0.1},
    "video_generation": {"vector": 0.5, "bm25": 0.3, "sql": 0.2},
    "meta_query": {"vector": 0.1, "bm25": 0.1, "sql": 0.8},
}


# ── 内部数据结构 ──

class _IntentNode:
    """意图树节点."""

    __slots__ = ("id", "name", "weight", "keywords", "children", "parent")

    def __init__(
        self,
        id: str,
        name: str,
        weight: float,
        keywords: list[str],
        children: list[_IntentNode] | None = None,
        parent: _IntentNode | None = None,
    ):
        self.id = id
        self.name = name
        self.weight = weight
        self.keywords = keywords
        self.children: list[_IntentNode] = children or []
        self.parent = parent


class IntentRouter:
    """关键词 0.4 + LLM 0.6 融合的意图分类器。

    Args:
        llm: LLM 客户端，为 None 时仅用关键词匹配。
        tree: 意图树定义列表（默认 DEFAULT_INTENT_TREE）。
        channel_weights: 意图→通道权重映射。
        kw_weight: 关键词分数权重，默认 0.4。
    """

    def __init__(self, llm=None, tree=None, channel_weights=None, kw_weight=0.4):
        self._llm = llm
        self._kw_weight = kw_weight
        self._cw = channel_weights or DEFAULT_CHANNEL_WEIGHTS
        self._all_nodes: dict[str, _IntentNode] = {}
        self._leaf_nodes: list[_IntentNode] = []
        self._build_tree(tree or DEFAULT_INTENT_TREE)

    # ----------------------------------------------------------------
    # tree build
    # ----------------------------------------------------------------
    def _build_tree(self, items: list[dict[str, Any]], parent: _IntentNode | None = None) -> None:
        """递归构建意图树."""
        for item in items:
            node = _IntentNode(
                id=item["id"],
                name=item["name"],
                weight=item.get("weight", 1.0),
                keywords=item.get("keywords", []),
                parent=parent,
            )
            self._all_nodes[node.id] = node
            children = item.get("children")
            if children:
                self._build_tree(children, node)
            else:
                self._leaf_nodes.append(node)

    # ----------------------------------------------------------------
    # keyword match （公开，可直接测试）
    # ----------------------------------------------------------------
    def _keyword_match(self, text: str) -> dict[str, float]:
        """对所有意图节点（含根节点和叶子节点）做关键词命中率打分."""
        scores: dict[str, float] = {}
        for node in self._all_nodes.values():
            hits = sum(1 for kw in node.keywords if kw in text)
            if hits > 0:
                scores[node.id] = hits / max(len(node.keywords), 1)
        return scores

    # ----------------------------------------------------------------
    # llm classify
    # ----------------------------------------------------------------
    async def _llm_classify(self, text: str, candidates: list[str]) -> dict[str, float]:
        """调用 LLM 对候选意图打分."""
        if not self._llm or not candidates:
            return {}
        candidate_lines = "\n".join(
            f"- {c}: {self._all_nodes[c].name}" if c in self._all_nodes else f"- {c}"
            for c in candidates[:10]
        )
        prompt = (
            "判断查询属于哪些意图（一个分数0-1）。\n\n"
            f"查询：{text}\n候选意图：\n{candidate_lines}\n"
            "输出JSON：{\"intent_id\": 0.0, ...}"
        )
        try:
            resp = await self._llm.chat(
                ChatRequest(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=256,
                )
            )
            return json.loads(resp.content)
        except Exception:
            logger.warning("LLM classify failed", exc_info=True)
            return {}

    # ----------------------------------------------------------------
    # route 主入口
    # ----------------------------------------------------------------
    async def route(self, query: str, sub_queries: list[str]) -> IntentRouteResult:
        """执行意图路由：关键词匹配 + LLM 融合 + 通道配额分配.

        Args:
            query: 原始用户查询（改写后）。
            sub_queries: 拆解后的子问题列表。

        Returns:
            IntentRouteResult: 包含意图权重、通道配额和主意图。
        """
        all_text = " ".join([query] + sub_queries)

        # 1. keyword scores
        kw_scores = self._keyword_match(all_text)

        # 2. LLM scores (top 10 kw candidates)
        top_ids = sorted(kw_scores, key=kw_scores.get, reverse=True)[:10]
        llm_scores = await self._llm_classify(all_text, top_ids)

        # 3. fusion
        fused: dict[str, float] = {}
        for nid in self._all_nodes:
            kw = kw_scores.get(nid, 0.0)
            llm = llm_scores.get(nid, 0.0)
            fused[nid] = self._kw_weight * kw + (1.0 - self._kw_weight) * llm

        # 4. upward aggregation (parent gets max of children * parent.weight)
        for node in self._all_nodes.values():
            if node.children:
                cmax = max(fused.get(c.id, 0.0) for c in node.children)
                fused[node.id] = max(fused.get(node.id, 0.0), cmax * node.weight)

        # 5. normalize + filter
        total = sum(fused.values()) or 1.0
        intent_weights = {k: v / total for k, v in fused.items() if v / total >= 0.02}

        # fallback: 无任何命中时默认 video_qa
        if not intent_weights:
            intent_weights = {"video_qa": 1.0}

        primary = max(intent_weights, key=intent_weights.get) if intent_weights else "video_qa"

        # 6. channel quota
        quota = self._compute_quota(intent_weights)
        return IntentRouteResult(
            intent_weights=intent_weights,
            channel_quota=quota,
            primary_intent=primary,
        )

    def _compute_quota(self, ights: dict[str, float]) -> ChannelQuota:
        """加权计算多通道检索配额.

        按意图权重加权求和，归一化后映射到 60 为基数的配额空间。
        """
        v = sum(
            ights.get(intent, 0)
            * self._cw.get(intent.split(".")[0], {}).get("vector", 0)
            for intent in ights
        )
        b = sum(
            ights.get(intent, 0)
            * self._cw.get(intent.split(".")[0], {}).get("bm25", 0)
            for intent in ights
        )
        s = sum(
            ights.get(intent, 0)
            * self._cw.get(intent.split(".")[0], {}).get("sql", 0)
            for intent in ights
        )
        t = v + b + s or 1.0
        return ChannelQuota(
            vector=int(v / t * 60),
            bm25=int(b / t * 60),
            sql=int(s / t * 60),
        )