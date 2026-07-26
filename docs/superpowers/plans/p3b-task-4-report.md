# Phase 3B Task 4: Critic 评审器 + EvidenceVerifier 证据校验器 — 完成报告

**状态**: DONE
**日期**: 2026-07-26
**测试**: 5/5 新增 PASS，132/132 全量回归 PASS

---

## 创建的文件

| 文件 | 说明 |
|------|------|
| `src/videomind/core/agent_loop/verifier.py` | EvidenceVerifier 硬校验器（65 行） |
| `src/videomind/core/agent_loop/critic.py` | Critic 评审器 + EvidenceVerifier 组合（105 行） |
| `tests/core/agent_loop/test_verifier.py` | 证据校验器测试（2 tests） |
| `tests/core/agent_loop/test_critic.py` | 评审器测试（3 tests） |

## 实现细节

### EvidenceVerifier（verifier.py）

纯函数式硬校验，无外部依赖（DB/ASR/OCR）。

- `verify_all(evidence, duration_ms)` — 逐条校验，返回 `(passed: bool, failed: list[Evidence])`
- `_verify_one(ev, duration_ms)` — 单条校验规则：
  - `0 <= ev.timestamp_ms <= duration_ms`（时间戳范围）
  - `len(ev.content) > 0`（内容非空）

### Critic（critic.py）

组合 LLM 质量审查 + EvidenceVerifier 硬校验。

- `critique(state)` 流程:
  1. 构造结构化 prompt 发送 LLM 获取评审数据（coverage_score、structure_ok、hallucination_risk）
  2. 调用 `EvidenceVerifier.verify_all()` 进行硬校验
  3. 综合两者输出 `CriticResult`（`passed = llm_passed and verified_passed`）
- 异常处理：LLM 调用失败时优雅降级，返回 `passed=False` 的默认结果，不抛异常
- 空 result 保护：`state.result is None` 时直接返回失败结果

## 测试覆盖

| 测试 | 场景 | 结果 |
|------|------|------|
| `test_verifier_all_passed_with_valid_evidence` | 合法证据全部通过 | PASS |
| `test_verifier_detects_out_of_range` | 负时间戳/越界/空内容检测 | PASS |
| `test_critic_passes_with_perfect_result` | LLM 返回满分，所有校验通过 | PASS |
| `test_critic_fails_with_low_coverage` | LLM 返回低分，反映不通过 | PASS |
| `test_critic_LLM_error_graceful` | LLM 异常时降级不抛异常 | PASS |

## 回归结果

`python -m pytest tests/core/ -v --tb=short` → **132 passed**, 0 failed, 48.24s