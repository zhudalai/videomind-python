# 项目实现与计划对照 / Decision Log
# プロジェクト実装と計画の対照 / Implementation vs. Plan & Decision Log

> 本文档回答两个问题：
> 1. **实际实现 vs `docs/` 中设计计划的区别** — 见 §2 各子系统对照表
> 2. **修改的原因与决策过程** — 见 §3 决策史（D-α / D-β / P2-1 / P2-4 / P2-5 及其他修复）
>
> この文書は 2 つの問いに答えます：
> 1. 実際の実装と `docs/` 設計計画との違い — §2 の各サブシステム対照表を参照
> 2. 変更の理由と意思決定プロセス — §3 意思決定履歴を参照
>
> - 决策命名体系：`D-α` 召回深度分层、`D-β` rewriter 阈值、`P2-1` cross-encoder rerank opt-in、`P2-4` 通道级诊断、`P2-5` 设计阶段
> - 权威来源：代码 docstring、commit message、`docs/superpowers/specs/`、评测 JSON、`~/.claude/projects/.../memory/`
> - 最后更新：2026-08-06
> - 関連ドキュメント：[ARCHITECTURE.md](ARCHITECTURE.md) · [RAG-RETRIEVAL.md](RAG-RETRIEVAL.md) · [AGENT-LOOP.md](AGENT-LOOP.md) · [INTENT-ROUTING.md](INTENT-ROUTING.md) · [MODEL-GATEWAY.md](MODEL-GATEWAY.md)

---

## 1. 背景与目的 / Background

`docs/` 下的设计文档（RAG-RETRIEVAL.md / AGENT-LOOP.md / INTENT-ROUTING.md / MODEL-GATEWAY.md）刻画了系统的**理想形态**：三通道检索、CrossEncoder 融合权重、证据时间戳+jieba 相似度校验、MCP 工具调用、Checkpoint 双层存储等。但在后续的检索质量调优（D 阶段）与跨视频分析迭代中，实现落地了若干**偏离设计**的取舍：分层召回、顺序修复、cross-encoder 纯分重排、集合防幻觉、并发检索等。

这些取舍不是随意而为 —— 每一条都对应一次**单变量实验 + 评测证据 + 显式决策**。本文把"计划说了什么 → 实际做成什么 → 为什么这样改 → 用什么证据拍板 → 现在代码在哪"逐条钉死，避免日后"代码与文档各说各话"。

设计文档里描画但**尚未落地**的项统一收口在 §4。

---

## 2. 计划 vs 实际对照 / Plan vs. Implementation

### 2.1 RAG 检索管线（RAG-RETRIEVAL.md §2 + INTENT-ROUTING.md §5–7）

| 维度 | 计划（docs） | 实际（代码） | 差异性质 |
|---|---|---|---|
| 编排入口 | `MultiChannelRetrieval` 聚合 Vector + BM25 + **SQL** 三通道 | `HybridRetriever` 仅 Vector + BM25 **双通道** | SQL 通道未落地（见 §4） |
| RRF 常数 K | 60 | 60 | 一致 |
| 召回 / 喂下游深度 | 通道 `top_k=50`，`final_top_k=20` 一口锅 | 召回 `recall_k=60` 与喂下游 `top_k=12` **分层**（`recall_k=None` 退化为旧行为） | ★ D-α（§3.1） |
| expand 顺序 | 总览图中 `ContextExpander` 在 `Rerank` **之前** | `search → rerank → expand`（rerank 先于 expand） | ★ 顺序修复（§3.6） |
| expand 邻居分数 | `score * 0.8` 降权 | `score=0.0` 置尾，**不参与 rerank** | ★ 顺序修复（§3.6） |
| 重排后端 | `DeterministicReranker`（固定权重）+ 可选 `CrossEncoder`（融合 `0.7*det + 0.3*ce`） | `rerank_provider` 三档 `off / api / local`，**纯 cross-encoder 分**，不做融合 | ★ P2-1（§3.3） |
| Deterministic 权重 | 时间新近 0.2 / 来源权威 0.15 / 内容完整性 0.15 / 意图匹配 0.2 | `position 0.3 + source 0.2 + original 0.5` 三阶段线性加权 | 简化权重 |
| cross-encoder 融合 | `rerank_score = 0.7*det + 0.3*ce` | 纯 `relevance_score`，跨 query/media **min-max 归一** | ★ P2-1 改设计（§3.3） |
| api rerank `top_n` | `crossencoder_top_k=10` | `RERANK_TOP_N=60` **跟随 recall**（`top_n=min(60, len)`） | ★ D-α（§3.1） |
| BM25 分词 | `jieba.lcut` | `jieba + 2-gram` | ★ CJK 修复（§3.7） |
| 后处理器链 | `Deduplication / ThresholdFilter(0.3) / DiversitySampling` | 无显式后处理链 | 未实现（见 §4） |
| `HybridRetriever` 缓存 | 每次检索 `select(Chunk)` 重建 BM25 | 进程内 `_RETRIEVER_CACHE` 按 media_id 单例，无 TTL | 性能优化 |
| `.env` rerank_provider | — | `.env` 实开 `api`；`config.py`/`.env.example` 默认 `off` | 运行配置 |

