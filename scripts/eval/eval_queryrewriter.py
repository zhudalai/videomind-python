"""QueryRewriter 端到端评测 —— 子查询能否拉升 hard query 的命中深度。

背景：
  diag_retrieval.py 发现 ja_002/ja_004 的 gold 其实在 RRF 融合池中（RRF rank=25 / 57），
  只是超出默认 top_k=20 窗口被截掉。QueryRewriter 生成 2-4 个子查询，可能从不同角度
  命中 gold，提升其融合排名的上限。

流程：
  1. 对每条 QA 调用 QueryRewriter 生成 sub_queries
  2. 原 query + 每个 sub_query 分别跑 pipeline.search（top_k=100）
  3. 合并各查询的 raw_hits，按 chunk_id dedup 取最高 RRF score
  4. 对比单查询 vs 多查询的 gold rank（多查询合并后重排序）
  5. 报告 hard query 改善情况
  6. 结果写入 scripts/eval/results_queryrewriter.json

用法：
    python scripts/eval/eval_queryrewriter.py
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path

from videomind.infrastructure.storage.database import AsyncSessionLocal

logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

EVAL_DIR = Path(__file__).resolve().parent
TOP_K = 100
REWRITE_TIMEOUT = 15


async def evaluate() -> None:
    qa = json.loads((EVAL_DIR / "qa_corpus.json").read_text("utf-8"))

    print("=" * 60)
    print("QueryRewriter evaluation (top_k=%d)" % TOP_K)
    print("=" * 60)

    results: list[dict] = []

    async with AsyncSessionLocal() as db:
        for item in qa:
            mid_uuid = uuid.UUID(item["media_id"])
            gold_id = item["gold_chunk_id"]
            qid = item["qid"]

            # 1. single query baseline
            from videomind.core.rag import pipeline as rag_pipeline

            base = await rag_pipeline.search(db, item["query"], mid_uuid, top_k=TOP_K)
            base_rank = next(
                (i + 1 for i, h in enumerate(base["raw_hits"]) if h.chunk_id == gold_id),
                None,
            )

            # 2. QueryRewriter
            sub_queries: list[str] = [item["query"]]
            q_method = "none"
            try:
                from videomind.core.intent.pipeline import get_intent_service
                from videomind.core.intent.types import RewriteContext
                from videomind.infrastructure.storage import models as m

                svc = get_intent_service()
                media = await db.get(m.MediaFile, mid_uuid)
                title = (media.meta_json or {}).get("title") or media.filename if media else ""
                duration_sec = (media.duration_ms // 1000) if media and media.duration_ms else 0

                rewritten = await asyncio.wait_for(
                    svc.rewriter.rewrite(
                        item["query"],
                        RewriteContext(video_title=title, duration_sec=duration_sec),
                        db=db,
                    ),
                    timeout=REWRITE_TIMEOUT,
                )
                if rewritten.sub_queries:
                    sub_queries = rewritten.sub_queries
                    q_method = rewritten.method
            except Exception as e:
                q_method = "err:%s" % type(e).__name__

            # 3. multi-query merge
            dedup: dict[str, dict] = {}
            for sq in sub_queries:
                out = await rag_pipeline.search(db, sq, mid_uuid, top_k=TOP_K)
                for h in out["raw_hits"]:
                    prev = dedup.get(h.chunk_id)
                    if prev is None or h.score > prev["rrf_score"]:
                        dedup[h.chunk_id] = {
                            "chunk_id": h.chunk_id,
                            "rrf_score": h.score,
                        }

            merged = sorted(dedup.values(), key=lambda x: x["rrf_score"], reverse=True)
            multi_rank = next(
                (i + 1 for i, c in enumerate(merged) if c["chunk_id"] == gold_id),
                None,
            )

            # 4. report and accumulate
            diff = item["difficulty"]
            arrow = "=="
            if multi_rank and base_rank:
                arrow = "UP" if multi_rank < base_rank else ("DN" if multi_rank > base_rank else "==")
            elif multi_rank and not base_rank:
                arrow = "RECOVERED"

            print()
            print("  %s [%s] %s" % (qid, diff, item["language"]))
            print("    query: %s" % item["query"][:80])
            print("    sub_queries (%s): n=%d" % (q_method, len(sub_queries)))
            for i, sq in enumerate(sub_queries):
                print("      [%d] %s" % (i, sq[:80]))
            print("    baseline rank: %s" % (base_rank if base_rank else "MISS"))
            print("    multi-query rank: %s  %s" % (multi_rank if multi_rank else "MISS", arrow))
            if base_rank and base_rank > 20:
                print("    baseline outside top-20 by %d" % (base_rank - 20))
            if multi_rank and multi_rank > 20:
                print("    multi-query outside top-20 by %d" % (multi_rank - 20))
            if multi_rank and multi_rank <= 20:
                print("    [OK] gold in top-20 window (was #%s)" % (base_rank if base_rank else "MISS"))

            results.append({
                "qid": qid,
                "difficulty": diff,
                "language": item["language"],
                "method": q_method,
                "sub_queries": sub_queries,
                "baseline_rank": base_rank,
                "multi_query_rank": multi_rank,
                "improvement": arrow,
            })

    # write results
    out_path = EVAL_DIR / "results_queryrewriter.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\nresults written to: %s" % out_path)


def main() -> None:
    asyncio.run(evaluate())


if __name__ == "__main__":
    main()