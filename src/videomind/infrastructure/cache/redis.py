"""Redis 异步客户端 —— 缓存 / Celery broker / GPU 锁 / SSE pub-sub。

对应 docs/ARCHITECTURE.md Redis 用途分层：
1. DB0 = 应用缓存（hot checkpoint、媒体状态等）
2. DB1 = Celery broker
3. DB2 = Celery result backend
4. 通用 = GPU 分布式锁（gpu:lock）+ SSE 进度 pub/sub

redis-py asyncio 原生支持，无需 anyio 包装。
"""

from __future__ import annotations

from functools import lru_cache

import redis.asyncio as aioredis

from videomind.config import get_settings


def get_redis() -> aioredis.Redis:
    """获取应用缓存 Redis（DB0）客户端。

    不使用 lru_cache，因为 Celery worker 使用 anyio.run() 创建临时事件循环，
    循环关闭后缓存的连接池会失效。每次调用创建新客户端，连接池由 redis-py 管理。
    """
    s = get_settings()
    return aioredis.from_url(
        s.redis_url,
        decode_responses=True,
        max_connections=20,
    )


async def close_redis() -> None:
    """应用关闭时关闭连接池（无缓存时无需操作，保留接口兼容）。"""
    pass


# ──────────────────────────── 键空间约定 ────────────────────────────


def key_media_status(media_id) -> str:
    return f"media:{media_id}:status"


def key_media_progress(media_id) -> str:
    """SSE 进度频道（pub/sub）。"""
    return f"media:{media_id}:progress"


def key_agent_checkpoint(task_id) -> str:
    """Agent 热缓存 checkpoint（PostgreSQL 真源 + Redis 热缓存）。"""
    return f"checkpoint:{task_id}"


def key_gpu_lock(stage: str) -> str:
    """GPU 分布式锁键。对应 docs/TASK-ORCHESTRATION GPUResourceManager。"""
    return f"gpu:lock:{stage}"


# ──────────────────────────── 简单缓存辅助 ────────────────────────────


async def cache_get(key: str) -> str | None:
    r = get_redis()
    return await r.get(key)


async def cache_set(key: str, value: str, *, ttl: int | None = None) -> None:
    r = get_redis()
    if ttl:
        await r.set(key, value, ex=ttl)
    else:
        await r.set(key, value)


async def cache_delete(key: str) -> None:
    r = get_redis()
    await r.delete(key)
