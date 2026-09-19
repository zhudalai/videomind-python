"""L1 unit tests for task_orchestration/gpu.py - pure logic parts."""

from videomind.application.task_orchestration.gpu import (
    GPUResourceManager,
    GPUHandle,
)


class TestGpuKeys:
    """Tests for GPU lock/heartbeat key construction (via instance methods)."""

    def test_lock_key_format(self):
        """_lock_key(stage) -> 'gpu:lock:{stage}'."""
        mgr = GPUResourceManager()
        assert mgr._lock_key("asr") == "gpu:lock:asr"
        assert mgr._lock_key("ocr") == "gpu:lock:ocr"
        assert mgr._lock_key("embedding") == "gpu:lock:embedding"

    def test_lock_key_different_stages(self):
        """Different stages produce different keys."""
        mgr = GPUResourceManager()
        assert mgr._lock_key("asr") != mgr._lock_key("ocr")

    def test_lock_value_format(self):
        """_lock_value(task_id) -> '{task_id}:{timestamp_ms}'."""
        mgr = GPUResourceManager()
        task_id = "task-123"
        value = mgr._lock_value(task_id)
        assert value.startswith(f"{task_id}:")
        # timestamp part should be numeric
        ts_part = value.split(":")[-1]
        assert ts_part.isdigit()
        # timestamp should be recent (within last few seconds)
        import time
        assert abs(int(ts_part) - int(time.time() * 1000)) < 5000

    def test_lock_value_different_task_ids(self):
        """Different task_ids produce different values."""
        mgr = GPUResourceManager()
        assert mgr._lock_value("task1") != mgr._lock_value("task2")


class TestLuaScripts:
    """Tests for Lua script constants (pure string checks)."""

    def test_acquire_script_not_empty(self):
        assert GPUResourceManager._ACQUIRE_SCRIPT is not None
        assert len(GPUResourceManager._ACQUIRE_SCRIPT) > 0

    def test_acquire_script_contains_set_nx_ex(self):
        """Acquire script uses SET NX EX for atomic lock."""
        script = GPUResourceManager._ACQUIRE_SCRIPT.upper()
        assert "SET" in script
        assert "NX" in script
        assert "EX" in script

    def test_release_script_not_empty(self):
        assert GPUResourceManager._RELEASE_SCRIPT is not None
        assert len(GPUResourceManager._RELEASE_SCRIPT) > 0

    def test_release_script_checks_owner(self):
        """Release script verifies owner before DEL."""
        script = GPUResourceManager._RELEASE_SCRIPT.upper()
        assert "GET" in script
        assert "DEL" in script
        assert "ARGV" in script

    def test_renew_script_not_empty(self):
        assert GPUResourceManager._RENEW_SCRIPT is not None
        assert len(GPUResourceManager._RENEW_SCRIPT) > 0

    def test_renew_script_refreshes_ttl(self):
        """Renew script refreshes TTL if owner matches."""
        script = GPUResourceManager._RENEW_SCRIPT.upper()
        assert "GET" in script
        assert "EXPIRE" in script or "PEXPIRE" in script


class TestGPUHandle:
    """Tests for GPUHandle dataclass."""

    def test_handle_creation(self):
        """GPUHandle can be created with required fields."""
        mgr = GPUResourceManager()
        handle = GPUHandle(task_id="task-1", stage="asr", _manager=mgr)
        assert handle.task_id == "task-1"
        assert handle.stage == "asr"
        assert handle._acquired is True

    def test_handle_release_calls_manager(self):
        """handle.release() calls manager.release with (stage, lock_value)."""
        import asyncio
        from unittest.mock import AsyncMock

        mgr = GPUResourceManager()
        mgr.release = AsyncMock(return_value=True)
        handle = GPUHandle(task_id="task-1", stage="asr", _manager=mgr, _lock_value="task-1:123")

        asyncio.run(handle.release())
        mgr.release.assert_called_once_with("asr", "task-1:123")
        assert handle._acquired is False

    def test_handle_double_release_idempotent(self):
        """Double release doesn't call manager twice."""
        import asyncio
        from unittest.mock import AsyncMock

        mgr = GPUResourceManager()
        mgr.release = AsyncMock(return_value=True)
        handle = GPUHandle(task_id="task-1", stage="asr", _manager=mgr)

        asyncio.run(handle.release())
        asyncio.run(handle.release())  # second call
        assert mgr.release.call_count == 1

    def test_handle_async_context_manager(self):
        """GPUHandle works as async context manager."""
        import asyncio
        from unittest.mock import AsyncMock

        mgr = GPUResourceManager()
        mgr.release = AsyncMock(return_value=True)
        handle = GPUHandle(task_id="task-1", stage="asr", _manager=mgr, _lock_value="v-1")

        async def test_cm():
            async with handle as h:
                assert h is handle
            # __aexit__ should have called release
            mgr.release.assert_called_once()

        asyncio.run(test_cm())


