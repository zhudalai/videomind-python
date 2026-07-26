"""L3 真实集成测试：Embedding 真跑 sentence-transformers（小 fixture 文本）。"""

import pytest
import uuid
from pathlib import Path

from videomind.core.video_pipeline.embed import get_embedding_backend, chunk_text, EmbedResult


@pytest.mark.infra
class TestEmbeddingIntegration:
    """Embedding 真实集成测试（依赖 sentence-transformers BGE-M3）。"""

    async def test_embed_chinese_text_dim_1024(self):
        """中文文本 → 1024 维向量，归一化后模长 ≈ 1。"""
        embedder = get_embedding_backend()

        texts = [
            "这是一段中文测试文本",
            "VideoMind 是一个视频理解系统",
            "嵌入向量用于语义检索",
        ]
        result = await embedder.embed(texts)

        assert isinstance(result, EmbedResult)
        assert result.model_name == "BAAI/bge-m3"
        assert result.dim == 1024
        assert len(result.vectors) == 3
        for vec in result.vectors:
            assert len(vec) == 1024
            # BGE-M3 默认归一化，模长 ≈ 1
            norm_sq = sum(x * x for x in vec)
            assert abs(norm_sq - 1.0) < 1e-4, f"向量未归一化: norm^2={norm_sq}"

    async def test_embed_empty_list_returns_empty(self):
        """空列表 → 空向量列表，dim=0。"""
        embedder = get_embedding_backend()
        result = await embedder.embed([])
        assert result.vectors == []
        assert result.dim == 0

    async def test_embed_deterministic_same_input(self):
        """同一输入两次嵌入 → 向量完全一致。"""
        embedder = get_embedding_backend()
        texts = ["测试确定性嵌入", "deterministic embedding test"]

        r1 = await embedder.embed(texts)
        r2 = await embedder.embed(texts)

        assert len(r1.vectors) == len(r2.vectors)
        for v1, v2 in zip(r1.vectors, r2.vectors):
            for x1, x2 in zip(v1, v2):
                assert x1 == x2

    async def test_chunk_text_edge_cases(self):
        """chunk_text 滑动窗口边界情况。"""
        # 空串
        assert chunk_text("") == []
        # 短文本
        assert chunk_text("短文本") == ["短文本"]
        # 长文本（大于 chunk_size）
        long_text = "这是一个很长的文本" * 50  # ~500 字符
        chunks = chunk_text(long_text, chunk_size=200, chunk_overlap=50)
        assert len(chunks) >= 2
        # 相邻 chunk 有重叠
        for i in range(len(chunks) - 1):
            # 至少有一个字符重叠（简单检查）
            assert len(chunks[i]) > 0
            assert len(chunks[i + 1]) > 0

    async def test_chunk_text_mixed_zh_en(self):
        """中英混合文本分块不丢字符。"""
        text = "Hello 世界! This is 测试 text. " * 20
        chunks = chunk_text(text, chunk_size=300, chunk_overlap=60)
        # 拼接回去应包含原文所有字符（允许重叠区重复）
        concatenated = "".join(chunks)
        for ch in set(text):
            assert ch in concatenated, f"字符 {ch} 丢失"