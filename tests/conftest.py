# tests/conftest.py
import asyncio
import os
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from minio import Minio
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qm
import redis.asyncio as redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from videomind.config import get_settings
from videomind.infrastructure.storage.database import AsyncSessionLocal, engine, Base
from videomind.infrastructure.vector.qdrant import make_qdrant_point_id

# --------------------------------------------------------
# 通用 infra guard：复用 rag test_e2e 的 _is_connection_error
# --------------------------------------------------------
def _is_connection_error(exc: BaseException) -> bool:
    """综合判定：网络层/HTTP/gRPC/asyncpg/Qdrant/Redis/MinIO 连接异常。"""
    msg = str(exc).lower()
    if any(k in msg for k in (
        "connection refused", "connect call failed", "timeout",
        "name or service not known", "no route to host",
        "connection reset", "broken pipe", "eof",
        "asyncpg.exceptions", "qdrant_client.http.exceptions",
        "redis.exceptions.connectionerror", "minio.error"
    )):
        return True
    # 类型兜底
    from asyncpg import PostgresConnectionError
    from qdrant_client.http.exceptions import UnexpectedResponse
    from redis.exceptions import ConnectionError as RedisConnectionError
    from minio.error import S3Error
    return isinstance(exc, (PostgresConnectionError, UnexpectedResponse, RedisConnectionError, S3Error, ConnectionError, TimeoutError, OSError))


def infra_ok(name: str):
    """装饰器/上下文守门：infra 不通则 pytest.skip，不让测试红。"""
    def decorator(func):
        import functools
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                if _is_connection_error(e):
                    pytest.skip(f"基础设施 {name} 不可用: {e}")
                raise
        return wrapper
    return decorator


# --------------------------------------------------------
# settings（session 级，读 .env）
# --------------------------------------------------------
@pytest.fixture(scope="session")
def settings():
    get_settings.cache_clear()
    return get_settings()


# --------------------------------------------------------
# PG：AsyncSession fixture + 自动 alembic upgrade head（function 级）
# --------------------------------------------------------
_alembic_done = False


@pytest_asyncio.fixture
async def pg_session(settings) -> AsyncGenerator[AsyncSession, None]:
    global _alembic_done
    if not _alembic_done:
        # 用 alembic CLI 升级（不要用 create_all）
        # 便携化（2.2）：sys.executable 替代硬编码 Anaconda 绝对路径（CI/他机可跑），
        # 项目根从 conftest 位置推导（cwd 随 pytest 启动目录漂移）
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            pytest.skip(f"alembic upgrade head 失败: {result.stderr}")
        _alembic_done = True

    async with AsyncSessionLocal() as session:
        yield session
        # 测试后回滚未提交的更改
        await session.rollback()


# --------------------------------------------------------
# Qdrant：client + 临时 collection（function 级自动清理）
# --------------------------------------------------------
@pytest_asyncio.fixture
async def qdrant_client(settings) -> AsyncGenerator[AsyncQdrantClient, None]:
    client = AsyncQdrantClient(url=settings.qdrant_url, timeout=10.0)
    try:
        yield client
    finally:
        await client.close()


@pytest_asyncio.fixture
async def temp_collection(qdrant_client: AsyncQdrantClient) -> AsyncGenerator[str, None]:
    coll = f"test_{uuid.uuid4().hex[:8]}"
    await qdrant_client.create_collection(
        collection_name=coll,
        vectors_config=qm.VectorParams(size=1024, distance=qm.Distance.COSINE),
    )
    try:
        yield coll
    finally:
        try:
            await qdrant_client.delete_collection(coll)
        except Exception:
            pass


# --------------------------------------------------------
# Redis：client（function 级）
# --------------------------------------------------------
@pytest_asyncio.fixture
async def redis_client(settings) -> AsyncGenerator[redis.Redis, None]:
    # settings.redis_url 如 redis://localhost:16379/0
    client = redis.from_url(settings.redis_url, decode_responses=True)
    try:
        yield client
    finally:
        await client.close()


# --------------------------------------------------------
# MinIO：client + 临时 bucket（function 级自动清理）
# --------------------------------------------------------
@pytest.fixture
def minio_client(settings) -> Minio:
    return Minio(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )


@pytest.fixture
def temp_bucket(minio_client: Minio) -> str:
    import uuid as _uuid
    bucket = f"test-{_uuid.uuid4().hex[:8]}"
    minio_client.make_bucket(bucket)
    try:
        yield bucket
    finally:
        # 清空再删
        for obj in minio_client.list_objects(bucket, recursive=True):
            minio_client.remove_object(bucket, obj.object_name)
        minio_client.remove_bucket(bucket)


# --------------------------------------------------------
# 通用测试标识
# --------------------------------------------------------
@pytest.fixture
def media_id() -> str:
    return uuid.uuid4().hex[:32]  # 32 hex = UUID 无连字符


# --------------------------------------------------------
# 测试视频路径：缺失则触发 _gen.py 生成（下步做）
# --------------------------------------------------------
@pytest.fixture(scope="session")
def test_video_path() -> str:
    path = r"D:\shu_e\Documents\Video MInd python\tests\fixtures\test_3s.mp4"
    if not os.path.exists(path):
        pytest.skip(f"测试视频不存在 {path}，先运行 tests/fixtures/_gen.py 生成")
    return path


# --------------------------------------------------------
# 自动应用 asyncio_mode=auto（pytest-asyncio 已在 pyproject.toml 配置）
# --------------------------------------------------------
pytest_plugins = ("pytest_asyncio",)