class TestLockValueConsistency:
    """Regression: acquire 写入的锁值必须与续租/释放使用的是同一份。

    旧实现在 acquire/heartbeat/release 三处各自重新生成锁值（内含时间戳），
    Lua 的 GET==ARGV 比对在续租/释放时永远失败——超 30s TTL 的任务互斥失效。
    """

    def test_acquire_stores_value_in_handle(self):
        """acquire 成功后 handle._lock_value == 写入 Redis 的值（不再重新生成）。"""
        import asyncio
        from unittest.mock import AsyncMock

        mgr = GPUResourceManager()
        mgr._redis = AsyncMock()
        mgr._redis.script_load.return_value = "sha-x"
        # SET NX EX 成功（返回 1）
        mgr._redis.evalsha.return_value = 1

        async def run():
            handle = await mgr.acquire("task-9", "asr", timeout=1.0)
            return handle

        handle = asyncio.run(run())
        # evalsha(sha, 1, key, value, ttl) —— 锁值在 args[3]
        written = mgr._redis.evalsha.call_args_list[0].args[3]
        assert handle._lock_value == written
        assert written.startswith("task-9:")

    def test_release_uses_stored_value_not_regenerated(self):
        """release 传给 Lua 的值 == acquire 写入的值（时间戳不再刷新）。"""
        import asyncio
        import time
        from unittest.mock import AsyncMock

        mgr = GPUResourceManager()
        mgr._redis = AsyncMock()
        mgr._redis.script_load.return_value = "sha-x"
        mgr._redis.evalsha.return_value = 1

        async def run():
            handle = await mgr.acquire("task-9", "ocr", timeout=1.0)
            await asyncio.sleep(0.05)  # 若重新生成，时间戳位必然不同
            await handle.release()
            return handle

        handle = asyncio.run(run())
        calls = mgr._redis.evalsha.call_args_list
        acquire_val = calls[0].args[3]
        release_val = calls[-1].args[3]
        assert acquire_val == release_val == handle._lock_value
        # 锁值的时间戳是 acquire 时刻的，不是释放时刻的
        assert int(release_val.split(":")[-1]) <= int(time.time() * 1000)

    def test_heartbeat_reuses_stored_value(self):
        """心跳续租传给 Lua 的值 == acquire 写入的值。"""
        import asyncio
        from unittest.mock import AsyncMock

        mgr = GPUResourceManager()
        mgr._redis = AsyncMock()
        mgr._redis.script_load.return_value = "sha-x"
        mgr._redis.evalsha.return_value = 1
        # 心跳间隔默认 max(5, ttl//3)；压到最小让心跳尽快跑一轮
        mgr._heartbeat_interval = 0.01

        async def run():
            handle = await mgr.acquire("task-9", "embedding", timeout=1.0)
            await asyncio.sleep(0.05)  # 等至少一轮心跳
            await handle.release()
            return handle

        asyncio.run(run())
        calls = mgr._redis.evalsha.call_args_list
        acquire_val = calls[0].args[3]
        # 心跳的 renew 调用也携带同一值
        renew_vals = [c.args[3] for c in calls[1:]]
        assert acquire_val in renew_vals


if __name__ == "__main__":
    pytest.main([__file__, "-v"])