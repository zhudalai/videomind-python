"""真实 LLM 调用端到端测试。

使用 opencode.ai API 验证：
1. 非流式 chat completions 可正常响应
2. 流式 mode 可正常返回片段

配置从项目根目录 .env 读取（LLM_BASE_URL / LLM_API_KEY / LLM_MODEL）。

注意：当前使用的 deepseek-v4-flash-free 是 reasoning 模型，响应包含
reasoning_content 字段；content 可能为空（reasoning 耗尽 max_tokens 时）。
因此断言同时检查 content 和 reasoning_content，二者之一有内容即认为成功。
"""

import json
import os

import httpx
import pytest
from dotenv import load_dotenv


def _load_config():
    """从项目 .env 加载 LLM 配置，返回 (base_url, api_key, model)。"""
    project_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    )
    env_path = os.path.join(project_root, ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)

    base_url = os.getenv("LLM_BASE_URL", "https://opencode.ai/zen/v1")
    api_key = os.getenv("LLM_API_KEY", "")
    model = os.getenv("LLM_MODEL", "deepseek-v4-flash-free")
    return base_url, api_key, model


def _extract_response_text(message: dict) -> str:
    """提取响应中的文本内容，兼容 reasoning 模型。

    reasoning 模型（如 deepseek-v4）会把实际回答放在 content 中，
    推理链放在 reasoning_content 中；两个字段都参与检查。
    """
    parts = []
    for key in ("content", "reasoning_content"):
        val = message.get(key, "")
        if val:
            parts.append(val)
    return "".join(parts)


@pytest.mark.asyncio
async def test_real_llm_connectivity():
    """验证 opencode.ai API 非流式 chat completions 能正常响应。

    包含网络预检：/models 不可达则自动跳过。
    兼容 reasoning 模型——同时检查 content 和 reasoning_content 字段。
    """
    base_url, api_key, model = _load_config()
    if not api_key:
        pytest.skip("LLM_API_KEY not set in .env")

    # ---- 网络预检 ----
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0)
        ) as c:
            r = await c.get(
                base_url.rstrip("/") + "/models", follow_redirects=True
            )
            if r.status_code >= 500:
                pytest.skip(f"LLM API /models returned {r.status_code}")
    except Exception as e:
        pytest.skip(f"Network unavailable: {e}")

    # ---- 非流式调用（max_tokens 需足够多，reasoning 模型会吃掉一部分） ----
    req_body = {
        "model": model,
        "messages": [{"role": "user", "content": "回复一个字：好"}],
        "max_tokens": 200,
        "temperature": 0.0,
    }
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=5.0)
    ) as c:
        r = await c.post(
            base_url.rstrip("/") + "/chat/completions",
            json=req_body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        assert r.status_code == 200, f"HTTP {r.status_code}: {r.text[:200]}"
        data = r.json()
        assert "choices" in data, f"No 'choices' in response keys: {list(data.keys())}"
        assert len(data["choices"]) > 0, "Response has empty choices list"
        msg = data["choices"][0].get("message", {})
        text = _extract_response_text(msg)
        assert len(text) > 0, (
            f"Both content and reasoning_content are empty. "
            f"content={repr(msg.get('content', ''))}, "
            f"reasoning_content={repr(msg.get('reasoning_content', ''))}"
        )
        assert "usage" in data, f"No 'usage' in response keys: {list(data.keys())}"
        assert data["usage"]["total_tokens"] > 0, (
            f"total_tokens should be > 0, got {data['usage']['total_tokens']}"
        )


@pytest.mark.asyncio
async def test_real_llm_stream():
    """验证 opencode.ai API 流式模式能正常返回 SSE 片段。

    组装所有 SSE 片段，同时收集 content 和 reasoning_content 两个增量字段。
    """
    base_url, api_key, model = _load_config()
    if not api_key:
        pytest.skip("LLM_API_KEY not set in .env")

    # ---- 网络预检 ----
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(15.0, connect=5.0)
        ) as c:
            r = await c.get(
                base_url.rstrip("/") + "/models", follow_redirects=True
            )
            if r.status_code >= 500:
                pytest.skip(f"LLM API /models returned {r.status_code}")
    except Exception as e:
        pytest.skip(f"Network unavailable: {e}")

    # ---- 流式调用 ----
    req_body = {
        "model": model,
        "messages": [{"role": "user", "content": "说一句话"}],
        "max_tokens": 200,
        "temperature": 0.0,
        "stream": True,
    }
    chunks_count = 0
    alltext = ""
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=5.0)
    ) as c:
        async with c.stream(
            "POST",
            base_url.rstrip("/") + "/chat/completions",
            json=req_body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        ) as resp:
            assert resp.status_code == 200, (
                f"HTTP {resp.status_code}: chunk read failed"
            )
            async for line in resp.aiter_lines():
                if line.startswith("data: ") and line != "data: [DONE]":
                    try:
                        frame = json.loads(line[6:])
                        chunks_count += 1
                        delta = frame["choices"][0].get("delta", {})
                        for key in ("content", "reasoning_content"):
                            val = delta.get(key, "")
                            if val:
                                alltext += val
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

    assert len(alltext) > 0, (
        f"No content gathered from stream "
        f"({chunks_count} SSE chunks received)"
    )