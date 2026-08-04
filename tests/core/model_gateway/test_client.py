"""OpenAICompatibleClient HTTP 客户端单元测试。

测试 chat / stream_chat / 错误处理 / 端点 URL 验证。
用 unittest.mock 替代 respx（respx 未安装），mock httpx.AsyncClient。
"""

import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from videomind.core.model_gateway.types import ChatRequest, ChatResponse, ChatChunk, TokenUsage
from videomind.core.model_gateway.http_client import OpenAICompatibleClient


# ----------------------------------------------------------------
# 辅助方法
# ----------------------------------------------------------------


def _make_mock_http(mock_resp):
    """构造 mock httpx.AsyncClient，post 返回 mock_resp，aclose 为 AsyncMock。"""
    mock_http = MagicMock()
    mock_http.post = AsyncMock(return_value=mock_resp)
    mock_http.aclose = AsyncMock()
    return mock_http


def _make_mock_response_200():
    """构造一个 mock httpx Response (status=200, JSON body)。"""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "choices": [
            {
                "message": {"content": "你好，我是 AI 助手"},
                "finish_reason": "stop",
            }
        ],
        "model": "test-model",
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        },
    }
    return mock_resp


def _make_mock_response_500():
    """构造一个 mock httpx Response (status=500, raise_for_status 抛 HTTPStatusError)。"""
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "500 Server Error",
            request=MagicMock(),
            response=MagicMock(status_code=500),
        )
    )
    return mock_resp


# ----------------------------------------------------------------
# fixtures
# ----------------------------------------------------------------


@pytest.fixture
def chat_request():
    """创建一个基础 ChatRequest 实例。"""
    return ChatRequest(
        messages=[{"role": "user", "content": "说一句中文"}],
        model="test-model",
        temperature=0.3,
        max_tokens=100,
    )


# ----------------------------------------------------------------
# test_chat_success
# ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_success(chat_request):
    """mock HTTP 200，返回 ChatResponse(content=..., model=..., usage=...)。"""
    mock_resp = _make_mock_response_200()
    mock_http = _make_mock_http(mock_resp)

    with patch("httpx.AsyncClient", return_value=mock_http):
        client = OpenAICompatibleClient(base_url="http://test.local/v1", api_key="sk-test")
        resp = await client.chat(chat_request)
        await client.close()

    assert isinstance(resp, ChatResponse)
    assert resp.content == "你好，我是 AI 助手"
    assert resp.model == "test-model"
    assert resp.usage.prompt_tokens == 10
    assert resp.usage.completion_tokens == 5
    assert resp.usage.total_tokens == 15
    assert resp.finish_reason == "stop"


# ----------------------------------------------------------------
# test_chat_http_error
# ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_http_error(chat_request):
    """mock HTTP 500，应 raise httpx.HTTPStatusError（带状态码）。"""
    mock_resp = _make_mock_response_500()
    mock_http = _make_mock_http(mock_resp)

    with patch("httpx.AsyncClient", return_value=mock_http):
        client = OpenAICompatibleClient(base_url="http://test.local", api_key="sk-test")
        with pytest.raises(httpx.HTTPStatusError):
            await client.chat(chat_request)
        await client.close()


# ----------------------------------------------------------------
# test_stream_chat_delta_chunks
# ----------------------------------------------------------------


STREAM_LINES = [
    'data: {"choices":[{"delta":{"content":"你"},"finish_reason":null}]}',
    "",
    'data: {"choices":[{"delta":{"content":"好"},"finish_reason":"stop"}]}',
    "",
    "data: [DONE]",
    "",
]


async def _make_aiter_lines():
    """模拟 httpx aiter_lines() 的 async generator。"""
    for line in STREAM_LINES:
        yield line


@pytest.mark.asyncio
async def test_stream_chat_delta_chunks(chat_request):
    """mock SSE 流，验证生成 2 个 ChatChunk 且 content/finish_reason 正确。"""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.raise_for_status = MagicMock()
    mock_resp.aiter_lines = _make_aiter_lines

    mock_http = _make_mock_http(mock_resp)

    with patch("httpx.AsyncClient", return_value=mock_http):
        client = OpenAICompatibleClient(base_url="http://test.local", api_key="sk-test")
        chunks = []
        async for chunk in client.stream_chat(chat_request):
            chunks.append(chunk)
        await client.close()

    assert len(chunks) == 2
    assert chunks[0].delta_content == "你"
    assert chunks[0].finish_reason is None
    assert chunks[1].delta_content == "好"
    assert chunks[1].finish_reason == "stop"


# ----------------------------------------------------------------
# test_chat_respects_base_url
# ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_respects_base_url(chat_request):
    """验证 post URL 为 {llm_base_url}/chat/completions，json 包含正确 model/temperature。"""
    mock_resp = _make_mock_response_200()
    mock_http = _make_mock_http(mock_resp)

    base = "https://opencode.ai/zen/v1"

    with patch("httpx.AsyncClient", return_value=mock_http):
        client = OpenAICompatibleClient(base_url=base, api_key="sk-test")
        await client.chat(chat_request)
        await client.close()

    mock_http.post.assert_called_once()
    called_url = mock_http.post.call_args[0][0]
    assert called_url == "https://opencode.ai/zen/v1/chat/completions"

    called_json = mock_http.post.call_args[1]["json"]
    assert called_json["model"] == "test-model"
    assert called_json["stream"] is False
    assert called_json["temperature"] == 0.3


# ----------------------------------------------------------------
# test_chat_reasoning_forwarding
# ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_chat_reasoning_false_serialized():
    """reasoning=False -> body 含 {"reasoning": {"enabled": False}}（关 OpenRouter 思维链）。"""
    mock_resp = _make_mock_response_200()
    mock_http = _make_mock_http(mock_resp)
    req = ChatRequest(messages=[{"role": "user", "content": "x"}], reasoning=False)

    with patch("httpx.AsyncClient", return_value=mock_http):
        client = OpenAICompatibleClient(base_url="http://t.local/v1", api_key="sk")
        await client.chat(req)
        await client.close()

    body = mock_http.post.call_args[1]["json"]
    assert body["reasoning"] == {"enabled": False}


@pytest.mark.asyncio
async def test_chat_reasoning_true_serialized():
    """reasoning=True -> body 含 {"reasoning": {"enabled": True}}。"""
    mock_resp = _make_mock_response_200()
    mock_http = _make_mock_http(mock_resp)
    req = ChatRequest(messages=[{"role": "user", "content": "x"}], reasoning=True)

    with patch("httpx.AsyncClient", return_value=mock_http):
        client = OpenAICompatibleClient(base_url="http://t.local/v1", api_key="sk")
        await client.chat(req)
        await client.close()

    body = mock_http.post.call_args[1]["json"]
    assert body["reasoning"] == {"enabled": True}


@pytest.mark.asyncio
async def test_chat_reasoning_none_omitted():
    """reasoning=None -> body 不含 reasoning 字段（按模型默认，向后兼容旧行为）。"""
    mock_resp = _make_mock_response_200()
    mock_http = _make_mock_http(mock_resp)
    req = ChatRequest(messages=[{"role": "user", "content": "x"}])

    with patch("httpx.AsyncClient", return_value=mock_http):
        client = OpenAICompatibleClient(base_url="http://t.local/v1", api_key="sk")
        await client.chat(req)
        await client.close()

    body = mock_http.post.call_args[1]["json"]
    assert "reasoning" not in body