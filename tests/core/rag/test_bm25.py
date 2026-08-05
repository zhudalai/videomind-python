"""InMemoryBM25 关键词检索单元的测试

验证 BM25 索引构建、检索、空索引和 rebuild 行为。
"""

import uuid

import pytest

from videomind.core.rag.bm25 import InMemoryBM25


# ---------------------------------------------------------------------------
# Mock 辅助：模拟 Chunk-like objects（具有 .id 和 .content 属性）
# ---------------------------------------------------------------------------

class _FakeChunk:
    """模拟 Chunk ORM 对象，只提供检索所需的最小接口。"""

    def __init__(self, chunk_id: uuid.UUID, content: str) -> None:
        self.id = chunk_id
        self.content = content


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------

class TestInMemoryBM25Basic:
    """基本 build + search 行为。"""

    def test_build_and_search(self):
        """3 个 chunk，检索 "vector database" 应返回包含该词条的 chunk 排前。"""
        c1 = _FakeChunk(uuid.uuid4(), "this is about vector databases and embedding search")
        c2 = _FakeChunk(uuid.uuid4(), "vector database systems are powerful")
        c3 = _FakeChunk(uuid.uuid4(), "deep learning with neural networks")

        bm25 = InMemoryBM25()
        bm25.build([c1, c2, c3])
        results = bm25.search("vector database", top_k=3)

        # 返回 ≤ 3 条
        assert len(results) <= 3
        # 至少返回 2 条（c1, c2 包含关键词）
        assert len(results) >= 2

        # 每个结果是 (uuid.UUID, float)
        for r in results:
            assert isinstance(r, tuple), f"expected tuple, got {type(r)}"
            assert len(r) == 2
            assert isinstance(r[0], uuid.UUID)
            assert isinstance(r[1], float)

        # scores 降序排列
        scores = [r[1] for r in results]
        for i in range(len(scores) - 1):
            assert scores[i] >= scores[i + 1], (
                f"scores not descending: {scores}"
            )

        # c1 和 c2 包含关键词，应排在前两位
        top_ids = {r[0] for r in results[:2]}
        assert c1.id in top_ids or c2.id in top_ids

    def test_empty_index_search_returns_empty(self):
        """未 build 时 search 应返回空列表（不抛异常）。"""
        bm25 = InMemoryBM25()
        results = bm25.search("anything")
        assert results == []

    def test_build_empty_list(self):
        """build([]) 后 search 应返回空列表。"""
        bm25 = InMemoryBM25()
        bm25.build([])
        results = bm25.search("anything")
        assert results == []

    def test_rebuild_replaces_old_corpus(self):
        """build([C1]) 之后 build([C2, C3]) 应只能搜到 C2、C3。"""
        c1 = _FakeChunk(uuid.UUID(hex="0000000000014000a000000000000000"), "apple banana")
        c2 = _FakeChunk(uuid.UUID(hex="0000000000024000a000000000000000"), "vector database")
        c3 = _FakeChunk(uuid.UUID(hex="0000000000034000a000000000000000"), "keyword search")

        bm25 = InMemoryBM25()
        bm25.build([c1])
        results_first = bm25.search("apple", top_k=5)
        assert len(results_first) == 1
        assert results_first[0][0] == c1.id

        # 用新 corpus 重新 build
        bm25.build([c2, c3])
        results_second = bm25.search("apple", top_k=5)
        # C1 不在新 corpus，不应返回
        c1_ids = [r[0] for r in results_second]
        assert c1.id not in c1_ids

        # 能搜到 c2
        results_db = bm25.search("vector database", top_k=5)
        assert len(results_db) == 2  # 两条都有 keyword 命中，但都返回
        db_ids = {r[0] for r in results_db}
        assert c2.id in db_ids
        assert c3.id in db_ids

    def test_top_k_limits_results(self):
        """top_k 应正确截断返回数量。"""
        chunks = [
            _FakeChunk(uuid.uuid4(), f"document number {i} with unique words")
            for i in range(10)
        ]
        bm25 = InMemoryBM25()
        bm25.build(chunks)
        results = bm25.search("document", top_k=3)
        assert len(results) == 3

    def test_no_matching_terms(self):
        """查询词不在语料中时所有 score 应为 0。"""
        c1 = _FakeChunk(uuid.uuid4(), "apple banana cherry")
        c2 = _FakeChunk(uuid.uuid4(), "dog cat mouse")
        bm25 = InMemoryBM25()
        bm25.build([c1, c2])
        results = bm25.search("zebra phantom", top_k=5)
        # 全部返回，score 均为 0
        assert len(results) == 2
        for _, score in results:
            assert score == 0.0

    def test_single_chunk(self):
        """单条语料人群确认基础流程正常。"""
        c = _FakeChunk(uuid.uuid4(), "the quick brown fox")
        bm25 = InMemoryBM25()
        bm25.build([c])
        results = bm25.search("quick fox", top_k=5)
        assert len(results) == 1
        assert results[0][0] == c.id
        # 单文档语料场景下 BM25 score 可能为负（IDF 负值），只验证是 float 即可
        assert isinstance(results[0][1], float)


