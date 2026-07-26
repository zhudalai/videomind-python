# gw-task-3: ModelHealthStore 三态熔断器

**状态**: DONE
**时间**: 2026-07-25

## 产出文件

| 文件 | 路径 |
|------|------|
| 类型定义（补充） | `src/videomind/core/model_gateway/types.py` |
| 实现 | `src/videomind/core/model_gateway/circuit.py` |
| 测试 | `tests/core/model_gateway/test_circuit.py` |

## 实现内容

`ModelHealthStore` 实现三态熔断器状态机，纯内存模式（redis=None），为每个 model_id 维护独立状态。

- **CLOSED**: 正常放行，累积 `mark_failure` 计数。
- **OPEN**: 熔断拒绝，`allow_call` 返回 `False`。经过 `open_duration_ms` 后自动转 HALF_OPEN。
- **HALF_OPEN**: 限量探测（`half_open_max_calls` 个），成功转 CLOSED，失败回 OPEN（重置冷却计时）。

关键参数：
- `failure_threshold`（默认 3）：连续失败触发 OPEN。
- `open_duration_ms`（默认 30000）：OPEN 冷却时间。
- `half_open_max_calls`（默认 2）：HALF_OPEN 阶段并发探测数。

## 测试用例（8 个全部通过）

1. **test_initial_allow_call** — 初始 CLOSED，允许调用
2. **test_trip_failure_threshold** — failure_threshold=2，第 2 次失败后 OPEN
3. **test_success_resets** — mark_success 重置失败计数器
4. **test_open_then_half_open_then_success** — OPEN -> HALF_OPEN -> 探测成功 -> CLOSED
5. **test_half_open_failure_back_to_open** — HALF_OPEN 探测失败，回到 OPEN 重置计时
6. **test_half_open_max_probe_slots** — half_open_max_calls=2，第 3 个探测被拒绝
7. **test_different_models_are_isolated** — model-A 熔断不影响 model-B
8. **test_get_state** — get_state 返回各阶段正确状态

## 测试结果

```
tests/core/model_gateway/test_circuit.py::test_initial_allow_call PASSED
tests/core/model_gateway/test_circuit.py::test_trip_failure_threshold PASSED
tests/core/model_gateway/test_circuit.py::test_success_resets PASSED
tests/core/model_gateway/test_circuit.py::test_open_then_half_open_then_success PASSED
tests/core/model_gateway/test_circuit.py::test_half_open_failure_back_to_open PASSED
tests/core/model_gateway/test_circuit.py::test_half_open_max_probe_slots PASSED
tests/core/model_gateway/test_circuit.py::test_different_models_are_isolated PASSED
tests/core/model_gateway/test_circuit.py::test_get_state PASSED
8 passed in 0.45s

完整 suite: 74 passed (无回归)
```