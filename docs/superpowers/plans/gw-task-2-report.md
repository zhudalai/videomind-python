# gw-task-2: OpenAICompatibleClient — httpx chat + stream_chat

**状态**: DONE
**时间**: 2026-07-25

## 产出文件

| 文件 | 路径 |
|------|------|
| 实现 | `src/videomind/core/model_gateway/http_client.py` |
| 测试 | `tests/core/model_gateway/test_client.py` |

## 实现内容

`OpenAICompatibleClient` 实现 `__init__`、`chat`、`stream_chat`、`estimate_cost`、`close`。

- `chat` — 发送 POST `{base_url}/chat/completions`，stream=False，解析 JSON 返回 `ChatResponse`
- `stream_chat` — 发送 POST 同端点，stream=True，按 SSE 行解析 yield `ChatChunk`
- `estimate_cost` — 返回 0.0（占位）
- `close` — `await self._client.aclose()`

## 测试用例（4 个全部通过）

1. **test_chat_success** — mock HTTP 200 → 返回 `ChatResponse`，验证 content/model/usage/finish_reason
2. **test_chat_http_error** — mock HTTP 500 → `httpx.HTTPStatusError` 被抛出
3. **test_stream_chat_delta_chunks** — mock SSE → 生成 2 个 ChatChunk，delta_content 为 "你"/"好"，第 2 个 finish_reason="stop"
4. **test_chat_respects_base_url** — 验证 URL 为 `{base_url}/chat/completions`，json body 含正确 model/stream/temperature

## Mock 策略

使用 `unittest.mock.patch("httpx.AsyncClient")` 替代 respx（respx 未安装）：
- `mock_http.post = AsyncMock(return_value=mock_resp)`
- `mock_http.aclose = AsyncMock()`
- SSE 流用 async generator 模拟 `aiter_lines`

## 测试结果

```
tests/core/model_gateway/test_client.py::test_chat_success PASSED
tests/core/model_gateway/test_client.py::test_chat_http_error PASSED
tests/core/model_gateway/test_client.py::test_stream_chat_delta_chunks PASSED
tests/core/model_gateway/test_client.py::test_chat_respects_base_url PASSED
4 passed in 0.13s

完整 suite: 7 passed (test_types.py 3 + test_client.py 4)
```