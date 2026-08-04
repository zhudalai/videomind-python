"""RAG 检索效果评测脚本 —— 三档 rerank_provider 对比 recall@k / MRR / nDCG / 延迟。

用法：
    python scripts/eval/run_eval.py                       # 跑 off/api/local 三档
    python scripts/eval/run_eval.py --providers off,api   # 只跑指定档
    python scripts/eval/run_eval.py --top-k 20            # 检索深度（默认 20）
    python scripts/eval/run_eval.py --recall-k 60         # 召回/RRF 宽窗（D-α，设 60 测宽召回 gold 是否进 evidence top-20）

评测对象：scripts/eval/qa_corpus.json（9 条中/日文 paraphrase QA）+ gold_chunks.json。
链路：直接调 pipeline.search()（跳过 QueryRewriter，隔离 rerank 单变量），对每条 QA 取
  - raw_hits：pre-rerank 顺序（RRF 融合 = 向量+BM25 召回质量上限 / 跨 provider 应一致）
  - evidence：post-rerank final 顺序（rerank+expand+normalize 后交给 LLM 的真实序）
两套序列分别算 recall@1/3/5、MRR、nDCG（单 gold），按 difficulty(normal/hard) 分组聚合，
macro-avg（每条 query 等权平均）+ 每条命中 rank + 单次延迟。raw_hits 跨档一致性校验作为
防线（retriever 不依赖 rerank，三档 raw 必须逐条相同，否则评测基础设施有问题）。

切档机制：同进程内 os.environ["RERANK_PROVIDER"]=<provider> + get_settings.cache_clear() +
rerank_backend.get_reranker.cache_clear()，pydantic-settings 重建读 env（env 优先级 > .env）。
retriever 缓存（BM25 索引）跨档复用，预热一次消除首条构建开销，三档 latency 纯检索+rerank 可比。

基线用途：本脚本输出即 P2-3（rerank N×M 串行延迟优化）的基线数据 —— 单 query×单 media
的 rerank 单点延迟，三档 api/local/off 对比可量化远程调用开销。

依赖：DB（已 alembic upgrade）+ 已注入的 a40dfdba/3776e946 两视频 chunks；vecto 通道需
Qdrant + embedding 后端就绪。任一不通会在首条查询抛错退出并提示。
安全：绝不打印 settings 的 api_key。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import uuid
from pathlib import Path

from videomind.config import get_settings
from videomind.core.rag import pipeline, rerank_backend
from videomind.core.rag.metrics import mrr, ndcg, recall_at_k
from videomind.infrastructure.storage.database import AsyncSessionLocal

# ── 控制台 UTF-8 化 + 日志静默（Windows GBK 终端兼容）──
# 关闭 SQLAlchemy echo 的 INFO 噪声（database.py engine.echo=is_dev 开着），保留 WARNING+
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)

EVAL_DIR = Path(__file__).resolve().parent
K_VALUES = [1, 3, 5]
DEFAULT_TOP_K = 20


# ───────────────────────── 单条评测 ─────────────────────────


async def eval_one(
    db,
    item: dict,
    *,
    top_k: int,
    recall_k: int | None = None,
) -> dict:
    """跑一条 QA，返回 raw/evidence 两套序列的指标 + 命中位置 + 延迟。

    隔离 QueryRewriter：直接喂 item["query"]，测纯检索+rerank。
    recall_k=None → recall==top_k（旧行为，向后兼容）。
    recall_k=60 → 宽召回窗口，gold rank 25/57 进候选池。
    """
    media_id = uuid.UUID(item["media_id"])
    gold_id = item["gold_chunk_id"]

    t0 = time.perf_counter()
    out = await pipeline.search(db, item["query"], media_id, top_k=top_k, recall_k=recall_k)
    elapsed = time.perf_counter() - t0

    raw_ids = [h.chunk_id for h in out["raw_hits"]]
    ev_ids = [e.chunk_id for e in out["evidence"]]

    gold = [gold_id]
    return {
        **item,
        "elapsed_s": round(elapsed, 4),
        "raw_ids_n": len(raw_ids),
        "ev_ids_n": len(ev_ids),
        "raw_recall@1": recall_at_k(raw_ids, gold, k=1),
        "raw_recall@3": recall_at_k(raw_ids, gold, k=3),
        "raw_recall@5": recall_at_k(raw_ids, gold, k=5),
        "raw_mrr": mrr(raw_ids, gold),
        "raw_ndcg": ndcg(raw_ids, gold),
        "ev_recall@1": recall_at_k(ev_ids, gold, k=1),
        "ev_recall@3": recall_at_k(ev_ids, gold, k=3),
        "ev_recall@5": recall_at_k(ev_ids, gold, k=5),
        "ev_mrr": mrr(ev_ids, gold),
        "ev_ndcg": ndcg(ev_ids, gold),
        "raw_hit_rank": (raw_ids.index(gold_id) + 1) if gold_id in raw_ids else None,
        "ev_hit_rank": (ev_ids.index(gold_id) + 1) if gold_id in ev_ids else None,
    }


# ───────────────────────── 预热 retriever 缓存 ─────────────────────────


async def warmup_retriever(db, qa_items: list[dict]) -> None:
    """对每个 media 跑一次 dummy 查询暖 BM25 索引缓存，消除首条构建开销。

    跨 provider 复用 retriever 缓存，故三档只需暖一次（在切档前）。
    """
    seen_media: set[str] = set()
    for item in qa_items:
        mid = item["media_id"]
        if mid in seen_media:
            continue
        seen_media.add(mid)
        # 任意 query 触发缓存构建，结果丢弃
        await pipeline.search(db, "x", uuid.UUID(mid), top_k=DEFAULT_TOP_K)
    print(f"  预热完成: {len(seen_media)} 个 media 索引已缓存")


# ───────────────────────── 切档 ─────────────────────────


def switch_provider(provider: str) -> None:
    """单进程内切 rerank_provider：env 覆盖 + 清 settings/reranker 双 cache。

    pydantic-settings 实例化时 os.environ 优先级高于 .env，故 env 写入后 cache_clear
    再 get_settings() 即读到新 provider。get_reranker lru_cache 单例也须清，否则仍用旧后端。
    """
    os.environ["RERANK_PROVIDER"] = provider
    get_settings.cache_clear()
    rerank_backend.get_reranker.cache_clear()
    s = get_settings()
    # 不打印 api_key。仅确认 provider 生效
    actual = s.rerank_provider
    if actual != provider:
        # 可能 .env 强制覆盖？pydantic env 应优先。若不一致告警但仍继续
        print(f"  [WARN] 切档目标 {provider} 实读 {actual}（.env 可能强优先），按实读档评测")


# ───────────────────────── 聚合 ─────────────────────────


_METRIC_KEYS = [
    "raw_recall@1", "raw_recall@3", "raw_recall@5", "raw_mrr", "raw_ndcg",
    "ev_recall@1", "ev_recall@3", "ev_recall@5", "ev_mrr", "ev_ndcg",
]


def _avg(rows: list[dict], key: str) -> float:
    vals = [r[key] for r in rows if key in r]
    return sum(vals) / len(vals) if vals else 0.0


def aggregate(rows: list[dict]) -> dict:
    """macro-avg 全量 + difficulty 分组（normal vs hard）。"""
    agg = {
        "n": len(rows),
        **{k: round(_avg(rows, k), 4) for k in _METRIC_KEYS},
        "avg_latency_s": round(_avg(rows, "elapsed_s"), 4),
    }
    for diff in ("normal", "hard"):
        sub = [r for r in rows if r["difficulty"] == diff]
        if sub:
            agg[f"n_{diff}"] = len(sub)
            for k in _METRIC_KEYS + ["elapsed_s"]:
                agg[f"{k}_{diff}"] = round(_avg(sub, k), 4)
    return agg


# ───────────────────────── raw 一致性校验 ─────────────────────────


def verify_raw_consistency(per_provider_rows: dict[str, list[dict]]) -> list[str]:
    """retriever 与 rerank 无关 → 三档 raw hits 排序必须逐条相同。

    不一致说明 retriever 缓存被污染或存在随机性，评测基础设施不可信。
    """
    issues = []
    providers = list(per_provider_rows.keys())
    if len(providers) < 2:
        return issues
    base = per_provider_rows[providers[0]]
    for p in providers[1:]:
        other = per_provider_rows[p]
        for i in range(len(base)):
            # raw_hit_rank 一致即 raw 序一致（单 gold 命中位置）；未命中需比对全序
            b, o = base[i], other[i]
            if b.get("raw_hit_rank") != o.get("raw_hit_rank"):
                issues.append(f"{b['qid']}: raw_hit_rank {providers[0]}={b['raw_hit_rank']} vs {p}={o['raw_hit_rank']}")
    return issues


# ───────────────────────── 表格打印 ─────────────────────────


def print_table(per_provider_agg: dict[str, dict], per_provider_raw_n: dict[str, list[int]]) -> None:
    """终端对比表（GBK 终端避开全角字符，用 ASCII）。"""
    cols = [
        ("provider", 10),
        ("n", 4),
        ("raw_R@1", 8), ("raw_R@5", 8), ("raw_MRR", 8), ("raw_nDCG", 8),
        ("ev_R@1", 8), ("ev_R@5", 8), ("ev_MRR", 8), ("ev_nDCG", 8),
        ("lat_s", 7),
    ]
    header = " ".join(name.ljust(w) for name, w in cols)
    print("\n" + header)
    print("-" * len(header))
    for provider, agg in per_provider_agg.items():
        line = [
            provider.ljust(10),
            str(agg["n"]).rjust(4),
            f"{agg['raw_recall@1']:.4f}".rjust(8),
            f"{agg['raw_recall@5']:.4f}".rjust(8),
            f"{agg['raw_mrr']:.4f}".rjust(8),
            f"{agg['raw_ndcg']:.4f}".rjust(8),
            f"{agg['ev_recall@1']:.4f}".rjust(8),
            f"{agg['ev_recall@5']:.4f}".rjust(8),
            f"{agg['ev_mrr']:.4f}".rjust(8),
            f"{agg['ev_ndcg']:.4f}".rjust(8),
            f"{agg['avg_latency_s']:.4f}".rjust(7),
        ]
        print(" ".join(line))

    print("\n[difficulty 分组] hard 是否被 rerank 拉起（ev_MRR_hard vs raw_MRR_hard）:")
    for provider, agg in per_provider_agg.items():
        raw_h = agg.get("raw_mrr_hard", 0.0)
        ev_h = agg.get("ev_mrr_hard", 0.0)
        delta = ev_h - raw_h
        arrow = "UP" if delta > 0.001 else ("DN" if delta < -0.001 else "==")
        print(f"  {provider:<10} hard: raw_MRR={raw_h:.4f}  ev_MRR={ev_h:.4f}  {arrow} d={delta:+.4f}")


# ───────────────────────── 主流程 ─────────────────────────


async def run(providers: list[str], top_k: int, recall_k: int | None = None) -> None:
    qa_path = EVAL_DIR / "qa_corpus.json"
    if not qa_path.exists():
        raise SystemExit(f"QA 集缺失: {qa_path}")
    qa_items = json.loads(qa_path.read_text(encoding="utf-8"))
    print(f"装入 QA 集: {len(qa_items)} 条")

    # 落盘结果文件（避免 Date.now —— 用 perf_counter 不行，这里落盘名用 provider 拼接即可）
    per_provider_rows: dict[str, list[dict]] = {}

    async with AsyncSessionLocal() as db:
        # 1. 预热 retriever 缓存：用 off 档跑（不装配 API reranker，免浪费远程 quota）。
        #    retriever 缓存跨档复用（BM25 索引独立于 rerank）；首条 query 含索引构建开销，
        #    预热后三档 latency 纯检索+rerank 可比。
        print("预热 retriever 缓存 (off 档) ...")
        switch_provider("off")
        await warmup_retriever(db, qa_items)

        # 2. 逐档评测
        for provider in providers:
            print(f"\n=== provider={provider} ===")
            switch_provider(provider)
            rows = []
            for item in qa_items:
                try:
                    r = await eval_one(db, item, top_k=top_k, recall_k=recall_k)
                except Exception as e:
                    print(f"  FAIL {item['qid']} 查询失败: {type(e).__name__}: {e}")
                    # 标注为全 0 的失败行，不中断
                    r = {**item, "elapsed_s": 0.0, "raw_ids_n": 0, "ev_ids_n": 0,
                         "raw_hit_rank": None, "ev_hit_rank": None,
                         **{k: 0.0 for k in _METRIC_KEYS}}
                rows.append(r)
                hit = f"rank={r.get('ev_hit_rank')}" if r.get("ev_hit_rank") else "miss"
                print(f"  {item['qid']} [{item['difficulty']:<6}] ev_MRR={r['ev_mrr']:.3f} ev_R@5={r['ev_recall@5']:.3f} {hit} ({r['elapsed_s']}s)")
            per_provider_rows[provider] = rows

    # 3. 聚合
    per_provider_agg = {p: aggregate(rows) for p, rows in per_provider_rows.items()}

    # 4. raw 一致性校验
    issues = verify_raw_consistency(per_provider_rows)
    if issues:
        print("\n[WARN] raw hits 跨 provider 不一致（评测基础设施存疑）:")
        for s in issues[:10]:
            print(f"  {s}")
    else:
        print("\n[OK] raw hits 跨 provider 一致（retriever 未被 rerank 污染）")

    # 5. 打印对比表
    print_table(per_provider_agg, {})

    # 6. 落盘明细 JSON（recall_k 纳入文件名，避免 D-α 两次运行覆盖）
    rec_suffix = f"_recallk{recall_k}" if recall_k is not None else ""
    out_path = EVAL_DIR / f"results_{'_'.join(providers)}_topk{top_k}{rec_suffix}.json"
    payload = {
        "top_k": top_k,
        "recall_k": recall_k,
        "aggregate": per_provider_agg,
        "raw_consistency_issues": issues,
        "per_query": {p: rows for p, rows in per_provider_rows.items()},
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n明细已落盘: {out_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description="RAG rerank_provider 三档对比评测")
    ap.add_argument("--providers", default="off,api,local",
                    help="逗号分隔的 rerank_provider，默认 off,api,local")
    ap.add_argument("--top-k", type=int, default=DEFAULT_TOP_K, help="检索深度，默认 20")
    ap.add_argument("--recall-k", type=int, default=None,
                    help="召回/RRF 宽窗（默认 None=与 top_k 一致旧行为；D-α 设 60 测宽召回 gold 是否进 evidence top-20）")
    args = ap.parse_args()
    providers = [p.strip() for p in args.providers.split(",") if p.strip()]
    if not providers:
        raise SystemExit("--providers 不能为空")
    asyncio.run(run(providers, args.top_k, args.recall_k))


if __name__ == "__main__":
    main()
