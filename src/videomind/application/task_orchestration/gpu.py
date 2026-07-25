"""GPU 资源管理器 —— Redis 分布式锁 + 心跳续租。

对应 docs/TASK-ORCHESTRATION.md GPUResourceManager + docs/VIDEO-PIPELINE.md 错峰策略。
设计要点：
1. **锁键**：`gpu:lock:{stage}`（stage=asr/ocr/embedding/llm），同一阶段同一时刻只能 1 个任务。
2. **锁值**：`{task_id}:{heartbeat_ts}`，TTL = `GPU_LOCK_TTL`（默认 30s）。
3. **心跳**：持有锁期间，后台任务每 10s 刷新 TTL（续租），防止 worker 崩溃导致锁永不释放。
4. **获取**：`acquire(task_id, stage, timeout=300)` 阻塞等待，最多 timeout 秒；成功返回 `GPUHandle`（含释放方法）。
5. **释放**：显式释放或上下文退出时删除锁（Lua 脚本保证原子性：只删自己的锁）。

使用示例：
    gpu = get_gpu_manager()
    async with await gpu.acquire(task_id, "asr") as handle:
        # 独占 GPU 做 ASR
        ...
    # 自动释放
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import redis.asyncio as aioredis

from videomind.config import get_settings
from videomind.infrastructure.cache.redis import get_redis
from videomind.observability.metrics import GPU_UTILIZATION


@dataclass
class GPUHandle:
    """GPU 锁持有句柄。退出上下文时自动释放。"""

    task_id: str
    stage: str
    _manager: "GPUResourceManager"
    _acquired: bool = True

    async def release(self) -> None:
        if self._acquired:
            await self._manager.release(self.task_id, self.stage)
            self._acquired = False

    async def __aenter__(self) -> "GPUHandle":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.release()


class GPUResourceManager:
    """GPU 分布式锁管理器（Redis 实现）。"""

    # Lua 脚本：原子获取锁（SET NX EX）
    _ACQUIRE_SCRIPT = """
    if redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', ARGV[2]) then
        return 1
    else
        return 0
    end
    """

    # Lua 脚本：原子释放锁（只有值匹配才删）
    _RELEASE_SCRIPT = """
    if redis.call('GET', KEYS[1]) == ARGV[1] then
        return redis.call('DEL', KEYS[1])
    else
        return 0
    end
    """

    # Lua 脚本：原子续租（只有值匹配才刷新 TTL）
    _RENEW_SCRIPT = """
    if redis.call('GET', KEYS[1]) == ARGV[1] then
        return redis.call('EXPIRE', KEYS[1], ARGV[2])
    else
        return 0
    end
    """

    def __init__(self) -> None:
        s = get_settings()
        self._redis = get_redis()
        self._ttl = s.gpu_lock_ttl  # 秒
        self._heartbeat_interval = max(5, self._ttl // 3)  # 约 1/3 TTL 刷新一次

        # 注册 Lua 脚本
        self._sha_acquire = None
        self._sha_release = None
        self._sha_renew = None

    async def _ensure_scripts(self) -> None:
        """首次使用时加载 Lua 脚本（缓存 SHA）。"""
        if self._sha_acquire is None:
            self._sha_acquire = await self._redis.script_load(self._ACQUIRE_SCRIPT)
            self._sha_release = await self._redis.script_load(self._RELEASE_SCRIPT)
            self._sha_renew = await self._redis.script_load(self._RENEW_SCRIPT)

    def _lock_key(self, stage: str) -> str:
        return f"gpu:lock:{stage}"

    def _lock_value(self, task_id: str) -> str:
        """锁值 = task_id:timestamp（用于心跳校验）。"""
        return f"{task_id}:{int(time.time() * 1000)}"

    async def acquire(
        self, task_id: str, stage: str, *, timeout: float = 300.0
    ) -> GPUHandle:
        """获取 GPU 锁（阻塞等待）。

        Args:
            task_id: Celery task id（唯一标识持有者）
            stage: 阶段名（asr/ocr/embedding/llm）
            timeout: 最大等待秒数，超时抛出 TimeoutError

        Returns:
            GPUHandle，用作 async with 上下文管理器
        """
        await self._ensure_scripts()
        key = self._lock_key(stage)
        value = self._lock_value(task_id)
        deadline = time.monotonic() + timeout

        while True:
            # 尝试获取
            try:
                ok = await self._redis.evalsha(
                    self._sha_acquire, 1, key, value, self._ttl
                )
            except aioredis.NoScriptError:
                # 脚本被 flush，重新加载
                await self._ensure_scripts()
                continue

            if ok:
                # 启动心跳续租任务
                handle = GPUHandle(task_id, stage, self)
                asyncio.create_task(self._heartbeat(handle))
                # GPU 利用率置 100%（独占）
                GPU_UTILIZATION.labels(device_id="0", stage=stage).set(100.0)
                return handle

            # 未获取到，短暂等待后重试
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"GPU lock acquire timeout: stage={stage}, task={task_id}"
                )
            await asyncio.sleep(0.5)

    async def release(self, task_id: str, stage: str) -> bool:
        """释放 GPU 锁（仅持有者可释放）。"""
        await self._ensure_scripts()
        key = self._lock_key(stage)
        value = self._lock_value(task_id)
        try:
            result = await self._redis.evalsha(self._sha_release, 1, key, value)
            # GPU 利用率置 0%
            GPU_UTILIZATION.labels(device_id="0", stage=stage).set(0.0)
            return result == 1
        except aioredis.NoScriptError:
            await self._ensure_scripts()
            return await self.release(task_id, stage)

    async def _heartbeat(self, handle: GPUHandle) -> None:
        """后台心跳：定期刷新锁 TTL。"""
        key = self._lock_key(handle.stage)
        value = self._lock_value(handle.task_id)

        try:
            while handle._acquired:
                await asyncio.sleep(self._heartbeat_interval)
                if not handle._acquired:
                    break
                try:
                    await self._redis.evalsha(
                        self._sha_renew, 1, key, value, self._ttl
                    )
                except aioredis.NoScriptError:
                    await self._ensure_scripts()
                except Exception:
                    # 续租失败（锁可能已被抢占或过期），停止心跳
                    break
        except asyncio.CancelledError:
            pass

    @asynccontextmanager
    async def lock(self, task_id: str, stage: str, *, timeout: float = 300.0):
        """便捷上下文管理器：`async with gpu.lock(...):`"""
        handle = await self.acquire(task_id, stage, timeout=timeout)
        try:
            yield handle
        finally:
            await handle.release()


def get_gpu_manager() -> GPUResourceManager:
    """获取 GPU 资源管理器实例。

    不使用 lru_cache，因为 Celery eager 模式使用 anyio.run() 创建临时事件循环，
    循环关闭后缓存的管理器会持有失效的 Redis 连接。每次调用创建新实例，
    连接池由 redis-py 管理。
    """
    return GPUResourceManager()