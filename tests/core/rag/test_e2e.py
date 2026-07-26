"""E2E 集成测试 —— 用真实基础设施验证完整 RAG 检索管线。

策略：
    1. 尝试连接 DB 和 Qdrant → 连不上就 pytest.skip
    2. 查到任意已有 chunks 的 media_id → 执行 search → 验证返回

运行方式：
    pytest tests/core/rag/test_e2e.py -v -m e2e
"""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import select

from videomind.core.rag.pipeline import search
from videomind.infrastructure.storage.database import AsyncSessionLocal
from videomind.infrastructure.storage.models import Chunk


def _is_connection_error(exc: Exception) -> bool:
    """判断异常是否为基础设施不可达导致的连接错误。

    覆盖范围：socket/网络层、HTTP/HTTPX、Qdrant 客户端、pg 驱动。
    """
    error_str = f"{type(exc).__name__} {exc}".lower()
    # 直接连接关键词
    if any(kw in error_str for kw in (
        "connect", "refused", "unreachable", "timeout", "reset",
        "cannot assign requested address", "no route to host",
        "nodename nor servname", "getaddrinfo",
    )):
        return True
    # 按异常层级走：httpx、grpc、socket、OSError 子类
    exc_module = type(exc).__module__
    if any(p in exc_module for p in (
        "httpx", "httpcore", "grpc.aio", "grpc._channel",
    )):
        return True
    if isinstance(exc, OSError) or "ConnectionResetError" in type(exc).__name__:
        return True
    # asyncpg 连接失败
    if "cannot connect" in error_str or "could not translate host name" in error_str:
        return True

    return False


@pytest.fixture(scope="module")
def module_setup():
    """模块级 setup：验证基础设施可用性（仅 run 一次）。"""
    # 快速检查 Qdrant URL 和 DB URL 配置是否存在
    from videomind.config import get_settings
    settings = get_settings()
    db_host = settings.database_url.split("@")[-1].split("/")[0] if "@" in settings.database_url else ""
    qdrant_host = settings.qdrant_url.split("/")[2].split(":")[0] if "://" in settings.qdrant_url else ""
    return {"db_host": db_host, "qdrant_host": qdrant_host, "config": settings}


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_e2e_db_connectivity(module_setup):
    """E2E: 验证 DB 连接可用，失败时 skip 并给出原因。"""
    info = module_setup
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(1))
            assert result.scalar() == 1
    except Exception as e:
        if _is_connection_error(e):
            pytest.skip(f"DB 不可达 ({info['db_host']}): {e}")
        raise


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_e2e_find_media_with_chunks(module_setup):
    """E2E: 查到一个已有 chunks 的 media_id，验证基础数据存在。"""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Chunk.media_id).distinct().limit(1)
            )
            row = result.first()
            if row is None:
                pytest.skip("数据库中没有已索引的 chunk 数据")
            media_id = row[0]
            assert isinstance(media_id, uuid.UUID)
    except Exception as e:
        if _is_connection_error(e):
            pytest.skip(f"数据库不可达: {e}")
        raise


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_e2e_pipeline_executes(module_setup):
    """E2E: 完整 RAG 检索管线在真实基础设施上执行不抛异常。

    步骤：
        1. 连接数据库查找任意 media_id（含 chunks）
        2. 调用 search() 完整走一遍 pipeline
        3. 验证返回结构包含 context / evidence / raw_hits
    """
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Chunk.media_id).distinct().limit(1)
            )
            row = result.first()
            if row is None:
                pytest.skip("没有已索引的 chunk 数据")

            media_id = row[0]

            # 完整管线执行
            results = await search(db, "测试查询", media_id, top_k=5)

            assert "context" in results
            assert "evidence" in results
            assert "raw_hits" in results
            # 三个字段类型校验
            assert isinstance(results["context"], list)
            assert isinstance(results["evidence"], list)
            assert isinstance(results["raw_hits"], list)
            # evidence 数量与 context 一致（一一对应）
            assert len(results["evidence"]) == len(results["context"])

    except Exception as e:
        if _is_connection_error(e):
            pytest.skip(f"基础设施不可用（Qdrant/DB 断开）: {e}")
        raise


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_e2e_evidence_format():
    """E2E: 验证返回的 evidence_id 以 EID_ 为前缀，格式正确。"""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Chunk.media_id).distinct().limit(1)
            )
            row = result.first()
            if row is None:
                pytest.skip("没有已索引的 chunk 数据")

            media_id = row[0]

            results = await search(db, "测试查询", media_id, top_k=5)

            evidence_list = results["evidence"]
            for ev in evidence_list:
                # 格式：EID_xxxxxxxx_NN
                assert ev.id.startswith("EID_"), f"evidence_id 缺少 EID_ 前缀: {ev.id}"
                # 去掉前缀后，格式应为 hex8_seq
                suffix = ev.id[4:]  # 去掉 "EID_"
                parts = suffix.rsplit("_", 1)
                assert len(parts) == 2, f"evidence_id 格式错误: {ev.id}"
                hex_part, seq_part = parts
                assert len(hex_part) == 8, f"hex part 长度不等于 8: {ev.id}"
                # hex part 应该是 8 位十六进制
                assert all(c in "0123456789abcdef" for c in hex_part), \
                    f"hex part 含非法字符: {hex_part}"
                # seq part 应该是纯数字
                assert seq_part.isdigit(), f"seq part 非数字: {seq_part}"
                # 验证 chunk_id 是合法 UUID 字符串
                uuid.UUID(ev.chunk_id)
                # 验证 score 范围
                assert 0.0 <= ev.score <= 1.0, f"score 不在 [0,1] 范围: {ev.score}"

    except ValueError:
        # UUID 解析失败 → 数据问题不是基础设施问题
        raise
    except Exception as e:
        if _is_connection_error(e):
            pytest.skip(f"基础设施不可用（Qdrant/DB 断开）: {e}")
        raise


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_e2e_top_k_respect():
    """E2E: 验证 top_k 参数生效 —— 返回结果不超过请求的数量。"""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Chunk.media_id).distinct().limit(1)
            )
            row = result.first()
            if row is None:
                pytest.skip("没有已索引的 chunk 数据")

            media_id = row[0]

            results = await search(db, "测试查询", media_id, top_k=3)

            assert len(results["evidence"]) <= 3
            assert len(results["context"]) <= 3
            assert len(results["raw_hits"]) <= 3

    except Exception as e:
        if _is_connection_error(e):
            pytest.skip(f"基础设施不可用（Qdrant/DB 断开）: {e}")
        raise


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_e2e_empty_result_structure():
    """E2E: 验证无意义查询时的返回结构完整性。"""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Chunk.media_id).distinct().limit(1)
            )
            row = result.first()
            if row is None:
                pytest.skip("没有已索引的 chunk 数据")

            media_id = row[0]

            # 用不太可能匹配的查询验证结构
            results = await search(db, "xyzzy_nonexistent_query_test_12345", media_id, top_k=3)

            # 即使没有结果，返回结构必须完整
            assert "context" in results
            assert "evidence" in results
            assert "raw_hits" in results
            assert isinstance(results["context"], list)
            assert isinstance(results["evidence"], list)
            assert isinstance(results["raw_hits"], list)

    except Exception as e:
        if _is_connection_error(e):
            pytest.skip(f"基础设施不可用（Qdrant/DB 断开）: {e}")
        raise