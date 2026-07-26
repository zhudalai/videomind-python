# Phase 3A Task 1: Intent 模块数据结构 -- 完成报告

## 状态：DONE

## 概要

按照 TDD 流程，在 `src/videomind/core/intent/` 下建立了意图路由模块的 4 个核心数据类。

## 新增文件

| 文件 | 说明 |
|---|---|
| `src/videomind/core/intent/__init__.py` | 包初始化，公开导出 4 个数据类 |
| `src/videomind/core/intent/types.py` | 数据类定义（107 行） |
| `tests/core/intent/__init__.py` | 测试包初始化（空） |
| `tests/core/intent/test_types.py` | 5 个单元测试 |

## 数据结构

| 数据类 | 字段 | 用途 |
|---|---|---|
| `RewriteContext` | `video_title`, `duration_sec` | 查询改写上下文 |
| `RewriteResult` | `original`, `rewritten`, `sub_queries`, `entities`, `method`, `confidence` | 查询改写结果 |
| `ChannelQuota` | `vector`, `bm25`, `sql` | 多通道检索配额 |
| `IntentRouteResult` | `intent_weights`, `channel_quota`, `primary_intent` | 意图路由结果 |

## 测试结果

```
tests/core/intent/test_types.py::TestRewriteResult::test_rewrite_result_defaults PASSED
tests/core/intent/test_types.py::TestRewriteContext::test_rewrite_context_defaults PASSED
tests/core/intent/test_types.py::TestChannelQuota::test_channel_quota_defaults_to_zero PASSED
tests/core/intent/test_types.py::TestIntentRouteResult::test_intent_route_result_primary_intent PASSED
tests/core/intent/test_types.py::TestIntentRouteResult::test_intent_weights_dict_is_accessible PASSED
```

**新模块：5/5 PASSED**

## 全量回归

```
tests/core/intent/      — 5 passed
tests/core/model_gateway/ — 32 passed, 2 failed (已有 E2E 连通性测试，与本次变更无关)
```

总计：**37 passed, 2 failed（预存 E2E 失败），0 个新回归问题**

## 遵循规范

- [x] 严格 TDD（先 RED 后 GREEN）
- [x] 中文 docstring
- [x] 所有 import 使用 `from __future__ import annotations`
- [x] 使用 `@dataclass` 装饰器
- [x] 可变默认值使用 `field(default_factory=...)`