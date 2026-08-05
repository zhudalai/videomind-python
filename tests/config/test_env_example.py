"""环境配置样例守卫 —— D-α：RERANK_TOP_N=60，避免宽召回 gold 被 api rerank top-20 截断。"""
from __future__ import annotations

from pathlib import Path

_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"


def _val(env_text: str, key: str) -> str | None:
    for line in env_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == key:
            return v.strip().split("#", 1)[0].strip()
    return None


def test_rerank_top_n_is_60():
    """D-α 宽召回 + rerank_top_n 跟随 recall：api rerank 不得截断宽召回的 gold。"""
    assert _EXAMPLE.exists()
    val = _val(_EXAMPLE.read_text(encoding="utf-8"), "RERANK_TOP_N")
    assert val == "60", f"RERANK_TOP_N 应为 60（D-α 宽召回不截断），实为 {val}"