class TestRegistry:
    """确保 InMemoryBM25 独立，无外部状态污染。"""

    def test_isolated_instances(self):
        """两个独立实例不应共享索引。"""
        ca = _FakeChunk(uuid.uuid4(), "apple")
        cb = _FakeChunk(uuid.uuid4(), "banana")

        a = InMemoryBM25()
        a.build([ca])
        b = InMemoryBM25()
        b.build([cb])

        # a 只能搜到 apple
        assert a.search("apple")[0][0] == ca.id
        # b 只能搜到 banana
        assert b.search("banana")[0][0] == cb.id
        # b 搜不到 apple
        assert all(r[0] != ca.id for r in b.search("apple"))


class TestCJKSegmentation:
    """CJK（中日文）分词——验证不再把整段无空格文本当成单个 token。

    背景：原实现 ``content.lower().split()`` 对无空格的 CJK 文本几乎不分词，
    导致 BM25 退化为整段字符串精确匹配，丧失词法分辨力。
    改进后对 CJK 文本做分词（jieba 中文 + 字符 bigram 兜底日文/假名），
    使含共享词的查询能命中对应 chunk。

    注：BM25Okapi 的 IDF 在小语料上可能为 0 或负（N=2,n=1 时 IDF=0；单文档 IDF<0，
    见 test_single_chunk 注释）。为保证断言 ``score > 0`` 成立，本组用 ≥3 文档、
    且相关词只在 1 个 chunk 出现，使 IDF>0。
    """

    def test_chinese_shared_word_ranks_relevant_chunk_first(self):
        """中文：3 个 chunk，query 只与其中 1 个共享 jieba 词，应排第一且分数严格更高。"""
        c_rel = _FakeChunk(uuid.uuid4(), "深度学习在图像识别与自然语言处理中的应用")
        c_irr1 = _FakeChunk(uuid.uuid4(), "心理学基础知识与情绪管理方法")
        c_irr2 = _FakeChunk(uuid.uuid4(), "育儿知识与儿童早期教育指南")
        bm25 = InMemoryBM25()
        bm25.build([c_rel, c_irr1, c_irr2])

        results = bm25.search("深度学习", top_k=3)
        assert len(results) == 3
        # 相关 chunk 必须排第一，且分数严格高于两个无关 chunk（排除全 0 误判）
        assert results[0][0] == c_rel.id
        assert results[0][1] > results[1][1]
        assert results[0][1] > results[2][1]

    def test_chinese_query_matches_chunk_via_shared_tokens(self):
        """中文：改之前整段为一个 token、BM25 score 全 0；改后借分词命中，score>0。"""
        c_rel = _FakeChunk(uuid.uuid4(), "这个视频介绍了商业模式与盈利计划")
        c_irr1 = _FakeChunk(uuid.uuid4(), "天气预报说今天会下雨")
        c_irr2 = _FakeChunk(uuid.uuid4(), "我对烹饪技巧很感兴趣")
        bm25 = InMemoryBM25()
        bm25.build([c_rel, c_irr1, c_irr2])

        results = bm25.search("商业模式", top_k=3)
        assert results[0][0] == c_rel.id
        # 命中分数应 > 0（'商业'/'业模'/'模式' 2-gram 只在 c_rel 出现 → IDF>0）
        assert results[0][1] > 0.0

    def test_japanese_bigram_recall(self):
        """日文假名：字符 bigram 让 query 召回含相同假名串的 chunk。

        jieba 不切日文假名连续串；2-gram 在字符级稳定，使 query 与 chunk
        的假名子串必然共享相同 2-gram。
        """
        c_rel = _FakeChunk(uuid.uuid4(), "はいどうもみなさんこんにちは今回の動画")
        c_irr1 = _FakeChunk(uuid.uuid4(), "天気予報によると明日は雨になる")
        c_irr2 = _FakeChunk(uuid.uuid4(), "料理番組でパスタを作ってみる")
        bm25 = InMemoryBM25()
        bm25.build([c_rel, c_irr1, c_irr2])

        results = bm25.search("みなさん", top_k=3)
        assert results[0][0] == c_rel.id
        # 假名 2-gram 'みな'/'なさ'/'さん' 只在 c_rel 出现 → IDF>0 → score>0
        assert results[0][1] > 0.0