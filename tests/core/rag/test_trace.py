"""RagTrace 检索链路追踪 DB 写入测试。

验证 record_rag_trace 函数：
  - 最少必填字段 query，其他默认 None
  - 全字段填充
  - 返回 RagTrace 实例
"""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from videomind.infrastructure.storage.models import RagTrace


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------


class TestRecordRagTrace:
    """record_rag_trace 函数测试集合。"""

    @pytest.mark.asyncio
    async def test_record_rag_trace_basic(self):
        """最少必填字段 query，其他默认 None，验证 db.add 被调用且字段正确。"""
        from videomind.core.rag.trace import record_rag_trace

        db = MagicMock()
        db.add = MagicMock()
        db.flush = AsyncMock()

        trace = await record_rag_trace(db, query="Python 异步编程详解")

        # 验证 db.add 被调用一次
        db.add.assert_called_once()
        # 验证 flush 被调用
        db.flush.assert_awaited_once()

        # 验证传入的 RagTrace 字段
        called_trace: RagTrace = db.add.call_args[0][0]
        assert isinstance(called_trace, RagTrace)
        assert called_trace.query == "Python 异步编程详解"
        assert called_trace.rewritten_queries is None
        assert called_trace.intent_path is None
        assert called_trace.retrieval_channels is None
        assert called_trace.fused_results is None
        assert called_trace.expanded_results is None
        assert called_trace.reranked_results is None
        assert called_trace.final_context is None
        assert called_trace.latency_ms is None
        assert called_trace.token_usage is None
        assert called_trace.task_id is None
        assert called_trace.user_id is None

    @pytest.mark.asyncio
    async def test_record_rag_trace_full(self):
        """全字段填充，验证每个字段都正确传递到 RagTrace。"""
        from videomind.core.rag.trace import record_rag_trace

        db = MagicMock()
        db.add = MagicMock()
        db.flush = AsyncMock()

        tid = uuid.uuid4()
        uid = uuid.uuid4()

        trace = await record_rag_trace(
            db,
            query="视频摘要",
            rewritten_queries=["摘要生成", "关键帧提取"],
            intent_path={"intent": "summarize", "confidence": 0.92},
            retrieval_channels=[{"bm25": 3, "vector": 4}],
            fused_results=[{"id": "abc", "score": 0.5}, {"id": "def", "score": 0.3}],
            expanded_results=[{"id": "abc", "score": 0.5, "expansion": "query2doc"}],
            reranked_results=[{"id": "abc", "score": 0.78}],
            final_context={"context_text": "完整上下文...", "chunk_count": 5},
            latency_ms=142,
            token_usage={"prompt": 1200, "completion": 300, "total": 1500},
            task_id=tid,
            user_id=uid,
        )

        db.add.assert_called_once()
        db.flush.assert_awaited_once()

        called_trace: RagTrace = db.add.call_args[0][0]
        assert isinstance(called_trace, RagTrace)
        assert called_trace.query == "视频摘要"
        assert called_trace.rewritten_queries == ["摘要生成", "关键帧提取"]
        assert called_trace.intent_path == {"intent": "summarize", "confidence": 0.92}
        assert called_trace.retrieval_channels == [{"bm25": 3, "vector": 4}]
        assert called_trace.fused_results == [
            {"id": "abc", "score": 0.5},
            {"id": "def", "score": 0.3},
        ]
        assert called_trace.expanded_results == [
            {"id": "abc", "score": 0.5, "expansion": "query2doc"}
        ]
        assert called_trace.reranked_results == [{"id": "abc", "score": 0.78}]
        assert called_trace.final_context == {
            "context_text": "完整上下文...",
            "chunk_count": 5,
        }
        assert called_trace.latency_ms == 142
        assert called_trace.token_usage == {"prompt": 1200, "completion": 300, "total": 1500}
        assert called_trace.task_id == tid
        assert called_trace.user_id == uid

    @pytest.mark.asyncio
    async def test_record_rag_trace_returns_trace(self):
        """验证返回值为 RagTrace 实例，且是传入 db.add 的同一个对象。"""
        from videomind.core.rag.trace import record_rag_trace

        db = MagicMock()
        db.add = MagicMock()
        db.flush = AsyncMock()

        result = await record_rag_trace(db, query="测试查询")

        assert isinstance(result, RagTrace)
        assert result.query == "测试查询"
        # 返回的就是传给 db.add 的那个对象
        called_trace = db.add.call_args[0][0]
        assert result is called_trace