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
        """handle.release() calls manager.release."""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        mgr = GPUResourceManager()
        mgr.release = AsyncMock(return_value=True)
        handle = GPUHandle(task_id="task-1", stage="asr", _manager=mgr)

        asyncio.run(handle.release())
        mgr.release.assert_called_once_with("task-1", "asr")
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
        handle = GPUHandle(task_id="task-1", stage="asr", _manager=mgr)

        async def test_cm():
            async with handle as h:
                assert h is handle
            # __aexit__ should have called release
            mgr.release.assert_called_once()

        asyncio.run(test_cm())


if __name__ == "__main__":
    pytest.main([__file__, "-v"])