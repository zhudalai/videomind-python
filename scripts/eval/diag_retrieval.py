"""检索通道级诊断 —— 对指定 QA 逐通道 (BM25 / Vector / RRF 融合) 检查 gold 命中情况。

排查思路：
  P2-2c eval 发现 ja_002/ja_004 hard 查询 gold 从未进入 top-20 候选池（raw_hit_rank=None）。
  这是 retrieval 侧问题，不是 rerank 侧。本脚本用同一 retriever 实例分别检查：
    - BM25 通道（jieba+2gram）：纯字面/子串匹配
    - Vector 通道（BGE-M3 Qdrant）：语义嵌入检索
    - RRF 融合结果：两通道 RRF 融合后黄金是否出现
  定位是单通道 miss 还是双通道 miss，为后续解决提供依据。

用法：
    python scripts/eval/diag_retrieval.py
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
TOP_K = 100  # 拉大窗口看 gold 到底在哪

# 关注的 hard queries（raw_hit_rank=None 的条目）
TARGET_QIDS = {"ja_002", "ja_004"}


async def diagnose() -> None:
    qa = json.loads((EVAL_DIR / "qa_corpus.json").read_text("utf-8"))
    gold = json.loads((EVAL_DIR / "gold_chunks.json").read_text("utf-8"))

    async with AsyncSessionLocal() as db:
        for item in qa:
            if item["qid"] not in TARGET_QIDS:
                continue

            g = gold[item["gold_chunk_id"]]
            mid_uuid = uuid.UUID(item["media_id"])
            gold_hash = item["gold_chunk_id"]

            # 通过 pipeline._get_retriever 取缓存/新建 retriever
            from videomind.core.rag.pipeline import _get_retriever

            retriever = await _get_retriever(db, mid_uuid)

            # ── 1. Vector 通道 ──
            v_hits = await retriever._vector.retrieve(
                item["query"], mid_uuid, top_k=TOP_K
            )
            v_rank = next(
                (i + 1 for i, h in enumerate(v_hits) if h.chunk_id == gold_hash),
                None,
            )

            # ── 2. BM25 通道 ──
            bm_results = retriever._bm25.search(item["query"], top_k=TOP_K)
            bm_rank = next(
                (i + 1 for i, (cid, _) in enumerate(bm_results) if str(cid) == gold_hash),
                None,
            )

            # ── 3. RRF 融合（retriever.search 内部逻辑的等效再现） ──
            raw_hits: list[dict] = []
            try:
                # 调用完整 search 链路（从 pipeline 走会有 cached retriever，结果一致）
                from videomind.core.rag import pipeline as rag_pipeline

                out = await rag_pipeline.search(
                    db, item["query"], mid_uuid, top_k=TOP_K
                )
                raw_hits = [{"chunk_id": h.chunk_id, "score": h.score} for h in out["raw_hits"]]
            except Exception as e:
                raw_hits = [{"error": str(e)}]

            rrf_rank = next(
                (i + 1 for i, h in enumerate(raw_hits) if h.get("chunk_id") == gold_hash),
                None,
            )

            # ── 报告 ──
            ch = g["content"]
            print(f"\n{'='*60}")
            print(f"{item['qid']}: {item['query']}")
            print(f"{'='*60}")
            print(f"  gold chunk index={g['chunk_index']}  len={len(ch)} chars")
            print(f"  gold snippet: {ch[:100]}")
            print(f"  ---")
            print(f"  Vector top-{TOP_K} rank: {v_rank or 'MISS'}")
            print(f"  BM25   top-{TOP_K} rank: {bm_rank or 'MISS'}")
            print(f"  RRF    top-{TOP_K} rank: {rrf_rank or 'MISS'}")
            print(f"  ---")
            if v_rank:
                v_top = [(h.chunk_id[:8], round(h.score, 4)) for h in v_hits[:5]]
                print(f"  Vector top-5: {v_top}")
            if bm_rank:
                bm_top = [(str(cid)[:8], round(s, 4)) for cid, s in bm_results[:5]]
                print(f"  BM25   top-5: {bm_top}")

            # 如果 miss，看 RRF 融合后的 top-N 中有没有排到 gold 的邻近 chunk
            if not rrf_rank and len(raw_hits) > 10:
                top_20_ids = [h["chunk_id"][:8] for h in raw_hits[:20]]
                print(f"  RRF    top-20 chunk_ids: {top_20_ids}")

            # 额外提示
            if v_rank is None and bm_rank is None:
                print(f"  [WARN] 双通道均未命中！gold 完全不在候选池 → rerank 无法补救")
            elif v_rank and bm_rank is None:
                print(f"  [INFO] 仅 Vector 命中（rank={v_rank}），BM25 未命中")
            elif bm_rank and v_rank is None:
                print(f"  [INFO] 仅 BM25 命中（rank={bm_rank}），Vector 未命中")


def main() -> None:
    asyncio.run(diagnose())


if __name__ == "__main__":
    main()