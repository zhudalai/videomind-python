# 检索深度调优 Design（D 阶段）

- 日期：2026-08-04
- 状态：待实施
- 上游：P2-4 QueryRewriter 评测 + 通道级诊断（`scripts/eval/diag_retrieval.py` / `eval_queryrewriter.py`）
- 下游：本 spec 批准后转 writing-plans 出 D-α / D-β 实施计划；D 质量数据决定是否启动 B（本地 rerank 上 GPU）

## 1. 背景

P2-4 通道级诊断发现两条 hard query 的 gold 并非"完全检索失败"，而是被检索深度截断：

| qid | 来源 | gold RRF rank | 命中通道 |
|---|---|---|---|
| ja_002 | diag_retrieval.py (top_k=100) | 25 | Vector rank=31, BM25 rank=84, RRF rank=25 |
| ja_004 | diag_retrieval.py (top_k=100) | 57 | Vector rank=26, BM25 MISS, RRF rank=57 |

而三层 top_k 的真实值：

- 生产 `executor.py` `TOP_K_PER_VIDEO=5`（gold rank 25/57 连召回都进不来）
- `run_eval.py` `top_k=20`（只覆盖 rank≤20 → ev_hit_rank=None）
- `diag_retrieval.py` `top_k=100`（才看到 gold 在 25/57）

**纯"5→20"救不到任何 hard**（25、57 都 > 20）。要让 gold 进候选，召回深度需 ≥57。

另一隐藏杀手：`rerank_backend.py` 的 api rerank `top_n=min(rerank_top_n=20, len(hits))` 对宽召回只返回 cross-encoder top 20；gold 之所以 RRF rank 偏后正因为弱相关（抽象 query），cross-encoder 相关性很可能排不进 top 20 —— 宽召回把 gold 拉进池，rerank 又踢出去。

## 2. 目标与非目标

**目标**

- 让 rank 25/57 的 hard query gold 真正进入候选池与 LLM 上下文
- 各改动可独立归因（单变量实验）
- 保持向后兼容（不破坏 run_eval baseline、不破坏现有测试）

**非目标**

- 不重索引、不换 embedding 模型、不装 CUDA、不写新检索算法
- 不动 embedding/rerank 的 provider/device（属于 B 阶段）

## 3. 分层检索架构

`pipeline.search` 的 `top_k` 当前一口锅盛两件事：召回深度 + 喂下游条数。拆为：

```python
async def search(
    db, query, media_id, *,
    top_k: int = 20,        # 喂下游最终条数（rerank+expand 后截断）
    recall_k: int | None = None,   # 召回/RRF 宽窗（新参，None 时退化到 top_k）
    intent_path=None,
):
    recall = recall_k if recall_k is not None else top_k
    raw_hits = await retriever.search(query, top_k=recall)   # 宽召回
    reranked = await reranker.rerank(query, [replace(h) for h in raw_hits])
    expanded = await expander.expand(db, media_id, reranked)
    expanded_top = expanded[:top_k]                          # 窄喂下游
    ...
```

- `recall_k=None` → `recall == top_k`，行为全同旧 → **run_eval 不传 recall_k 即向后兼容**
- `_RagRetriever.search` 加 `recall_k` 透传给 `pipeline.search`
- `executor.py` 拆两个常量：

```python
RECALL_TOP_K = 60          # 召回/RRF 深度（覆盖 gold rank 25 与 57；ja_004 余量仅 3，边界风险见 §8）
LLM_CONTEXT_TOP_K = 12     # rerank 精排后喂 LLM 条数
# 调用：self._retriever.search(query, mid_uuid, top_k=LLM_CONTEXT_TOP_K, recall_k=RECALL_TOP_K)
```

喂 LLM context 量级估算（N=3 task × M=2 media × ≈330 token/chunk）：

- 现状 3×2×5 = 10k token
- 分层后 3×2×12 = 24k token ✓ 可控

## 4. rerank_top_n 跟随 recall

`.env`：`RERANK_TOP_N=20 → 60`。让 api rerank 精排全部 60 条 raw_hits（`top_n=min(60, 60)=60`），避免宽召回进的 gold 被 rerank top-20 截掉。这是 D-α 真能测出召回收益的前提，也是 D→B 决策的诚实基础。

> 注：这正是 D 与 B 强耦合的证据——宽召回配不截断 rerank 才真正救 hard gold。B 用 local rerank（天然不截断 + GPU 加速）；D-α 靠临时调大 `RERANK_TOP_N` 模拟"不截断"以测召回上限。

## 5. D-α / D-β 两步独立实验

按 memory 警示（QueryRewriter 前测恶化过 zh_001，多变量混算无法归因），拆两步单变量。

### D-α：召回水位分层（单变量）