### 2.2 AgentLoop（AGENT-LOOP.md）

| 维度 | 计划（docs） | 实际（代码） | 差异性质 |
|---|---|---|---|
| 最大轮数 | `MAX_ROUNDS = 2`（固定常量） | `loop.run(..., max_rounds=2)` 默认；API/前端 `le=3` 可上调 | ★ 910695a（§3.5） |
| Planner 子任务数 | 1–5（`tasks[:5]`） | `MAX_TASKS = 5` | 一致 |
| Executor 喂 LLM 条数 | `top_k=10`（单视频） | `LLM_CONTEXT_TOP_K=12`，召回 `recall_k=60` | ★ D-α（§3.1） |
| Executor 检索方式 | `retrieve_by_time_range` 或 `retrieve(top_k=10)` 单视频 | `rag_search` **跨 M 视频 `asyncio.gather` 并发** | ★ 多视频 + 并发（§3.2） |
| `media_ids` | 单视频 `ctx.media_id` | `state.media_ids` 列表，前端 `max_length=4` | ★ 910695a（§3.5） |
| 证据硬校验 | `EvidenceVerifier`：时间戳范围 + **jieba 内容相似度 0.7** + source 分流（asr/ocr/frame） | 纯函数：时间戳范围 + **content 非空** | ★ 简化（§3.8） |
| 防幻觉机制 | Critic 合并 `EvidenceVerifier.verify_all` | `conclusion.evidence_ids ⊆ retrieved_evidence_ids` **集合包含**校验 | ★ 改设计（§3.8） |
| Checkpoint 存储 | PostgreSQL 真源 + **Redis 热缓存**，`resume_analysis` 恢复入口 | `_checkpoint` 直接写 PostgreSQL（`AgentCheckpoint` 表）；Redis 热缓存基础设施已建于 `infrastructure/cache/redis.py` | 部分落地（§4） |
| `_run_agent_loop` 整合 | docs 由 `AgentLoop.run` 内部自管 checkpoint | 生产路径在 `routes/agent.py` 编排，**单 DB session 贯穿全轮**，逐阶段落 checkpoint | 实现注记 |

### 2.3 意图路由与查询改写（INTENT-ROUTING.md）

| 维度 | 计划（docs） | 实际（代码） | 差异性质 |
|---|---|---|---|
| 意图树来源 | `config/intent_tree.yaml` 外部文件 | 代码内 `DEFAULT_INTENT_TREE` 常量 | 实现选择（少一个文件依赖） |
| 路由融合权重 | kw 0.4 + LLM 0.6 | `kw_weight=0.4`（kw 0.4 + LLM 0.6） | 一致 |
| 通道配额基数 | 60 | 60 | 一致 |
| 通道权重表 | `intent_channel_weights`（yaml） | `DEFAULT_CHANNEL_WEIGHTS`（代码常量） | 一致 |
| QueryRewriter `confidence_threshold` | 0.7（`intent_routing.yaml`） | 0.7（D-β 回滚后定值） | ★ D-β（§3.4） |
| QueryRewriter `thinking/reasoning` | `thinking=False` | `reasoning=False`（关思维链） | ★ 66a1864（§3.9） |
| QueryRewriter `db` 透传 | 未提 | `db` 透传给 `RoutingLLMService.chat` 做计费入库 | ★ d4692e1（§3.9） |
| MCP 工具调用 | §8 设计 `MCPToolRegistry` + `VideoSegmentTool` + `VideoSearchTool` | **未实现** | 未实现（§4） |

