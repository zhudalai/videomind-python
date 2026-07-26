"""ModelHealthStore 三态熔断器实现。

状态机：
    CLOSED --(连续失败 >= failure_threshold)--> OPEN
    OPEN   --(经过 open_duration_ms)-----------> HALF_OPEN
    HALF_OPEN --(探测成功)--------------------> CLOSED
    HALF_OPEN --(探测失败)--------------------> OPEN（重置计时）

支持可选 Redis 注入，无 Redis 时降级为纯内存 dict。
"""

from __future__ import annotations

import time
from typing import Optional

from .types import CircuitState


class _ModelRecord:
    """单个模型的熔断记录（内部使用）。

    Attributes:
        state: 当前熔断状态。
        failures: 连续失败计数。
        opened_at_ms: OPEN 状态开始时刻（毫秒时间戳）。
        probe_count: HALF_OPEN 阶段已放行的探测数。
    """

    __slots__ = ("state", "failures", "opened_at_ms", "probe_count")

    def __init__(self) -> None:
        self.state: CircuitState = CircuitState.CLOSED
        self.failures: int = 0
        self.opened_at_ms: float = 0.0
        self.probe_count: int = 0


class ModelHealthStore:
    """三态熔断器存储。

    为每个模型 ID 维护独立的熔断状态，在模型服务不可用时自动熔断，
    冷却期后以限量探测的方式检测恢复。

    Attributes:
        failure_threshold: 连续失败多少次后触发 OPEN。
        open_duration_ms: OPEN 持续时间（毫秒），此后转为 HALF_OPEN。
        half_open_max_calls: HALF_OPEN 阶段最多放行几个探测请求。
    """

    def __init__(
        self,
        redis: object | None = None,
        *,
        failure_threshold: int = 3,
        open_duration_ms: int = 30000,
        half_open_max_calls: int = 2,
    ) -> None:
        """初始化熔断器。

        Args:
            redis: 可选的 aioredis 客户端，为 None 时使用内存 dict。
            failure_threshold: 触发 OPEN 的连续失败数（默认 3）。
            open_duration_ms: OPEN 冷却时间（毫秒，默认 30000）。
            half_open_max_calls: HALF_OPEN 阶段允许的并发探测数（默认 2）。
        """
        self._redis: object | None = redis
        self.failure_threshold: int = failure_threshold
        self.open_duration_ms: int = open_duration_ms
        self.half_open_max_calls: int = half_open_max_calls
        self._records: dict[str, _ModelRecord] = {}

    async def allow_call(self, model_id: str) -> bool:
        """检查是否允许对指定模型发起调用。

        Args:
            model_id: 模型标识符。

        Returns:
            True 表示允许调用，False 表示熔断拒绝。
        """
        rec: _ModelRecord = self._get_or_create(model_id)

        if rec.state == CircuitState.CLOSED:
            return True

        if rec.state == CircuitState.OPEN:
            now_ms: float = time.time() * 1000
            if (now_ms - rec.opened_at_ms) >= self.open_duration_ms:
                rec.state = CircuitState.HALF_OPEN
                rec.probe_count = 0
            else:
                return False

        # HALF_OPEN 限量放行
        if rec.probe_count < self.half_open_max_calls:
            rec.probe_count += 1
            return True

        return False

    async def mark_success(self, model_id: str) -> None:
        """标记一次成功调用，重置熔断状态为 CLOSED。

        Args:
            model_id: 模型标识符。
        """
        rec: _ModelRecord = self._get_or_create(model_id)
        rec.state = CircuitState.CLOSED
        rec.failures = 0
        rec.probe_count = 0

    async def mark_failure(self, model_id: str) -> None:
        """标记一次失败调用。

        累积失败计数达到 failure_threshold 或在 HALF_OPEN 阶段失败时，
        触发 OPEN 熔断。

        Args:
            model_id: 模型标识符。
        """
        rec: _ModelRecord = self._get_or_create(model_id)
        rec.failures += 1

        if rec.failures >= self.failure_threshold or rec.state == CircuitState.HALF_OPEN:
            rec.state = CircuitState.OPEN
            rec.opened_at_ms = time.time() * 1000
            rec.probe_count = 0

    def get_state(self, model_id: str) -> CircuitState:
        """获取指定模型的当前熔断状态。

        Args:
            model_id: 模型标识符。

        Returns:
            当前 CircuitState 枚举值。
        """
        rec: _ModelRecord | None = self._records.get(model_id)
        if rec is None:
            return CircuitState.CLOSED
        return rec.state

    def _get_or_create(self, model_id: str) -> _ModelRecord:
        """获取或创建模型的内部记录。

        Args:
            model_id: 模型标识符。

        Returns:
            对应模型的 _ModelRecord 实例。
        """
        if model_id not in self._records:
            self._records[model_id] = _ModelRecord()
        return self._records[model_id]