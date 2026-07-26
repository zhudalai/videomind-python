# Phase 3A Task 2 报告: QueryRewriter + RuleRewriter

**日期**: 2026-07-26
**状态**: DONE

## 变更文件

| 文件 | 操作 |
|------|------|
| `tests/core/intent/test_rewriter.py` | 新建 - 5 个测试 (3 RuleRewriter + 2 QueryRewriter) |
| `src/videomind/core/intent/rewriter.py` | 新建 - RuleRewriter + QueryRewriter 实现 |

## TDD 流程

1. 写测试 -> 5/5 FAIL (ModuleNotFoundError: No module named 'rewriter')
2. 写实现 -> 5/5 PASS
3. 全量回归 -> **103 passed**, 0 failed, 1 warning

## 实现摘要

### RuleRewriter
- 纯规则改写，不依赖 LLM
- 同义词扩展 (SYNONYMS 字典包含 视频/内容/总结/原话/画面)
- 多意图自动拆解：按 `, `、`,`、`和` 分隔，最多 4 个子问题
- 置信度固定 0.6，method = "rule"

### QueryRewriter
- LLM 优先 + 规则兜底架构
- LLM 成功且置信度 >= 阈值 (默认 0.7) 时返回 LLM 结果
- LLM 异常/低置信度时降级到 RuleRewriter
- 通过 `structlog` 记录 fallback 警告日志

### 修复项
- 原规范中 `imported_prompt` 变量名与 `prompt` 使用不一致 -> 统一为 `prompt`
- `RuleRewriter.rewrite()` 的 `_context` 参数类型标注为 `RewriteContext | None = None`
- `ChatRequest` 从 `videomind.core.model_gateway.types` 正确导入

## 测试覆盖

| # | 测试 | 场景 |
|---|------|------|
| 1 | `test_simple_rewrite_no_expansion` | 无同义词匹配，original 不变 |
| 2 | `test_synonym_expansion` | 同义词扩展，rewritten 比 original 长 |
| 3 | `test_multi_intent_split` | 多意图按分隔符自动拆解 |
| 4 | `test_llm_rewrite_success` | LLM 返回合法 JSON，提取 RewriteResult |
| 5 | `test_llm_failure_falls_back_to_rule` | LLM 异常 → 规则兜底 |