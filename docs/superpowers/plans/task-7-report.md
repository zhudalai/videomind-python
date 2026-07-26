# Task 7: DeterministicReranker 确定性重排 — 完成报告

**日期**: 2026-07-25
**状态**: DONE

## 目标

实现 `DeterministicReranker`，对 VectorHit 列表进行三阶段线性加权重排序。

## 实现文件

| 文件 | 说明 |
|---|---|
| `src/videomind/core/rag/rerank.py` | 确定性重排器实现（48 行） |
| `tests/core/rag/test_rerank.py` | 8 个测试用例 |

## 算法

三阶段线性加权（权重总和 1.0）：

| 阶段 | 权重 | 说明 |
|---|---|---|
| position | 0.3 | 位次衰减：1 - i/n，越靠前分越高 |
| source | 0.2 | 来源类型偏好：asr(0.3) > mixed(0.25) > ocr(0.2) > other(0.15) |
| original | 0.5 | 原始得分 clamp 到 [0, 1] |

## 测试结果

```
tests/core/rag/test_rerank.py - 8 passed
tests/core/rag/ (全部47个) - 47 passed, 无回归
```

## 测试覆盖

| 测试 | 场景 |
|---|---|
| test_rerank_empty | 空列表返回 [] |
| test_rerank_single_hit | 单 hit score 正确计算 |
| test_rerank_ordering_asr_first | asr/mixed/ocr 同向加分排序 |
| test_rerank_different_source_types | 4 种来源类型验证 source bonus |
| test_rerank_position_weights | 同条件下位次靠前胜出 |
| test_rerank_original_score_dominates | 原始分 0.5 权重主导，高分胜出 |
| test_rerank_score_clamped | score 超出 [0,1] 正确 clamp |
| test_rerank_inplace_modification | 原地修改，返回同一列表引用 |