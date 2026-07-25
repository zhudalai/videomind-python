"""数据库引擎与会话工厂 —— SQLAlchemy 2.0 async。

对应 docs/DATA-MODEL.md §4 迁移策略 + docs/ARCHITECTURE.md 持久化层。

设计要点：
1. **async engine**（asyncpg）：所有应用层 DB IO 走 async；Alembic 迁移用 sync URL 自动转换。
2. **sessionmaker**：FastAPI 路由用 `Depends(get_db)`；后台 Celery 任务直接 yield session。
3. `Base` 是所有 ORM 模型的声明式基类（见 models.py）。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from videomind.config import get_settings

_settings = get_settings()

# —— 连接池策略 ——
# Celery 视频任务是 sync 函数，内部用 `anyio.run(_run)` 起临时 event loop 跑 async DB IO；
# 任务结束 loop 关闭，但 SQLAlchemy 默认池里复用的 asyncpg connection 绑在那个已关 loop 上。
# 下个任务在新 loop 取连接时，`pool_pre_ping` 想测活并 terminate 旧连接 → 旧连接所属 loop
# 已 closed → `RuntimeError: Event loop is closed` → 被 Celery 包成
# `AttributeError('NoneType' object has no attribute 'send')` → 一次无谓 retry。
#
# 用 NullPool（不缓存连接，每 session 新建、跑完即关）从根上消除"连接跨已关 loop 复用"窗口，
# 代价是每次 DB 操作新握手——在本项目「单机 prefork、低 QPS、视频任务以 IO/推理为主」的
# 场景下可忽略，换来的是任务级 loop 隔离的正确性。
# `pool_pre_ping` 在 NullPool 下也无需（每次都是新连接）。
engine = create_async_engine(
    _settings.database_url,
    echo=_settings.is_dev,
    poolclass=NullPool,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """所有 ORM 模型的声明式基类。模型定义见 `videomind.infrastructure.storage.models`。"""


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：每请求一个 session，结束自动关闭。异常时自动回滚。"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """非 HTTP 上下文（Celery 任务 / 后台脚本）用的 session 上下文管理器。"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """应用关闭时清理连接池（lifespan 调用）。"""
    await engine.dispose()