### 2.4 模型网关（MODEL-GATEWAY.md）

| 维度 | 计划（docs） | 实际（代码） | 差异性质 |
|---|---|---|---|
| 三态熔断 | CLOSED → OPEN → HALF_OPEN | 同 | 一致 |
| `ChatRequest.reasoning` 透传 | — | 透传：`reasoning` → `body {"reasoning":{"enabled":...}}` | ★ 66a1864（§3.9） |
| 熔断失败阈值 | — | `failure_threshold=3` | 一致 |
| `llm_first_packet_timeout_s` | — | 配置存在（10.0），当前未被主路径消费 | 见 §4 |

---

## 3. 决策史 / Decision Log

每条决策含：**背景 → 决策 → 评测证据 → 最终状态 → 代码定位**。带 ★ 的为偏离设计计划的关键决策。

### 3.1 D-α：召回深度分层　★★

**背景** — P2-4 通道级诊断（`diag_retrieval.py`，`top_k=100`）发现两条 hard query 的 gold 并非"检索失败"，而是被**检索深度截断**：

| qid | gold RRF rank | 命中通道 |
|---|---|---|
| ja_002 | 25 | Vector 31 / BM25 84 / RRF 25 |
| ja_004 | 57 | Vector 26 / BM25 MISS / RRF 57 |

三层 `top_k` 真实值不一：生产 `executor.py TOP_K_PER_VIDEO=5`（rank 25/57 连召回都进不来）、`run_eval.py top_k=20`（只覆盖 rank≤20）、`diag top_k=100`（才看到 gold）。**纯"5→20"救不到任何 hard**（25、57 都 > 20）。还有隐藏杀手：api rerank `top_n=min(rerank_top_n=20, len)` 对宽召回只返回 cross-encoder top 20 —— gold 因弱相关排不进 top 20，宽召回把它拉进池、rerank 又踢出去。

**决策** — 把 `pipeline.search` 的 `top_k`（一口锅盛召回深度 + 喂下游条数）**拆成两个参数**：
- `recall_k`：召回/RRF 宽窗（新参，`None` 退化到 `top_k`，向后兼容）
- `top_k`：rerank+expand 后喂下游最终条数

`executor.py` 拆两个常量：`RECALL_TOP_K=60`（覆盖 gold rank 25 与 57）、`LLM_CONTEXT_TOP_K=12`。同时 `RERANK_TOP_N 20→60` 让 api rerank 精排全部 60 条，避免宽召回进的 gold 被 rerank top-20 截掉。

**评测证据** — `run_eval.py --providers off,api --recall-k 60 --top-k 20`，对照 P2-4 基线（`api: raw_MRR_hard=0.0303, ev_MRR_hard=0.3333`）。设计文档 §5 成功判据：`ev_recall@5_hard` 与 `ev_mrr_hard`（top_k=20 档）较基线提升，ja_002/ja_004 进 evidence top-20（`ev_hit_rank` 不再 None）；防退化判据：normal query macro MRR 不降。双口径理由：executor 生产喂 LLM 用 12、eval 用 20 与 P2-4 基线可比。

**最终状态** — D 阶段质量数据证明召回水位是瓶颈，ja_002/ja_004 进入候选池 → 不必为质量启动 B 阶段（本地 rerank + GPU）。

