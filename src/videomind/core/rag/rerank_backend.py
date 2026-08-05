"""Cross-encoder 重排后端抽象 —— 取代固定权重 DeterministicReranker。

对应 docs/RAG-RETRIEVAL.md 重排序升级（P2-1）。设计要点：
1. 新抽象 Reranker Protocol：rerank(query, hits) -> list[VectorHit]。cross-encoder 必须
   query 参与（与 query 无关的旧 DeterministicReranker.rerank(hits) 不同），故重设协议签名。
2. DeterministicRerankerAdapter：包装现有 DeterministicReranker，收 query 后忽略、转调，
   保证 test_rerank.py 不动 + 作为 API/local 失败降级兜底。
3. OpenRouterRerankBackend：打 POST {base_url}/rerank（OpenRouter 专用 rerank 端点，
   非聊天 /chat/completions），body {model, query, documents:[{"text":...}], top_n}，
   response {results:[{index, relevance_score, document}, ...]}。纯文本 RAG：documents 一律
   {"text": chunk.content}，不碰 image 渠道（Nemotron-rerank-vl 是图文多模，文本渠道照常）。
4. LocalBGERerankBackend：本地 sentence-transformers BAAI/bge-reranker-v2-m3，与现有
   BGE-M3 embedder 同源、CJK 强、CPU 可跑、零 API 成本。import/初始化失败由工厂降级。
5. get_reranker()：按 config rerank_provider(off/api/local) 路由；缺 key/缺依赖降 Deterministic。

不变式（承接 [[rag-pipeline-rerank-expand-order]]）：(1) 邻居不进 rerank（pipeline 保证
   expand 在 rerank 之后）；(2) raw_hits 不被污染 —— 各后端不应原地改传入 hits 的 score，
   返回新列表或对拷贝操作；pipeline 侧仍额外传 dataclasses.replace 拷贝做双保险。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Protocol

import httpx

from videomind.config import get_settings
from videomind.core.rag.rerank import DeterministicReranker
from videomind.core.rag.vector import VectorHit

log = logging.getLogger(__name__)


# ───────────────────────── Reranker 协议 ─────────────────────────


class Reranker(Protocol):
    """重排器协议：用 query 对候选命中做相关性重排序。

    Methods:
        rerank: 按 query-文档相关性对 hits 重排，返回新列表（按 relevance 降序），
            每个 hit 的 score 已更新为重排分。不应原地修改传入 hits 的 score。
            统一 async：远程 API 后端天然异步，本地/固定权重后端虽同步亦声明 async
            以适配统一 await 调用点。
    """

    async def rerank(self, query: str, hits: list[VectorHit]) -> list[VectorHit]: ...


# ───────────────────────── DeterministicRerankerAdapter ─────────────────────────


class DeterministicRerankerAdapter:
    """包现有 DeterministicReranker 成 Reranker 协议：收 query 后忽略、转调。

    用途：(1) rerank_provider=off 时的默认重排器（保持原固定权重行为，opt-in 升级前不破坏
    现状）；(2) API/local 主后端失败时的降级兜底。underlying 可注入便于测试。
    """

    def __init__(self, underlying: DeterministicReranker | None = None) -> None:
        self._underlying = underlying or DeterministicReranker()

    async def rerank(self, query: str, hits: list[VectorHit]) -> list[VectorHit]:
        """忽略 query（固定权重重排与 query 无关），转调底层。

        注意：DeterministicReranker.rerank 会原地改 hits 的 score，故传调用方给的 hits
        即可——pipeline 已先传 replace 拷贝，raw_hits 不会被污染（不变式 2）。声明 async
        以适配统一 Reranker 协议（远程后端天然异步）。
        """
        # query 当前对固定权重重排无意义，显式丢弃避免误用
        del query
        return self._underlying.rerank(hits)


# ───────────────────────── OpenRouterRerankBackend ─────────────────────────


class OpenRouterRerankBackend:
    """OpenRouter /rerank cross-encoder API 后端。

    打 POST {base_url}/rerank（OpenRouter 专用 rerank 端点）。纯文本渠道：documents
    一律 {"text": chunk.content}。按 API 返回 relevance_score 降序重组 hits 并回填 score。
    API 失败 raise，由 pipeline 层捕获降级（不在此吞，保持单一职责）。

    client 可注入便于测试；None 时内部 new httpx.AsyncClient（带 Bearer + 超时）。
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        top_n: int = 20,
        timeout_s: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._url = base_url.rstrip("/") + "/rerank"
        self._model = model
        self._top_n = top_n
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                timeout=timeout_s,
            )
        self._client = client
        self._owns_client = owns_client

    async def rerank(self, query: str, hits: list[VectorHit]) -> list[VectorHit]:
        """对 hits 按 query-文档相关性重排序，返回新列表（score 已回填 relevance 分）。

        Args:
            query: 用户查询（与检索阶段同一 query）。
            hits: 候选命中（真实检索命中，邻居不在此列——由 pipeline 保证）。

        Returns:
            按 relevance_score 降序的新 VectorHit 列表；空输入直接返回 []，不打 API。

        Raises:
            httpx.HTTPStatusError / 业务异常：API 失败向上抛，pipeline 层捕获降级。
        """
        if not hits:
            return []

        body = {
            "model": self._model,
            "query": query,
            "documents": [{"text": h.content} for h in hits],
            "top_n": min(self._top_n, len(hits)),
        }
        r = await self._client.post(self._url, json=body)
        r.raise_for_status()
        data = r.json()

        # API 返回 results 已按 relevance_score 降序：[{index, relevance_score, document}, ...]
        results = data.get("results", [])
        # 用 index 映射回原 hits；新对象避免改输入
        return [
            VectorHit(
                chunk_id=hits[ri["index"]].chunk_id,
                score=float(ri["relevance_score"]),
                content=hits[ri["index"]].content,
                start_ms=hits[ri["index"]].start_ms,
                end_ms=hits[ri["index"]].end_ms,
                source_type=hits[ri["index"]].source_type,
                content_hash=hits[ri["index"]].content_hash,
            )
            for ri in results
            if 0 <= ri["index"] < len(hits)
        ]

    async def aclose(self) -> None:
        """关闭自有 httpx 客户端；注入的客户端由调用方管理。"""
        if self._owns_client:
            await self._client.aclose()


