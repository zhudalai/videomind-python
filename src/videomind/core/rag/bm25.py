"""InMemoryBM25 关键词检索模块 —— 基于 rank-bm25 的纯 Python 内存 BM25 索引。

用法:
    bm25 = InMemoryBM25()
    bm25.build(chunks)          # chunks 是 Chunk-like 对象列表 (attr: id, content)
    results = bm25.search(query, top_k=20)  # → [(uuid.UUID, float), ...]
"""

import uuid

from rank_bm25 import BM25Okapi


class InMemoryBM25:
    """内存 BM25 关键词检索索引。

    将 Chunk 对象列表填进索引，按 query 打分排序返回
    [(chunk_id, score), ...]。
    """

    def __init__(self) -> None:
        """初始化空索引。"""
        self._bm25: BM25Okapi | None = None
        self._chunk_ids: list[uuid.UUID] = []   # 与 corpus 中每条一一对应
        self._corpus: list[list[str]] = []       # 已分词后的语料

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def build(self, chunks: list) -> None:
        """从 Chunk ORM 列表构建 BM25 索引。

        每条 chunk 的 .content 被切为小写空格分词的 token 序列。

        Args:
            chunks: Chunk-like 对象列表，每个必须有 .id (UUID) 和 .content (str)。
        """
        if not chunks:
            self._bm25 = None
            self._chunk_ids = []
            self._corpus = []
            return

        tokenized = [c.content.lower().split() for c in chunks]
        self._corpus = tokenized
        self._chunk_ids = [c.id for c in chunks]
        self._bm25 = BM25Okapi(tokenized)

    def search(self, query: str, *, top_k: int = 20) -> list[tuple[uuid.UUID, float]]:
        """检索与 query 最相关的 chunk。

        Args:
            query: 查询文本。
            top_k: 最大返回条数，默认 20。

        Returns:
            [(chunk_id, score), ...] 按 score 降序排列。空索引时返回 []。
        """
        if self._bm25 is None or not self._chunk_ids:
            return []

        tokens = query.lower().split()
        scores = self._bm25.get_scores(tokens)

        # 将 (idx, score) 配对并排序
        scored = [(self._chunk_ids[i], float(score)) for i, score in enumerate(scores)]
        scored.sort(key=lambda x: x[1], reverse=True)

        return scored[:top_k]


__all__ = ["InMemoryBM25"]