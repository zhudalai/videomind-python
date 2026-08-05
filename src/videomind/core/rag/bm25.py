"""InMemoryBM25 关键词检索模块 —— 基于 rank-bm25 的纯 Python 内存 BM25 索引。

用法:
    bm25 = InMemoryBM25()
    bm25.build(chunks)          # chunks 是 Chunk-like 对象列表 (attr: id, content)
    results = bm25.search(query, top_k=20)  # → [(uuid.UUID, float), ...]
"""

import re
import uuid

import jieba  # 中文分词；日文假名连续串靠字符 bigram 兜底
from rank_bm25 import BM25Okapi

# CJK 统一表意文字 + 日文假名 + 韩文字母
_CJK_RE = re.compile(r"[一-鿿぀-ヿ가-힯]")
# 连续 CJK 串（用于 bigram 兜底判断）
_CJK_RUN_RE = re.compile(r"[一-鿿぀-ヿ가-힯]+")


def _tokenize(text: str) -> list[str]:
    """混合分词：拉丁文按空白/标点切，CJK 走 jieba + 字符 bigram 兜底。

    原 ``content.lower().split()`` 对无空格的 CJK 文本几乎不分词，把整段当成
    单个超长 token，使 BM25 退化为整串精确匹配、丧失词法分辨力。本函数按语种
    分流：拉丁/数字段保持空白与标点切分（与原行为一致），CJK 段用 jieba 切词；
    对 jieba 切后仍偏长的 CJK 串（常见于日文假名连续串，jieba 不切假名）补
    字符 bigram，提供词法命中粒度。

    Args:
        text: 待分词文本（chunk content 或 query）。

    Returns:
        token 列表（已 lower、已去空）。
    """
    tokens: list[str] = []
    if not text:
        return tokens

    # 先按空白切成段，再逐段分流
    for seg in text.lower().split():
        if not seg:
            continue
        if not _CJK_RE.search(seg):
            # 纯拉丁/数字段：按非字母数字字符再细化（兼容原 split 语义并兼容标点）
            for w in re.split(r"[^\w]+", seg):
                if w:
                    tokens.append(w)
            continue
        # 含 CJK：整段交给 jieba（能处理中英混排），再对切出的 CJK token 补字符 bigram
        for w in jieba.lcut(seg):
            w = w.strip()
            if not w:
                continue
            if _CJK_RE.search(w):
                tokens.append(w)
                # 字符级 2-gram：与 jieba 切法无关，保证 query 与 chunk 在 CJK 子串上
                # 必然共享相同 2-gram（含日文假名串——jieba 不切假名，整串 vs 逐字符
                # 形态可能不一致，但 2-gram 始终稳定）。中文短词重复 2-gram 无害（TF 加权）。
                if len(w) >= 2:
                    for i in range(len(w) - 1):
                        tokens.append(w[i : i + 2])
            else:
                tokens.append(w)
    return tokens


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

        每条 chunk 的 .content 经 ``_tokenize`` 混合分词为 token 序列（中文 jieba +
        日文 bigram 兜底，拉丁文按空白/标点切分）。

        Args:
            chunks: Chunk-like 对象列表，每个必须有 .id (UUID) 和 .content (str)。
        """
        if not chunks:
            self._bm25 = None
            self._chunk_ids = []
            self._corpus = []
            return

        tokenized = [_tokenize(c.content) for c in chunks]
        self._corpus = tokenized
        self._chunk_ids = [c.id for c in chunks]
        self._bm25 = BM25Okapi(tokenized)

    def search(self, query: str, *, top_k: int = 20) -> list[tuple[uuid.UUID, float]]:
        """检索与 query 最相关的 chunk。

        Args:
            query: 查询文本（经混合分词处理）。
            top_k: 最大返回条数，默认 20。

        Returns:
            [(chunk_id, score), ...] 按 score 降序排列。空索引时返回 []。
        """
        if self._bm25 is None or not self._chunk_ids:
            return []

        tokens = _tokenize(query)
        scores = self._bm25.get_scores(tokens)

        # 将 (idx, score) 配对并排序
        scored = [(self._chunk_ids[i], float(score)) for i, score in enumerate(scores)]
        scored.sort(key=lambda x: x[1], reverse=True)

        return scored[:top_k]


__all__ = ["InMemoryBM25"]