# ───────────────────────── LocalBGE 后端（懒加载，import 容错） ─────────────────────────


def _try_local_backend(model_name: str, *, device: str = "cpu") -> "Reranker | None":
    """尝试构造本地 BGE reranker；sentence-transformers 未装或模型加载失败返回 None。

    懒加载 + 容错：把重依赖隔离在此，工厂调用方只在 rerank_provider=local 时触发；
    失败时工厂降级 DeterministicRerankerAdapter，不阻断应用启动。
    """
    try:
        from videomind.core.rag.rerank_local import LocalBGERerankBackend  # 延迟 import
    except Exception as exc:  # noqa: BLE001 —— 依赖/导入 failures 一律降级，不在启动期崩
        log.warning("本地 BGE rerank 后端不可用，降级 DeterministicReranker：%s", exc)
        return None
    try:
        return LocalBGERerankBackend(model_name=model_name, device=device)
    except Exception as exc:  # noqa: BLE001 —— 模型下载/加载失败也降级
        log.warning("加载本地 BGE rerank 模型 %s 失败，降级 DeterministicReranker：%s", model_name, exc)
        return None


# ───────────────────────── 工厂 ─────────────────────────


@lru_cache
def get_reranker() -> Reranker:
    """按 config.rerank_provider 装配重排器单例（进程期一次性，对齐 get_llm_service 范式）。

    路由：
        off  → DeterministicRerankerAdapter（默认，opt-in 前保持行为）
        api  → OpenRouterRerankBackend（缺 api_key 降 Deterministic）
        local → LocalBGERerankBackend（缺依赖/加载失败降 Deterministic）

    降级一律走 DeterministicRerankerAdapter，保证重排链路总有可用后端。lru_cache 单例
    避免每次检索重建（provider=api 时复用同一 httpx.AsyncClient，不泄漏连接）；测试可用
    get_reranker.cache_clear() 重置。rerank 仅依赖静态 config，不缓存媒体数据故无需 invalidation。
    """
    s = get_settings()
    provider = (s.rerank_provider or "off").lower()

    if provider == "api":
        if s.rerank_api_key and s.rerank_api_model:
            return OpenRouterRerankBackend(
                base_url=s.rerank_api_base_url,
                api_key=s.rerank_api_key,
                model=s.rerank_api_model,
                top_n=getattr(s, "rerank_top_n", 20),
                timeout_s=getattr(s, "rerank_timeout_s", 30.0),
            )
        log.warning("rerank_provider=api 但缺 rerank_api_key/rerank_api_model，降级 DeterministicReranker")
        return DeterministicRerankerAdapter()

    if provider == "local":
        backend = _try_local_backend(
            s.rerank_local_model or "BAAI/bge-reranker-v2-m3",
            device=getattr(s, "rerank_local_device", "cpu"),
        )
        return backend or DeterministicRerankerAdapter()

    # off / 未知 → 默认固定权重兜底
    return DeterministicRerankerAdapter()


__all__ = [
    "Reranker",
    "DeterministicRerankerAdapter",
    "OpenRouterRerankBackend",
    "get_reranker",
]
