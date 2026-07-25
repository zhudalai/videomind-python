"""Embedding 向量化 —— 本地 sentence-transformers / Ollama / API 双路径。

对应 docs/ARCHITECTURE.md 双路径推理 + docs/RAG-RETRIEVAL.md BGE-M3 选型。
设计要点：
1. **双路径**：`EMBEDDING_PROVIDER=local|api` 环境变量切换后端。
   - local: sentence-transformers 加载 BGE-M3 (1024 dim) 或轻量模型
   - api: Ollama / OpenAI 兼容 HTTP 接口（预留）
2. BGE-M3 已归一化，余弦距离 = 1 - 点积，Qdrant 用 COSINE 即可。
3. 返回向量列表 + 模型名，供 Indexer 双写 Qdrant + chunk 表。

配置锚点（config.py）:
    EMBEDDING_PROVIDER=local
    EMBEDDING_MODEL=BAAI/bge-m3
    EMBEDDING_DEVICE=auto
    EMBEDDING_DIM=1024
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

import anyio

from videomind.config import get_settings


# ──────────────────────────── 文本分块（滑动窗口） ────────────────────────────

# 简单中英兼容的句子边界正则：句号/问号/叹号（含全/半角）+ 换行
_SENT_BOUNDARY = re.compile(r"(?<=[。！？.!?\n])\s*")


def chunk_text(text: str, chunk_size: int = 500, chunk_overlap: int = 80) -> list[str]:
    """滑动窗口分块 —— 短文本友好，长文本（>chunk_size）按句子边界切分。

    算法概览：
    1. 若 ``len(text) <= chunk_size``，直接返回 ``[text]``。
    2. 否则：先按句子边界粗切 -> 把粗切片段累积成 ≤ chunk_size 的窗口
       -> 最后一个窗口若与前一个重叠，就追加 chunk_overlap 字符做 overlap。

    参数：
        text: 输入文本（中英都行）
        chunk_size: 每个窗口最大字符数（默认 500）
        chunk_overlap: 重叠字符数（默认 80）

    返回：
        切片文本列表，顺序与原文一致。

    Why 不引入 langchain：当前实现仅 ~40 行，避免新增依赖；
    将来如需 RecursiveCharacterTextSplitter 等更精细策略，可替换此函数。
    """
    if not text:
        return []
    if chunk_size <= 0:
        return [text]
    if len(text) <= chunk_size:
        return [text]

    pieces: list[str] = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        # 优先在 [end - 60, end] 区间找最近的句子边界
        if end < n:
            window = text[end - 80 : end]
            cut = None
            for m in _SENT_BOUNDARY.finditer(window):
                cut = end - 80 + m.end()
            if cut and cut > start + chunk_size // 2:
                end = cut
        pieces.append(text[start:end])
        if end >= n:
            break
        # 下一个窗口起点：保留 chunk_overlap 重叠区
        start = max(end - chunk_overlap, start + 1)
    return pieces


@dataclass
class EmbedResult:
    """Embedding 结果。"""

    vectors: list[list[float]]
    model_name: str
    dim: int


class EmbeddingBackend(Protocol):
    """Embedding 后端协议。"""

    async def embed(self, texts: list[str]) -> EmbedResult: ...


class LocalEmbedding:
    """本地 sentence-transformers Embedding。"""

    def __init__(self) -> None:
        s = get_settings()
        self._model_name = s.embedding_model
        self._device = s.embedding_device
        self._model = None  # 延迟加载

    async def embed(self, texts: list[str]) -> EmbedResult:
        from sentence_transformers import SentenceTransformer

        def _load() -> SentenceTransformer:
            return SentenceTransformer(
                self._model_name,
                device=self._device,
            )

        if self._model is None:
            self._model = await anyio.to_thread.run_sync(_load)

        def _encode() -> list[list[float]]:
            # BGE-M3 默认归一化
            return self._model.encode(
                texts,
                batch_size=32,
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=True,
            ).tolist()

        vectors = await anyio.to_thread.run_sync(_encode)
        return EmbedResult(
            vectors=vectors,
            model_name=self._model_name,
            dim=len(vectors[0]) if vectors else 0,
        )


class APIEmbedding:
    """API 路径 Embedding（Ollama / OpenAI 兼容，占位）。"""

    def __init__(self) -> None:
        s = get_settings()
        self._base_url = s.embedding_api_base_url
        self._api_key = s.embedding_api_key
        self._model = s.embedding_api_model

    async def embed(self, texts: list[str]) -> EmbedResult:
        # TODO: 实现 Ollama / OpenAI 兼容 HTTP 调用
        # 结构：
        # POST {base_url}/api/embeddings  {model, prompt}
        # 或 POST {base_url}/v1/embeddings {model, input}
        raise NotImplementedError(
            "EMBEDDING_PROVIDER=api 尚未实现，请设置 EMBEDDING_PROVIDER=local"
        )


def get_embedding_backend() -> EmbeddingBackend:
    """工厂：根据配置返回 Embedding 后端实例。"""
    s = get_settings()
    if s.embedding_provider == "local":
        return LocalEmbedding()
    else:
        return APIEmbedding()