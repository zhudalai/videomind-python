"""Reranker 后端抽象与 cross-encoder 重排器测试（TDD）。

覆盖：
  - Reranker Protocol 契约：rerank(query, hits) -> list[VectorHit]
  - DeterministicRerankerAdapter：包现有 DeterministicReranker，收 query 后忽略转调
  - OpenRouterRerankBackend：打 /api/v1/rerank，documents 用 {"text"}，按 relevance 排序+回填
  - get_reranker 工厂路由：provider=off/api/local，缺 key/缺依赖降级 Deterministic
  - 不变式（P1-2 保留）：不污染传入 raw_hits（原地改分隔离），邻居不进 rerank（由 pipeline 保证）
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from videomind.core.rag.rerank import DeterministicReranker
from videomind.core.rag.rerank_backend import (
    DeterministicRerankerAdapter,
    OpenRouterRerankBackend,
    get_reranker,
)
from videomind.core.rag.vector import VectorHit


def _hit(cid: str, score: float = 0.5, content: str = "c", source: str = "asr") -> VectorHit:
    return VectorHit(
        chunk_id=cid, score=score, content=content,
        start_ms=0, end_ms=1000, source_type=source, content_hash="h",
    )


# ───────────────────────── DeterministicRerankerAdapter ─────────────────────────


class TestDeterministicRerankerAdapter:
    """收 query 后忽略、转调底层 DeterministicReranker；保持其位次/来源/原分线性加权语义。"""

    @pytest.mark.asyncio
    async def test_adapter_ignores_query_delegates_to_underlying(self):
        underlying = MagicMock(spec=DeterministicReranker)
        underlying.rerank = MagicMock(return_value=[_hit("a", 0.9)])
        adapter = DeterministicRerankerAdapter(underlying)

        hits = [_hit("a", 0.3), _hit("b", 0.4)]
        # 协议签名 rerank(query, hits)，async 须 await
        out = await adapter.rerank("任意查询", hits)

        # underlying 收到的就是 hits（query 被丢弃），返回其输出
        underlying.rerank.assert_called_once_with(hits)
        assert out == [_hit("a", 0.9)]

    @pytest.mark.asyncio
    async def test_adapter_default_underlying_is_real_deterministic(self):
        """不传 underlying 时，内部默认 new DeterministicReranker（线上默认兜底）。"""
        adapter = DeterministicRerankerAdapter()
        hits = [_hit("low", 0.1, "asr"), _hit("high", 0.9, "asr")]
        out = await adapter.rerank("q", list(hits))
        # 真实 DeterministicReranker：原始分权重 0.5 主导 → high 在前
        assert out[0].chunk_id == "high"


# ───────────────────────── OpenRouterRerankBackend ─────────────────────────


class TestOpenRouterRerankBackend:
    """打 OpenRouter /api/v1/rerank：query + documents[{text}] -> relevance 排序+回填。"""

    def _make_client(self, response_json: dict) -> MagicMock:
        """构造一个假 httpx.AsyncClient，post() 返回固定 JSON。"""
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=response_json)
        client = MagicMock(spec=httpx.AsyncClient)
        client.aclose = AsyncMock()
        client.post = AsyncMock(return_value=resp)
        return client

    async def test_rerank_sorts_by_relevance_and_backfills_scores(self):
        """API 返回 relevance_score，后端按其降序重组 hits 并回填 score。"""
        hits = [
            _hit("d0", 0.1, content="cat photo"),
            _hit("d1", 0.9, content="berlin map"),
            _hit("d2", 0.5, content="fluffy cat sun"),
        ]
        api_resp = {
            "results": [
                {"index": 2, "relevance_score": 0.95, "document": {"text": "fluffy cat sun"}},
                {"index": 0, "relevance_score": 0.42, "document": {"text": "cat photo"}},
                {"index": 1, "relevance_score": 0.05, "document": {"text": "berlin map"}},
            ]
        }
        client = self._make_client(api_resp)
        backend = OpenRouterRerankBackend(
            base_url="https://openrouter.ai/api/v1",
            api_key="sk-x", model="nvidia/llama-nemotron-rerank-vl-1b-v2:free",
            client=client,
        )

        out = await backend.rerank("a photograph of a cat", hits)

        assert [h.chunk_id for h in out] == ["d2", "d0", "d1"]
        assert out[0].score == pytest.approx(0.95)
        assert out[1].score == pytest.approx(0.42)
        assert out[2].score == pytest.approx(0.05)

    async def test_documents_sent_as_text_only(self):
        """documents 一律 {"text": chunk.content}，不碰 image 渠道（纯文本 RAG）。"""
        hits = [_hit("d0", content="纯文本内容")]
        client = self._make_client({"results": [{"index": 0, "relevance_score": 0.7, "document": {"text": "纯文本内容"}}]})
        backend = OpenRouterRerankBackend(
            base_url="https://openrouter.ai/api/v1", api_key="k", model="m", client=client,
        )
        await backend.rerank("查询", hits)

        sent_body = client.post.call_args[1]["json"]
        assert sent_body["query"] == "查询"
        assert sent_body["documents"] == [{"text": "纯文本内容"}]
        assert sent_body["model"] == "m"
        assert "image" not in sent_body["documents"][0]

    async def test_post_targets_rerank_endpoint(self):
        """URL 路径为 {base}/rerank，不是 /chat/completions。"""
        hits = [_hit("d0")]
        client = self._make_client({"results": [{"index": 0, "relevance_score": 0.5, "document": {"text": "x"}}]})
        backend = OpenRouterRerankBackend(
            base_url="https://openrouter.ai/api/v1/", api_key="k", model="m", client=client,
        )
        await backend.rerank("q", hits)

        url = client.post.call_args[0][0]
        assert url == "https://openrouter.ai/api/v1/rerank"

    async def test_rerank_does_not_mutate_input_hits(self):
        """后端不应原地改传入 hits 的 score（pipeline 已传拷贝，仍需保证后端干净）。"""
        hits = [_hit("d0", score=0.3), _hit("d1", score=0.4)]
        client = self._make_client({
            "results": [
                {"index": 1, "relevance_score": 0.9, "document": {"text": ""}},
                {"index": 0, "relevance_score": 0.2, "document": {"text": ""}},
            ]
        })
        backend = OpenRouterRerankBackend(
            base_url="https://openrouter.ai/api/v1", api_key="k", model="m", client=client,
        )
        await backend.rerank("q", hits)
        # 输入 scores 保持不变
        assert hits[0].score == 0.3
        assert hits[1].score == 0.4

    async def test_empty_hits_returns_empty_no_call(self):
        """空输入不应打 API。"""
        client = self._make_client({"results": []})
        backend = OpenRouterRerankBackend(
            base_url="https://openrouter.ai/api/v1", api_key="k", model="m", client=client,
        )
        out = await backend.rerank("q", [])
        assert out == []
        client.post.assert_not_called()

    async def test_api_failure_raises(self):
        """HTTP/JSON 异常应向上抛，由 pipeline 层捕获降级 Deterministic。"""
        client = MagicMock(spec=httpx.AsyncClient)
        client.aclose = AsyncMock()
        client.post = AsyncMock(side_effect=httpx.HTTPStatusError(
            "rate limited", request=MagicMock(), response=MagicMock(),
        ))
        backend = OpenRouterRerankBackend(
            base_url="https://openrouter.ai/api/v1", api_key="k", model="m", client=client,
        )
        with pytest.raises(httpx.HTTPStatusError):
            await backend.rerank("q", [_hit("d0")])

    async def test_top_n_clamps_candidates(self):
        """top_n 限制传给 API，避免大候选集挤爆请求。"""
        hits = [_hit(f"d{i}") for i in range(5)]
        client = self._make_client({"results": [{"index": i, "relevance_score": 1.0 - i * 0.1, "document": {"text": ""}} for i in range(5)]})
        backend = OpenRouterRerankBackend(
            base_url="https://openrouter.ai/api/v1", api_key="k", model="m", client=client, top_n=3,
        )
        await backend.rerank("q", hits)
        assert client.post.call_args[1]["json"]["top_n"] == 3


# ───────────────────────── get_reranker 工厂 ─────────────────────────


class TestGetRerankerFactory:
    """按 rerank_provider 路由；缺 key/未装依赖时降级 DeterministicRerankerAdapter。"""

    def test_off_provider_returns_deterministic_adapter(self):
        get_reranker.cache_clear()
        with patch("videomind.core.rag.rerank_backend.get_settings") as gs:
            gs.return_value = MagicMock(
                rerank_provider="off", rerank_api_base_url="", rerank_api_key="",
                rerank_api_model="", rerank_local_model="",
            )
            r = get_reranker()
        assert isinstance(r, DeterministicRerankerAdapter)

    def test_api_provider_with_key_returns_openrouter(self):
        get_reranker.cache_clear()
        with patch("videomind.core.rag.rerank_backend.get_settings") as gs:
            gs.return_value = MagicMock(
                rerank_provider="api",
                rerank_api_base_url="https://openrouter.ai/api/v1",
                rerank_api_key="sk-x",
                rerank_api_model="nvidia/llama-nemotron-rerank-vl-1b-v2:free",
                rerank_local_model="",
            )
            r = get_reranker()
        assert isinstance(r, OpenRouterRerankBackend)

    def test_api_provider_missing_key_falls_back_to_deterministic(self):
        """provider=api 但未配 api_key → 不应崩，降级 Deterministic（线上默认兜底）。"""
        get_reranker.cache_clear()
        with patch("videomind.core.rag.rerank_backend.get_settings") as gs:
            gs.return_value = MagicMock(
                rerank_provider="api",
                rerank_api_base_url="https://openrouter.ai/api/v1",
                rerank_api_key="",  # 缺 key
                rerank_api_model="m",
                rerank_local_model="",
            )
            r = get_reranker()
        assert isinstance(r, DeterministicRerankerAdapter)

    def test_local_provider_without_dependency_falls_back_to_deterministic(self):
        """provider=local 但 sentence-transformers 未安装 → 降级 Deterministic。"""
        get_reranker.cache_clear()
        with patch("videomind.core.rag.rerank_backend.get_settings") as gs, \
             patch("videomind.core.rag.rerank_backend._try_local_backend", return_value=None):
            gs.return_value = MagicMock(
                rerank_provider="local", rerank_api_base_url="", rerank_api_key="",
                rerank_api_model="", rerank_local_model="BAAI/bge-reranker-v2-m3",
            )
            r = get_reranker()
        assert isinstance(r, DeterministicRerankerAdapter)


# ───────────────────────── lru_cache 单例 ─────────────────────────


class TestGetRerankerCache:
    """get_reranker lru_cache 单例：同进程只装配一次，避免重建 httpx client。"""

    def test_get_reranker_cached_singleton(self):
        from videomind.core.rag import rerank_backend

        rerank_backend.get_reranker.cache_clear()
        with patch("videomind.core.rag.rerank_backend.get_settings") as gs:
            gs.return_value = MagicMock(
                rerank_provider="api", rerank_api_base_url="https://openrouter.ai/api/v1",
                rerank_api_key="sk-x", rerank_api_model="m", rerank_local_model="",
            )
            r1 = get_reranker()
            r2 = get_reranker()
        assert r1 is r2

    def test_cache_clear_rebuilds(self):
        from videomind.core.rag import rerank_backend

        rerank_backend.get_reranker.cache_clear()
        with patch("videomind.core.rag.rerank_backend.get_settings") as gs:
            gs.return_value = MagicMock(
                rerank_provider="api", rerank_api_base_url="https://openrouter.ai/api/v1",
                rerank_api_key="sk-x", rerank_api_model="m", rerank_local_model="",
            )
            r1 = get_reranker()
            rerank_backend.get_reranker.cache_clear()
            r2 = get_reranker()
        assert r1 is not r2
