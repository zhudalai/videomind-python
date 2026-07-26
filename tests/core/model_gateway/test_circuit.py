"""ModelHealthStore 三态熔断器单元测试。

测试 CircuitState 状态机：
    CLOSED --(连续失败>=阈值)--> OPEN
    OPEN   --(经过 open_duration_ms)--> HALF_OPEN
    HALF_OPEN --(探测成功)--> CLOSED
    HALF_OPEN --(探测失败)--> OPEN（重置计时）

所有测试使用纯内存模式（redis=None），不依赖外部 Redis。
"""

import asyncio

import pytest

from videomind.core.model_gateway.types import CircuitState
from videomind.core.model_gateway.circuit import ModelHealthStore


# ---------------------------------------------------------------------------
# 测试 1: 初始状态允许调用
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_initial_allow_call():
    """新模型默认 CLOSED 状态，允许调用。"""
    store = ModelHealthStore(redis=None)
    assert await store.allow_call("model-A") is True


# ---------------------------------------------------------------------------
# 测试 2: 连续失败达到阈值后触发熔断
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_trip_failure_threshold():
    """failure_threshold=2 时，第 2 次失败后变为 OPEN，拒绝调用。"""
    store = ModelHealthStore(redis=None, failure_threshold=2)
    await store.mark_failure("model-A")  # 1
    assert await store.allow_call("model-A") is True
    await store.mark_failure("model-A")  # 2 -> OPEN
    assert await store.allow_call("model-A") is False


# ---------------------------------------------------------------------------
# 测试 3: mark_success 重置失败计数
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_success_resets():
    """失败后调用 mark_success 应重置失败计数器，后续失败不会立即触发熔断。"""
    store = ModelHealthStore(redis=None, failure_threshold=2)
    await store.mark_failure("model-A")
    await store.mark_success("model-A")  # 重置计数为 0
    await store.mark_failure("model-A")  # 又变成 1 次
    assert await store.allow_call("model-A") is True  # 未超阈值


# ---------------------------------------------------------------------------
# 测试 4: OPEN -> HALF_OPEN -> 成功探测 -> CLOSED
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_open_then_half_open_then_success():
    """熔断打开后，冷却期满后变为 HALF_OPEN，探测成功恢复 CLOSED。"""
    store = ModelHealthStore(redis=None, failure_threshold=1, open_duration_ms=100)
    # 触发 OPEN
    await store.mark_failure("model-A")  # 1 -> OPEN
    assert await store.allow_call("model-A") is False  # 仍在冷却期
    # 等冷却期过后
    await asyncio.sleep(0.15)
    assert await store.allow_call("model-A") is True  # HALF_OPEN 探测放行
    await store.mark_success("model-A")
    # 成功后回到 CLOSED
    assert await store.allow_call("model-A") is True


# ---------------------------------------------------------------------------
# 测试 5: HALF_OPEN 探测失败 -> 回到 OPEN
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_half_open_failure_back_to_open():
    """HALF_OPEN 状态下探测失败应重新回到 OPEN 并重置计时。"""
    store = ModelHealthStore(redis=None, failure_threshold=1, open_duration_ms=50)
    # 触发 OPEN
    await store.mark_failure("model-A")  # 1 -> OPEN
    # 等冷却期过
    await asyncio.sleep(0.1)
    assert await store.allow_call("model-A") is True  # HALF_OPEN 放行 1 个探测
    # 探测失败 -> 回到 OPEN
    await store.mark_failure("model-A")
    assert await store.allow_call("model-A") is False  # OPEN 拒绝
    # 因为回到 OPEN 时重置了 opened_at_ms，即使短等仍拒绝
    await asyncio.sleep(0.01)
    assert await store.allow_call("model-A") is False


# ---------------------------------------------------------------------------
# 测试 6: HALF_OPEN 限量探测（half_open_max_calls）
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_half_open_max_probe_slots():
    """HALF_OPEN 状态下仅允许 half_open_max_calls 个并发探测。"""
    store = ModelHealthStore(redis=None, failure_threshold=1, open_duration_ms=10, half_open_max_calls=2)
    await store.mark_failure("model-A")  # -> OPEN
    await asyncio.sleep(0.05)
    # 2 个探测放行
    assert await store.allow_call("model-A") is True  # slot 1
    assert await store.allow_call("model-A") is True  # slot 2
    # 第 3 个被拒绝
    assert await store.allow_call("model-A") is False  # slot 3 被阻塞


# ---------------------------------------------------------------------------
# 测试 7: 不同模型 ID 独立隔离
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_different_models_are_isolated():
    """model-A 熔断不影响 model-B 的正常调用。"""
    store = ModelHealthStore(redis=None, failure_threshold=1)
    await store.mark_failure("model-A")  # model-A OPEN
    assert await store.allow_call("model-A") is False
    assert await store.allow_call("model-B") is True  # model-B 不受影响


# ---------------------------------------------------------------------------
# 测试 8: get_state 返回当前状态
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_state():
    """get_state 方法应正确返回各阶段状态。"""
    store = ModelHealthStore(redis=None, failure_threshold=1)
    # 初始 CLOSED
    assert store.get_state("model-A") == CircuitState.CLOSED
    # 失败后 OPEN
    await store.mark_failure("model-A")
    assert store.get_state("model-A") == CircuitState.OPEN
    # 成功后恢复 CLOSED
    await store.mark_success("model-A")
    assert store.get_state("model-A") == CircuitState.CLOSED