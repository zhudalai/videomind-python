# GW Task 7: E2E 真实 LLM 调用测试 -- Report

**Status**: DONE
**Date**: 2026-07-26

## 实现摘要

创建了真实 LLM 端到端测试，直接使用 httpx 对 opencode.ai API 发起 chat completions 调用，验证连通性和流式模式。

### 新增文件

| 文件 | 说明 |
|---|---|
| `tests/core/model_gateway/test_e2e.py` | 2 个 E2E 测试 |

### 配置

配置从项目根目录 `.env` 读取（通过 `python-dotenv`）：
- `LLM_BASE_URL` = `https://opencode.ai/zen/v1`
- `LLM_MODEL` = `deepseek-v4-flash-free`
- `LLM_API_KEY` 从 `.env` 加载

### 测试覆盖（2 个）

| 测试 | 覆盖场景 |
|---|---|
| `test_real_llm_connectivity` | 非流式 chat completions：HTTP 200、choices 非空、usage 有效、返回文本非空 |
| `test_real_llm_stream` | 流式模式：SSE 片段解析、content+reasoning_content 收集、最终文本非空 |

### 关键发现与调整

1. **Reasoning 模型兼容**：`deepseek-v4-flash-free` 是 reasoning 模型，响应中同时包含 `content` 和 `reasoning_content` 字段。当 `max_tokens` 只有 10 时，全部被 reasoning 吃光导致 content 为空。将 `max_tokens` 调整为 200 后正常输出内容。

2. **断言双字段检查**：使用 `_extract_response_text()` 辅助函数同时检查 `content` 和 `reasoning_content`，适配 reasoning 和非 reasoning 两种模型。

3. **网络预检**：每个测试前置 `GET /models` 探测，超时或 500+ 状态码则 `pytest.skip`，避免因网络不可达直接报 FAIL。

### 回归结果

```
tests/core/model_gateway/test_e2e.py::test_real_llm_connectivity PASSED
tests/core/model_gateway/test_e2e.py::test_real_llm_stream PASSED
============================== 2 passed in 8.23s ==============================
```

两个 E2E 测试均通过，验证 opencode.ai API 非流式和流式两种模式可用。