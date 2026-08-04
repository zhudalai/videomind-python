"""Embedding 后端延迟/维度实测 —— 填充优化对比表的真实数字。

跑三件事：
  1. 当前 BGE-M3 @ CPU：单 query encode 延迟中位数（这是查询热路径现状基线）
  2. OpenRouter nemotron-3-embed-1b:free：单 query 延迟 + 返回维度
     （验证 API 路径能否对齐 Qdrant collection 的 1024 维 —— 方向 A 可行性硬约束）
  3. 报告维度是否对齐，给出换 API 后是否必须重索引的结论

用法：
    /d/ProgramData/anaconda3/python.exe scripts/eval/bench_embedding.py
"""

from __future__ import annotations

import json
import os
import statistics
import time
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
REPO = EVAL_DIR.parent.parent

QUERY = "视频里主要讲了什么内容，有哪些关键观点？"
N = 10

def _load_env() -> dict[str, str]:
    """读 .env 拿 EMBEDDING_API_* 与 LLM_API_KEY（OpenRouter key 共用）。"""
    env: dict[str, str] = {}
    envp = REPO / ".env"
    if not envp.exists():
        return env
    for line in envp.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        env[k.strip()] = v.strip().split("#", 1)[0].strip()
    return env


def bench_local_bge_m3() -> dict:
    """BGE-M3 @ CPU 单 query encode 延迟（当前生产后端）。"""
    print("\n[1/2] BGE-M3 @ CPU（当前生产后端）...")
    try:
        from sentence_transformers import SentenceTransformer
    except Exception as e:
        return {"error": f"sentence_transformers 不可用: {e}"}

    t0 = time.perf_counter()
    model = SentenceTransformer("BAAI/bge-m3", device="cpu")
    load_s = time.perf_counter() - t0
    print(f"  model load: {load_s:.2f}s")

    # warmup（首次 encode 含图编译/缓存开销）
    model.encode([QUERY], normalize_embeddings=True)

    lats: list[float] = []
    for _ in range(N):
        t0 = time.perf_counter()
        model.encode([QUERY], batch_size=1, normalize_embeddings=True)
        lats.append(time.perf_counter() - t0)
    lats.sort()
    med = statistics.median(lats)
    print(f"  single-query encode: median={med*1000:.1f}ms  min={lats[0]*1000:.1f}ms  max={lats[-1]*1000:.1f}ms")
    return {
        "device": "cpu",
        "model": "BAAI/bge-m3",
        "load_s": round(load_s, 2),
        "median_ms": round(med * 1000, 1),
        "min_ms": round(lats[0] * 1000, 1),
        "max_ms": round(lats[-1] * 1000, 1),
        "dim": 1024,
    }


def bench_openrouter_embed() -> dict:
    """OpenRouter nemotron-3-embed-1b:free 单 query 延迟 + 维度。"""
    print("\n[2/2] OpenRouter nemotron-3-embed-1b:free（候选 API 后端）...")
    env = _load_env()
    base = env.get("EMBEDDING_API_BASE_URL", "https://openrouter.ai/api/v1")
    key = env.get("EMBEDDING_API_KEY") or env.get("LLM_API_KEY")
    model = env.get("EMBEDDING_API_MODEL", "nvidia/nemotron-3-embed-1b:free")
    if not key:
        return {"error": "未找到 EMBEDDING_API_KEY/LLM_API_KEY"}

    try:
        import httpx
    except Exception as e:
        return {"error": f"httpx 不可用: {e}"}

    url = base.rstrip("/") + "/embeddings"
    print(f"  POST {url}  model={model}")

    # 先打一次拿维度 + 单次延迟
    lats: list[float] = []
    first_dim = None
    err = None
    for i in range(N):
        body = {"model": model, "input": QUERY}
        t0 = time.perf_counter()
        try:
            r = httpx.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {key}"},
                timeout=30.0,
            )
            lats.append(time.perf_counter() - t0)
            if r.status_code != 200:
                err = f"{r.status_code}: {r.text[:200]}"
                break
            data = r.json()
            if first_dim is None:
                d = data.get("data") or []
                if d:
                    first_dim = len(d[0].get("embedding", []))
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            break

    if err:
        print(f"  [FAIL] {err}")
        return {"error": err, "url": url, "model": model}

    lats.sort()
    med = statistics.median(lats)
    print(f"  single-query: median={med*1000:.1f}ms  dim={first_dim}  n={len(lats)}")
    return {
        "provider": "openrouter",
        "model": model,
        "url": url,
        "median_ms": round(med * 1000, 1),
        "dim": first_dim,
    }


def main() -> None:
    local = bench_local_bge_m3()
    api = bench_openrouter_embed()

    print("\n" + "=" * 60)
    print("实测对比")
    print("=" * 60)
    print(f"  当前 (CPU BGE-M3):   {local}")
    print(f"  候选 (OpenRouter nemotron-3-embed-1b): {api}")

    # 维度对齐判定 —— 方向 A 重索引约束
    local_dim = local.get("dim")
    api_dim = api.get("dim")
    if local_dim and api_dim:
        if local_dim == api_dim:
            print(f"\n  [维度一致 {local_dim}] 仍可保留已入库向量？否 —— 即便维度相同，"
                  "不同模型的向量空间不同，相似度不可跨模型比较，必须重索引。")
        else:
            print(f"\n  [维度不一致 local={local_dim} api={api_dim}] "
                  "Qdrant collection 维度固定，换模型必须重建 collection + 重索引全部 chunk video。")

    out = {"local_bge_m3_cpu": local, "openrouter_nemotron_embed": api}
    (EVAL_DIR / "results_bench_embedding.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\nresults written to scripts/eval/results_bench_embedding.json")


if __name__ == "__main__":
    main()