- 改动：第 3 节（recall_k=60, top_k=12, recall_k 透传）+ 第 4 节（`RERANK_TOP_N=60`）
- eval：`run_eval.py --providers off,api --recall-k 60 --top-k 20`，对照 P2-4 基线（api: raw_MRR_hard=0.0303, ev_MRR_hard=0.3333），并附测 `--top-k 12` 档（模拟 executor 生产喂 LLM 的真实口径）
- 双口径理由：executor 生产喂 LLM 用 `LLM_CONTEXT_TOP_K=12`，eval 用 20 才与 P2-4 基线可比、用 12 才反映生产 hard gold 是否真进 LLM 上下文。单测一档会误判（只测 20 会高估生产命中率、只测 12 丢了与基线可比性）
- 成功判据：
  - `ev_recall@5_hard` 与 `ev_mrr_hard`（top_k=20 档）较基线提升
  - ja_002/ja_004 进 evidence top-20（ev_hit_rank 不再 None）
- 防退化判据：normal query macro MRR 不降

### D-β：QueryRewriter 阈值（独立单变量）

- 改动：`rewriter.py` `QueryRewriter.__init__` 默认 `confidence_threshold=0.7 → 0.5`，让 LLM 改写真生效、不再 fallback rule（先硬改验证，验证后 env 化）。**不依赖 D-α**，可独立 git commit / 独立 eval。
- eval：复用 `eval_queryrewriter.py` 复现 P2-4 对比 single vs multi-query
- **防恶化锚**：zh_001 P2-4 实测 baseline rank=52 → multi-query rank=136（恶化）。若 D-β 后 zh_001 multi-query 仍恶化 → **D-β 不上 / 回滚阈值**。先验后定，不默认启用。

## 6. 改动文件清单

| 文件 | 改动 | 实验 |
|---|---|---|
| `src/videomind/core/rag/pipeline.py` | `search()` 加 `recall_k` 可选参数 | D-α |
| `src/videomind/core/agent_loop/executor.py` | 拆 `RECALL_TOP_K=60` / `LLM_CONTEXT_TOP_K=12`，调用传 `recall_k` | D-α |
| `_RagRetriever.search`（executor.py 内） | 加 `recall_k` 透传 | D-α |
| `.env` | `RERANK_TOP_N=20→60`（同步 `.env.example`） | D-α |
| `scripts/eval/run_eval.py` | 加 `--recall-k` 参数透传给 `eval_one` / `pipeline.search` | D-α |
| `src/videomind/core/intent/rewriter.py` | `confidence_threshold` 0.7→0.5 | D-β |

## 7. 评测方案

### run_eval.py 扩展

- 新增 `--recall-k` 参数（默认 None = 旧行为，向后兼容）
- `--recall-k 60`：宽召回测 evidence 质量上限（看 hard gold 是否进 top-20 evidence）
- `--top-k 20` 保持（喂下游条数维持 20 与 P2-4 基线可比；executor 生产用 12 是另一个口径，eval 用 20 看 raw 召回是否进 evidence）
- raw 一致性校验照旧（不依赖 rerank）

### eval_queryrewriter.py 复用

- 直接跑（已有脚本），对比 D-β 前后 zh_001 multi-query rank
- D-β 验证用 LLM 真改写是否生成 2-4 个语义不同的 sub_queries（D-β 前清一色 `method=rule` 无效）

## 8. 不变式与风险

**不变式（沿用 [[rag-pipeline-rerank-expand-order]]）**

- `recall_k=None` 退化到旧行为 → 不破坏 run_eval / 现有测试
- raw_hits 保真不被 rerank 污染（双保险：pipeline 已 `replace(h)`，各 rerank 后端返回新列表）
- min-max 归一只在真实命中（score>0）做，邻居 score=0 保持 0
- api rerank 失败仍降 DeterministicRerankerAdapter（pipeline try/except 不变）

**风险**

- `RECALL_TOP_K=60` 对 ja_004 gold rank=57 余量仅 3：RRF 排序有微小波动时 gold 可能飘出 top-60 → 误判"召回水位无效"（D 自身的 false negative 风险）。若 D-α eval 显示 ja_004 仍飘忽，上调 `RECALL_TOP_K` 与 `RERANK_TOP_N` 至 70 重测一次再下结论
- api rerank 精排 60 条（query-doc pair）较 20 条慢、费增加 —— 这是 D-α 的探测代价，预期可接受单次评测
- D-α 若证召回水位无效（gold 即便进 60 池且不被 rerank 截断仍排不进 evidence top-20），则要走到 B（local rerank 不截断 + GPU）—— 这正是"D 根据质量决定是否 B"的预期分支
- D-β zh_001 恶化则回滚，D-β 不上

## 9. D → B 决策点

D-α + D-β eval 完成后，看：

1. 若 `ev_recall@5_hard` 拉升且 ja_002/ja_004 进 evidence top-20 → 召回水位是瓶颈、B 不必为质量做（B 仅延迟收益）
2. 若即便宽召回 + rerank_top_n=60 仍 ranks 不进 → 需更宽/更强 reranker → 启 B（local rerank 不截断 + GPU，质量上限 + 延迟双赢）
3. 中间态：D 有提升但仍不足 → B + D 叠加