**代码定位** —
- [src/videomind/core/rag/pipeline.py:48-89](src/videomind/core/rag/pipeline.py#L48-L89) `search()` 加 `recall_k: int | None = None`，`recall = recall_k if recall_k is not None else top_k`
- [src/videomind/core/agent_loop/executor.py:37-41](src/videomind/core/agent_loop/executor.py#L37-L41) `RECALL_TOP_K = 60` / `LLM_CONTEXT_TOP_K = 12`
- [.env:101](.env#L101) `RERANK_TOP_N=60`（与 `.env.example` 同步）
- 设计权威：[docs/superpowers/specs/2026-08-04-retrieval-depth-tuning-design.md](docs/superpowers/specs/2026-08-04-retrieval-depth-tuning-design.md)
- commit：`6059abc` / `0ebf246`（`run_eval.py --recall-k` CLI）

### 3.2 多视频并发检索　★

**背景** — 原 Executor 串行遍历 `state.media_ids`，N 个任务 × M 个视频 × L 检索延迟 → 墙钟 `O(N×M×L)`。扩展到三方/四方对比任务后串行成本线性放大。

**决策** — M 轴用 `asyncio.gather` 并发：各 `_RagRetriever.search` 开**独立 `AsyncSessionLocal`**（纯 async safe），墙钟从 `O(N×M×L)` 降为 `O(N×L)`，实测 ~3× 提速。

**最终状态** — 媒体维度并发检索成为 Executor 的固定形态；`memory_ids` 上限前端 4、后端校验。

**代码定位** — [src/videomind/core/agent_loop/executor.py:92-129](src/videomind/core/agent_loop/executor.py#L92-L129) `asyncio.gather(*[_search_one_mid(mid) for mid in state.media_ids])`
- commit：`16e8638`

### 3.3 P2-1：Cross-encoder rerank opt-in（三档后端）　★★

**背景** — 设计文档（RAG-RETRIEVAL.md §2.6 / INTENT-ROUTING.md §7）的重排器是 `DeterministicReranker`（固定权重）+ 可选 `CrossEncoderReranker`，融合 `rerank_score = 0.7*det + 0.3*ce`，`CrossEncoder` 默认设备 `cuda`。问题：固定权重重排**与 query 无关**，无法照 query-文档相关性精排；而 cuda 本地重排需额外 GPU。

**决策** — 新抽象 `Reranker` 协议 `rerank(query, hits) -> list[VectorHit]`（cross-encoder 必须 query 参与，故**重设协议签名**）。`get_reranker()` 按 `config.rerank_provider` 三档路由：

| provider | 后端 | 说明 |
|---|---|---|
| `off` | `DeterministicRerankerAdapter` | 包现有固定权重器，收 query 后忽略、转调；opt-in 升级前**不破坏现状** |
| `api` | `OpenRouterRerankBackend` | 打 POST `{base_url}/rerank`，纯文本渠道 `documents=[{"text":...}]`，按 `relevance_score` 降序重组 |
| `local` | `LocalBGERerankBackend` | 本地 `BAAI/bge-reranker-v2-m3`（与 BGE-M3 embedder 同源、CJK 强、CPU 可跑、零 API 成本），import/加载失败由工厂降级 |

降级一律走 `DeterministicRerankerAdapter` 保证重排链路总有可用后端。

**设计偏离** — **不再做 `0.7*det+0.3*ce` 融合**，改用纯 cross-encoder `relevance_score`；跨越 query/media 尺度不一（cross-encoder Nemotron 中文 ~0.16 vs 降级 Deterministic ~0.5-0.95）的问题由 **min-max 归一**解决。

**最终状态** — `config.py`/`.env.example` 默认 `off`（opt-in 前不破坏现状）；`.env` 实开 `api`（本地运行走 OpenRouter rerank 端点）。

**代码定位** —
- [src/videomind/core/rag/rerank_backend.py](src/videomind/core/rag/rerank_backend.py) 协议 + Adapter + OpenRouter 后端 + 工厂
- [src/videomind/core/rag/rerank.py](src/videomind/core/rag/rerank.py) `DeterministicReranker`（`position 0.3 + source 0.2 + original 0.5`）
- [src/videomind/config.py:113-129](src/videomind/config.py#L113-L129)
- memory：`rag-cross-encoder-rerank`

### 3.4 D-β：QueryRewriter `confidence_threshold` 回滚　★★

**背景** — 原 `QueryRewriter.confidence_threshold = 0.7`，LLM 改写多数因置信度不达标而 **fallback rule**（实测 P2-4 `method=rule` 清一色，改写形同虚设）。D-β 提议 `0.7→0.5` 让 LLM 改写真生效（commit `0ebf246`）。

**但这带来副作用**：对 `base_rank` 已精确（≤4）的查询，扩散子查询注入噪声 —— 9 条评测集上 **1UP / 3DN 净负**，极端把 rank 1 冲到 21（见 `results_queryrewriter.json`）。防恶化锚 `zh_001` P2-4 baseline `rank=52 → multi rank=136` 恶化。

**决策 + 证据** — 回滚 `0.5→0.7`（commit `8b7f478`）。判定：LLM 非确定性方差远大于 0.5↔0.7 阈值差异，"调阈值"不是解药 —— 0.7 保守，仅高置信开火，保留 `zh_001` 这种欠指定查询的 UP，规避已精确查询的扩散恶化。

**最终状态** — `confidence_threshold` 默认 0.7，docstring 原文记录 D-β 理由。

**代码定位** — [src/videomind/core/intent/rewriter.py:54-62](src/videomind/core/intent/rewriter.py#L54-L62) docstring；`:60` 默认值
- commit：`0ebf246`（硬改验证）→ `8b7f478`（保守定值）
- memory：`rewriter-threshold-not-cure`

### 3.5 max_rounds 2→3 + media_ids 2→4　★

**背景** — 跨视频对比任务（三方/四方）对 AgentLoop 轮数与媒体数量提出更高要求：Critic 的 feedback 在 2 轮内常来不及被真吸收；对比对象从 2 个扩展到 4 个。

**决策** — `AnalyzeRequest.max_rounds: le=2 → le=3`（保持默认 2，允许上调）；`media_ids: max_length=2 → max_length=4`。前端 Zod schema 同步：`max(2)→max(3)`、`min(1).max(4)`；i18n 新增 `threeRounds` / `maxVideosReached`。

**最终状态** — 后端 `models.py` `max_rounds` 默认 2；API + 前端允许 3 轮 / 4 视频；3 条新增测试（`max_rounds=3`、`4`、`4 videos`）8/8 绿。

**代码定位** — [src/videomind/interface/routes/agent.py:49](src/videomind/interface/routes/agent.py#L49)（`le=3`）、[src/videomind/infrastructure/storage/models.py:503](src/videomind/infrastructure/storage/models.py#L503)
- commit：`910695a`

### 3.6 search→rerank→expand 顺序 + 邻居不参与 rerank　★

**背景** — 旧实现 `expand → rerank` 让扩出来的邻居 chunk 靠 `position(0.3)+source(0.2)` 抢占真实命中排序，污染重排结果。这与"邻居只是上下文补全"的设计意图相悖。

**决策** — 改为 **`search → rerank → expand`**：先对真实检索命中重排，再在重排后的序上扩前后 ±1 邻居。邻居以 `score=0.0` 追加到重排序列**末尾**，不参与 rerank；插入位置由其关联真实命中在重排后的位置决定。

**不变式** — `raw_hits` 不被 rerank 污染：pipeline 先传 `dataclasses.replace(h)` 拷贝，各 rerank 后端返回新列表（双保险，呼应 memory `rag-pipeline-rerank-expand-order`）。

**最终状态** — 邻居与真命中口径分离，归一时邻居 `0.0` 自然垫底不与真命中竞争。

**代码定位** — [src/videomind/core/rag/pipeline.py:91-118](src/videomind/core/rag/pipeline.py#L91-L118)
- memory：`rag-pipeline-rerank-expand-order`

### 3.7 BM25 CJK 分词（jieba + 2-gram）　★

**背景** — 原 `BM25Okapi([c.split() for c in corpus])` 对 CJK 用 `str.split()` 不分词，整条语句被当成单个 token，BM25 词项匹配失效（不是单纯"无标点"问题，是分词缺失）。中文/日文检索严重退化。

**决策** — 改 `jieba` 分词 + 2-gram，修复 CJK 词项召回。`pyproject.toml` 加 `jieba` 依赖。

**最终状态** — BM25 对 CJK 内容恢复词项匹配能力。

**代码定位** — [src/videomind/core/rag/retriever.py](src/videomind/core/rag/retriever.py)（BM25 索引构建/打分）
- memory：`bm25-cjk-tokenize` / `asr-no-punctuation-cjk`

### 3.8 证据校验简化 + 集合防幻觉　★

**背景** — 设计文档 `EvidenceVerifier` 做 `timestamp_ms` 范围 + jieba 字符级相似度（阈值 0.7）+ source 分流（asr/ocr/frame 各走 `_verify_asr/_verify_ocr/_verify_frame`）。这依赖 segment 装载 + ASR/OCR 原文比对，重且耦合视频管线。

**决策** — 两层简化：
1. `EvidenceVerifier`（[verifier.py](src/videomind/core/agent_loop/verifier.py)）降为**纯函数**：仅校验时间戳 ∈ `[0, duration_ms]` + content 非空，不依赖 DB/ASR/OCR。
2. 防幻觉改走**集合包含**：Executor 把所有真实检索 hit 的 id 汇入 `state.retrieved_evidence_ids`；Critic 校验 `evidence.id ∈ retrieved` 且每条 `conclusion.evidence_ids ⊆ retrieved` —— LLM 编造的 EID 因不在真实检索集中被直接判幻觉。

**最终状态** — Critic.pasred = `llm_passed AND hard_passed`（`hard_passed = evidence_real AND conclusions_real`）。集合校验比原文相似度更强：伪造 ID 100% 被抓，且无 jieba 相似度的灰区。

**代码定位** — [src/videomind/core/agent_loop/critic.py:113-118](src/videomind/core/agent_loop/critic.py#L113-L118)、[src/videomind/core/agent_loop/executor.py:161-165](src/videomind/core/agent_loop/executor.py#L161-L165)、[src/videomind/core/agent_loop/verifier.py](src/videomind/core/agent_loop/verifier.py)

### 3.9 reasoning 透传 + rewriter 关思维链 + db 透传　★

**背景** — OpenRouter reasoning 模型（如 nemotron-3-ultra）默认吐思考链 + 末尾 JSON，`json.loads(整段)` 必失败 → 改写器全 fallback rule（D-β 阈值根本无机会生效）。另外改写器调 LLM 不透传 `db`，计费/记账链路在评测路径断开。

**决策** —
1. `ChatRequest.reasoning` 透传到网关请求 body `{"reasoning":{"enabled":...}}`（commit `66a1864`）。
2. `QueryRewriter` 调 LLM 显式 `reasoning=False` 关思维链，让模型直接吐纯 JSON（commit `66a1864`）。
3. `rewriter.rewrite(..., db)` 透传 `db` 给 `RoutingLLMService.chat(req, db)` 做计费入库；`db=None` 时 accounting 静默跳过入库（LLM 照调），评测/无 session 路径不阻断（commit `d4692e1`）。

**最终状态** — 改写 JSON 可解析，D-β 阈值方能生效；计费链路打通但评测不被阻断。

**代码定位** — [src/videomind/core/intent/rewriter.py:81-91](src/videomind/core/intent/rewriter.py#L81-L91)
- commit：`66a1864` / `d4692e1`

### 3.10 score min-max 归一　★

**背景** — cross-encoder 与降级 Deterministic 跨 query/media 合并时尺度不一（Nemotron raw 中文 ~0.16，Deterministic 线性加权 ~0.5–0.95），降级高分挤掉真实高相关 hit；前端按 `score*100` 显示百分比也需 `[0,1]`。

**决策** — 对真实命中（`score>0`）min-max 归一到 `[0,1]`；邻居 `score=0.0` 不纳入统计保持 0.0，跨 query 合并时自然垫底。`span≈0`（所有真命中同分）退化为 1.0 保留高分概念。`raw_hits` 与 trace 记归一前原值保真。

**最终状态** — 前端百分比有意义、跨 query/media 合并不再因尺度错位丢命中。

**代码定位** — [src/videomind/core/rag/pipeline.py:112-143](src/videomind/core/rag/pipeline.py#L112-L143)

### 3.11 D → B 决策点（B 阶段搁置）

**背景** — 设计文档 §9 定 D→B 决策点：D-α+D-β eval 后看 hard query 是否进 evidence top-20。
- 命中情况 1：`ev_recall@5_hard` 拉升且 ja_002/ja_004 进 top-20 → **召回水位是瓶颈、B 不必为质量做**
- 命中情况 2：宽召回 + `rerank_top_n=60` 仍 ranks 不进 → 启 B（local rerank 不截断 + GPU）
- 中间态：B + D 叠加

**决策 + 证据** — D 阶段实测命中**情况 1**（hard 进入候选池，ev_MRR 自基线 0.3333 提升）→ B 阶段（本地 rerank 上 GPU）**判不做**，仅三类因子反转（质量回退 / 延迟退化为可接受上限 / 成本失控）才重启。

**最终状态** — B 阶段搁置；`rerank_provider=api` 用 OpenRouter 远程 cross-encoder 顶替"本地 + GPU"路径。

**代码定位** — 设计权威 §9；memory：`stage-b-local-rerank-gpu-not-now`

---

## 4. 尚未落地 / 已简化的计划项 / Deferred or Simplified

设计文档描画但实现中**尚未落地**或**简化**的项，统一收口于此（非 bug，是显式取舍）：

| 项 | 计划出处 | 当前状态 | 备注 |
|---|---|---|---|
| **SQL / 结构化检索通道** | RAG-RETRIEVAL §2.3 / INTENT-ROUTING §5 | 未实现 | `IntentRouter` 仍算 sql 配额（`ChannelQuota.sql`），但无 `SQLSearchChannel`，`HybridRetriever` 只跑 vector+bm25 |
| **MCP 工具调用集成** | INTENT-ROUTING §8 | 未实现 | `MCPToolRegistry` / `VideoSegmentTool` / `VideoSearchTool` 未落地；网关仅透传上游 `tool_calls` delta |
| **后处理器链** | RAG-RETRIEVAL §2.3 | 未实现 | `Deduplication / ThresholdFilter(0.3) / DiversitySampling` 无显式实现 |
| **Checkpoint Redis 热缓存 + `resume_analysis`** | AGENT-LOOP §5 | 部分落地 | `infrastructure/cache/redis.py` 已建 checkpoint 缓存基础设施；生产 `_checkpoint`（`routes/agent.py`）**当前直接写 PostgreSQL**（`AgentCheckpoint` 表）；`resume_analysis` 断点恢复入口未见独立实现 |
| **`EvidenceVerifier` source 分流 + jieba 相似度** | AGENT-LOOP §3.4 | 已简化 | 见 §3.8，改纯函数 + 集合防幻觉 |
| **追问复用 `follow_up`** | AGENT-LOOP §6 | 未实现 | 加载前轮 `AgentState` / `VideoContext` 复用未见独立入口 |
| **CrossEncoder `0.7*det+0.3*ce` 融合 + cuda 默认** | RAG-RETRIEVAL §2.6 | 改设计 | 见 §3.3，改纯 cross-encoder 分 + min-max 归一 |
| **`llm_first_packet_timeout_s` 首包超时** | MODEL-GATEWAY | 未消费 | 配置存在（10.0），当前主路径未消费 |
| **意图树 yaml 文件化** | INTENT-ROUTING §2 | 实现选择 | 改代码内 `DEFAULT_INTENT_TREE` 常量，少一个文件依赖 |

> 说明：以上"未实现"项若有重启需求，对应设计文档章节仍为权威 specs，无需重新设计。

---

## 5. 决策来源索引 / Source Index

| 类别 | 位置 |
|---|---|
| D-α / D-β / P2-5 设计 | `docs/superpowers/specs/2026-08-04-retrieval-depth-tuning-design.md` |
| P2-4 诊断脚本 | `scripts/eval/diag_retrieval.py` / `eval_queryrewriter.py` |
| 评测结果 JSON | `results_off_api_topk20_recallk60.json` / `results_queryrewriter.json` |
| 代码内决策记录 | `rewriter.py:54-62`（D-β）、`executor.py:37-41`（D-α）、`pipeline.py:37-117`（顺序/分层/归一）、`rerank_backend.py:1-19`（P2-1） |
| Commit 序列 | `0ebf246`(D-β硬改) → `66a1864`(reasoning透传) → `8b7f478`(D-β回滚) → `16e8638`(并发检索:D-α) → `910695a`(max_rounds/media_ids) → `6059abc`(D-α分层+双路径重排) |
| 持久记忆 | `~/.claude/projects/d--shu-e-Documents-Video-MInd-python/memory/`（`rag-pipeline-rerank-expand-order` / `rag-cross-encoder-rerank` / `rewriter-threshold-not-cure` / `bm25-cjk-tokenize` / `stage-b-local-rerank-gpu-not-now` 等） |

---

> 本文档随实现与决策演进持续更新；新增偏离设计的决策应在此**追加**记录而非改写历史，以保留可追溯的决策链。
