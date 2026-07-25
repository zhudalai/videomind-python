# VideoMind 混合检索与 RAG 管线设计

> 检索原语链：HybridRetriever（向量+BM25）→ RRFusion（K=60）→ ContextExpander（±1）→ Rerank（BGE-Reranker-v2-m3）
> 核心参考：vid-lens `internal/service/chat.go` + Ragent `bootstrap/rag/retrieve/`

> **本文定位**：检索**原语与算法详解**（向量/词项融合、RRF 数学推导、上下文扩展、重排器选型、离线评测框架）。
> **权威定义另见他文**：意图树 / `IntentRouter` / 多通道编排入口 `MultiChannelRetrieval` / `evidence_id` / `[EID_xxx]` 引用样式 / 前端引用解析 —— 见 [INTENT-ROUTING.md](./INTENT-ROUTING.md)；评测外壳 `RetrievalPipeline` —— 见 [OBSERVABILITY.md](./OBSERVABILITY.md) rag-eval。本文不复述、不另议，以免双源漂移。
> 调用链（权威）：`IntentRouter → MultiChannelRetrieval → HybridRetriever(+SqlRetriever) → RRF / ContextExpander / Rerank → AnswerGenerator`。

---

## 1. 检索管线总览（算法视图）

> 编排入口、意图路由、答案生成与引用渲染的权威定义见 [INTENT-ROUTING.md](./INTENT-ROUTING.md)；本图仅展示检索算法栈。

```text
用户查询（来自 INTENT 的 IntentRouter → MultiChannelRetrieval）
      │
      ▼
┌──────────────────────────────────────────────────────────┐
│  HybridRetriever —— 向量 + BM25 单融合通道（本文 §2.3）     │
│  ┌────────────────┐      ┌──────────────────┐             │
│  │  VectorSearch   │      │    BM25Search    │             │
│  │   (Qdrant)      │      │ (Rank-BM25 内存) │             │
│  └────────┬────────┘      └────────┬─────────┘             │
└───────────┼─────────────────────────┼─────────────────────┘
            └───────────┬─────────────┘
                        ▼
┌──────────────────────────────────────────────────────────┐
│  RRFusion (K=60): score = Σ 1/(K + rank_i + 1)  →  TopK   │
│  （0-based；权威公式见 INTENT-ROUTING §6）                  │
└──────────────────────┬───────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────┐
│  ContextExpander (±1 chunk，按 chunk_index 字典扩展)        │
│  （本文 §2.5；与 INTENT §6 同一实现）                        │
└──────────────────────┬───────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────┐
│  Rerank                                                    │
│   · DeterministicReranker（无模型，权重见 INTENT §7）         │
│   · CrossEncoderReranker（BGE-Reranker-v2-m3，本文 §2.6）     │
└──────────────────────┬───────────────────────────────────┘
                       ▼
   结果交回 INTENT 的 AnswerGenerator
   生成 [EID_xxx] 引用（权威见 INTENT make_evidence_id）
```

---

## 2. 核心组件详细设计

### 2.1 QueryRewrite（查询改写 + 拆分）

```python
class QueryRewriter:
    REWRITE_PROMPT = """
    你是查询改写专家。给定用户原始问题和对话历史，请：
    1. 消除指代、补全省略信息，生成独立的完整问题
    2. 若原问题包含多个子问题，拆分为 1-3 个子问题
    3. 仅输出 JSON：{"rewritten": "...", "sub_questions": ["...", "..."]}
    
    对话历史：{history}
    原始问题：{query}
    """
    
    async def rewrite(self, query: str, history: list[Message]) -> RewriteResult:
        # 1. 术语归一化（同义词映射表）
        normalized = self.term_mapper.normalize(query)
        
        # 2. LLM 改写
        try:
            response = await self.llm.chat(
                messages=[{"role": "user", "content": self.REWRITE_PROMPT.format(
                    history=format_history(history),
                    query=normalized
                )}],
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            data = json.loads(response.content)
            return RewriteResult(
                original=query,
                rewritten=data["rewritten"],
                sub_questions=data.get("sub_questions", [data["rewritten"]])
            )
        except Exception:
            # 3. 规则兜底：按标点拆分
            sub_qs = self._rule_split(normalized)
            return RewriteResult(
                original=query,
                rewritten=normalized,
                sub_questions=sub_qs
            )
    
    def _rule_split(self, text: str) -> list[str]:
        # 中英文标点拆分，保留语义完整性
        parts = re.split(r'[？?。！!；;]', text)
        return [p.strip() for p in parts if p.strip()]
```

---

### 2.2 意图识别 —— 权威见 INTENT-ROUTING.md

意图树（`video_qa / video_search / video_analysis / video_generation / meta_query`，含各子意图与通道配额）与 `IntentRouter`（0.4 关键词 + 0.6 LLM 融合 + 通道配额）的**权威定义在 [INTENT-ROUTING.md §意图树 + IntentRouter](./INTENT-ROUTING.md)**，本文不再复述以免双源漂移。下文 §2.3 的 `HybridRetriever` 即被该意图路由编排消费的检索原语之一。

> 意图分类的**权威实现是 [INTENT-ROUTING.md](./INTENT-ROUTING.md) 的 `IntentRouter`**（0.4 关键词 + 0.6 LLM 融合 + 通道配额），取代早期 `IntentClassifier`（`MIN_SCORE` / `MAX_INTENTS` 等配额逻辑已并入 `IntentRouter`）。本节不再重复定义以免双源漂移。

---

### 2.3 HybridRetriever（向量 + BM25 单融合通道）

> 多通道编排入口 `MultiChannelRetrieval`（聚合 HybridRetriever + SqlRetriever，跑 RRF / ContextExpander / 重排）的权威定义在 [INTENT-ROUTING.md §MultiChannelRetrieval](./INTENT-ROUTING.md)。本节仅描述被其消费的单融合通道原语。

```python
class HybridRetriever:
    def __init__(self):
        self.channels: list[SearchChannel] = [
            VectorSearchChannel(top_k=50),
            BM25SearchChannel(top_k=50),
            # GraphSearchChannel(top_k=30),  # 预留
        ]
        self.post_processors: list[SearchResultPostProcessor] = [
            DeduplicationProcessor(),
            ThresholdFilterProcessor(min_score=0.3),
            DiversitySamplingProcessor(diversity_factor=0.3),
        ]
    
    async def retrieve(self, ctx: RetrievalContext) -> list[SearchResult]:
        # 1. 并行执行所有启用的通道
        enabled = [c for c in self.channels if c.is_enabled(ctx)]
        channel_results = await asyncio.gather(*[
            c.search(ctx) for c in enabled
        ])
        
        # 2. 合并结果（保留通道来源信息）
        merged = []
        for ch_name, results in zip([c.name for c in enabled], channel_results):
            for r in results:
                r.channel = ch_name
                merged.append(r)
        
        # 3. 后处理器链
        for processor in self.post_processors:
            if processor.is_enabled(ctx):
                merged = processor.process(merged, channel_results, ctx)
        
        return merged
```

**SearchChannel 接口**（策略模式）：
```python
class SearchChannel(Protocol):
    name: str
    priority: int
    
    def is_enabled(self, ctx: RetrievalContext) -> bool: ...
    
    async def search(self, ctx: RetrievalContext) -> list[SearchResult]: ...


class VectorSearchChannel:
    name = "vector"
    priority = 10
    
    def __init__(self, top_k: int = 50):
        self.top_k = top_k
        self.qdrant = QdrantClient()
    
    async def search(self, ctx: RetrievalContext) -> list[SearchResult]:
        # 为每个子问题生成向量并搜索
        all_results = []
        for sq in ctx.sub_questions:
            vector = await ctx.embedder.embed(sq)
            hits = await self.qdrant.search(
                collection_name="video_chunks",
                query_vector=vector,
                limit=self.top_k,
                query_filter=models.Filter(
                    must=[models.FieldCondition(key="media_id", match=models.MatchValue(value=ctx.media_id))]
                ) if ctx.media_id else None
            )
            for hit in hits:
                all_results.append(SearchResult(
                    chunk_id=hit.id,
                    content=hit.payload.get("content", ""),
                    score=hit.score,
                    metadata=hit.payload,
                    channel=self.name
                ))
        return all_results


class BM25SearchChannel:
    name = "bm25"
    priority = 20
    
    def __init__(self, top_k: int = 50):
        self.top_k = top_k
        self.bm25_indexes: dict[UUID, BM25Okapi] = {}
    
    async def search(self, ctx: RetrievalContext) -> list[SearchResult]:
        # 加载/构建 BM25 索引（内存缓存）
        if ctx.media_id not in self.bm25_indexes:
            chunks = await self.chunk_repo.get_by_media(ctx.media_id)
            corpus = [c.content for c in chunks]
            self.bm25_indexes[ctx.media_id] = BM25Okapi([c.split() for c in corpus])
        
        bm25 = self.bm25_indexes[ctx.media_id]
        all_results = []
        for sq in ctx.sub_questions:
            scores = bm25.get_scores(sq.split())
            top_indices = np.argsort(scores)[::-1][:self.top_k]
            for idx in top_indices:
                if scores[idx] > 0:
                    chunk = chunks[idx]
                    all_results.append(SearchResult(
                        chunk_id=chunk.id,
                        content=chunk.content,
                        score=float(scores[idx]),
                        metadata={"chunk_index": chunk.chunk_index, "start_ms": chunk.start_ms},
                        channel=self.name
                    ))
        return all_results
```

---

### 2.4 RRFusion（倒数秩融合）

```python
class RRFusion:
    def __init__(self, k: int = 60, final_top_k: int = 20):
        self.k = k
        self.final_top_k = final_top_k
    
    def fuse(self, channel_results: list[SearchResult]) -> list[SearchResult]:
        # 按 chunk_id 分组，收集各通道排名
        by_chunk: dict[UUID, dict[str, int]] = defaultdict(dict)
        
        # 为每个通道的结果分配排名
        by_channel = defaultdict(list)
        for r in channel_results:
            by_channel[r.channel].append(r)
        
        for ch_name, results in by_channel.items():
            for rank, r in enumerate(results):
                by_chunk[r.chunk_id][ch_name] = rank  # 0-based（与 INTENT §6 对齐）
        
        # 计算 RRF 分数
        fused = []
        for chunk_id, ranks in by_chunk.items():
            rrf_score = sum(1.0 / (self.k + rank + 1) for rank in ranks.values())  # score = Σ 1/(K + rank + 1)，0-based
            # 取任一通道的内容/元数据
            sample = next(r for r in channel_results if r.chunk_id == chunk_id)
            fused.append(SearchResult(
                chunk_id=chunk_id,
                content=sample.content,
                score=rrf_score,
                metadata=sample.metadata,
                channel="rrf",
                channel_ranks=ranks
            ))
        
        # 排序取 TopK
        fused.sort(key=lambda x: x.score, reverse=True)
        return fused[:self.final_top_k]
```

---

### 2.5 ContextExpander（上下文扩展）

```python
class ContextExpander:
    def __init__(self, expand_window: int = 1):
        self.expand_window = expand_window  # ±N 个相邻 chunk
    
    def expand(self, results: list[SearchResult], all_chunks: list[Chunk]) -> list[SearchResult]:
        """向前/向后各扩 expand_window 个 chunk，保证上下文连贯"""
        # 建立 chunk_index → chunk 映射
        by_media: dict[UUID, dict[int, Chunk]] = defaultdict(dict)
        for c in all_chunks:
            by_media[c.media_id][c.chunk_index] = c
        
        expanded = []
        seen = set()
        
        for r in results:
            if r.chunk_id in seen:
                continue
            seen.add(r.chunk_id)
            expanded.append(r)
            
            # 扩展相邻
            media_chunks = by_media.get(r.metadata.get("media_id"))
            if not media_chunks:
                continue
            
            center_idx = r.metadata.get("chunk_index", 0)
            for offset in range(-self.expand_window, self.expand_window + 1):
                if offset == 0:
                    continue
                neighbor_idx = center_idx + offset
                if neighbor_idx in media_chunks:
                    neighbor = media_chunks[neighbor_idx]
                    if neighbor.id not in seen:
                        seen.add(neighbor.id)
                        expanded.append(SearchResult(
                            chunk_id=neighbor.id,
                            content=neighbor.content,
                            score=r.score * 0.8,  # 扩展 chunk 降权
                            metadata={"chunk_index": neighbor.chunk_index, "expanded": True},
                            channel="expanded"
                        ))
        
        return expanded
```

---

### 2.6 Rerank（重排序）

```python
class Reranker(Protocol):
    def rerank(self, query: str, results: list[SearchResult]) -> list[SearchResult]: ...


# ↓ DeterministicReranker（无模型确定性重排）实现已收敛到 INTENT-ROUTING §7 单一权威：
#   from core.intent.rerank import DeterministicReranker
# 权重：时间新近度 0.2 / 来源权威性 0.15 / 内容完整性 0.15 / 意图匹配度 0.2（详见 INTENT-ROUTING §7）。
# 本文仅保留算法动机说明，不再重复实现，避免双源漂移。


class CrossEncoderReranker:
    """Cross-Encoder 重排（可选，精度更高但需额外 GPU）"""
    
    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3", device: str = "cuda"):
        self.model = CrossEncoder(model_name, device=device, max_length=512)
    
    def rerank(self, query: str, results: list[SearchResult]) -> list[SearchResult]:
        pairs = [(query, r.content[:512]) for r in results]
        scores = self.model.predict(pairs, batch_size=16)
        
        for r, score in zip(results, scores):
            r.cross_encoder_score = float(score)
            r.rerank_score = 0.7 * r.rerank_score + 0.3 * score  # 融合确定性分数
        
        results.sort(key=lambda x: x.rerank_score, reverse=True)
        return results
```

---

### 2.7 AnswerGenerator（答案生成 + 引用溯源）

**证据 ID 稳定引用锚点**（权威见 INTENT-ROUTING.md `make_evidence_id`）：

> 统一格式：`evidence_id = EID_{chunk_id[:8]}_{idx:02d}`；引用写作 `[EID_xxx]`；前端解析正则 `\[EID_[a-z0-9]{8}_\d{2}\]`。
> 本文不再重复定义 `make_evidence_id`，AnswerGenerator 直接复用 INTENT-ROUTING 实现，避免双源漂移。

**Prompt 构建**：
```python
class AnswerGenerator:
    SYSTEM_PROMPT = """
    你是视频内容分析助手。基于提供的证据片段回答用户问题。
    
    规则：
    1. 仅使用证据中的信息回答，不可编造
    2. 每个结论必须引用证据，格式：[EID_xxx]
    3. 证据包含时间戳，回答中可标注大致时间范围
    4. 若证据不足，明确说明"根据现有内容无法确定"
    5. 使用 Markdown 格式，结构化输出
    """
    
    async def generate(self, query: str, results: list[SearchResult]) -> AnswerStream:
        # 构建证据块
        evidence_blocks = []
        for i, r in enumerate(results):
            eid = make_evidence_id(r.chunk_id, i)  # 来自 INTENT-ROUTING：EID_{chunk_id[:8]}_{idx:02d}
            evidence_blocks.append(f"""
    [{eid}] [{ms_to_ts(r.metadata.get('start_ms', 0))}-{ms_to_ts(r.metadata.get('end_ms', 0))}]
    {r.content[:800]}
    """)
        
        context = "\n---\n".join(evidence_blocks)
        
        prompt = f"""
    用户问题：{query}
    
    证据片段：
    {context}
    
    请基于上述证据回答，每个结论后标注引用编号如 [EID_xxx]。
    """
        
        # 流式生成
        async for chunk in self.llm.stream_chat([
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]):
            yield chunk
```

**前端渲染引用卡片**：
```typescript
// 前端解析引用编号，渲染可点击卡片
const parseCitations = (markdown: string) => {
  const citationRegex = /\[EID_[a-z0-9]{8}_\d{2}\]/g;  // 与 INTENT-ROUTING make_evidence_id 对齐
  // 替换为可点击组件，点击跳转视频对应时间戳
};
```

---

## 3. 离线评测集成（rag-eval 风格）

```python
class RetrievalEvaluator:
    """CI 集成：每次检索管线变更自动跑基线对比"""
    
    async def evaluate(self, dataset_version: str, config_name: str) -> EvalReport:
        # 1. 加载封闭测试集（需 Token 授权）
        dataset = await self.load_dataset(dataset_version, split="test")
        
        # 2. 冻结证据（从 PostgreSQL/Qdrant 采样 → 规范化 JSON → SHA256）
        frozen_evidence = await self.freeze_evidence(dataset)
        
        # 3. 跑管线
        predictions = []
        for sample in dataset:
            ctx = RetrievalContext(
                query=sample.query,
                media_id=sample.media_id,
                sub_questions=[sample.query],  # 简化
                embedder=self.embedder
            )
            results = await self.pipeline.retrieve(ctx)
            predictions.append(self._format_prediction(results))
        
        # 4. 计算指标
        metrics = {
            "recall@5": self._recall_at_k(dataset, predictions, 5),
            "recall@10": self._recall_at_k(dataset, predictions, 10),
            "mrr": self._mrr(dataset, predictions),
            "latency_p50": self._latency_percentile(predictions, 50),
            "latency_p95": self._latency_percentile(predictions, 95),
            "no_result_rate": self._no_result_rate(predictions)
        }
        
        # 5. 单变量消融（仅对比 config_name 与 baseline）
        if config_name != "baseline":
            ablation = self._validate_single_variable_ablation(config_name)
            metrics["ablation_passed"] = ablation
        
        return EvalReport(
            dataset_version=dataset_version,
            config=config_name,
            metrics=metrics,
            evidence_sha256=frozen_evidence.sha256,
            timestamp=datetime.utcnow()
        )
```

---

## 4. 关键配置

```yaml
# config/rag_pipeline.yaml
rag:
  query_rewrite:
    enabled: true
    model: "qwen2.5-7b"
    temperature: 0.1
    max_sub_questions: 3
  
  # 意图树 / IntentRouter 配置由 INTENT-ROUTING.md 统一管理，此处不维护，避免双源。
  
  retrieval:
    vector:
      top_k: 50
      collection: "video_chunks"
    bm25:
      top_k: 50
    rrf:
      k: 60
      final_top_k: 20
    expand_window: 1
  
  rerank:
    deterministic:
      enabled: true
      # 权重（时间新近0.2 / 来源权威0.15 / 内容完整性0.15 / 意图匹配0.2）见 INTENT-ROUTING §7
    cross_encoder:
      enabled: false  # 需额外 GPU 时开启
      model: "BAAI/bge-reranker-v2-m3"
      weight: 0.3
  
  answer:
    model: "qwen2.5-7b"
    temperature: 0.3
    max_tokens: 2048
    system_prompt: "prompts/answer_system.md"
  
  evaluation:
    dataset_version: "v1"
    baseline_config: "baseline"
    metrics: ["recall@5", "recall@10", "mrr", "latency_p50", "latency_p95"]
```

---

> **关联文档**：[ARCHITECTURE.md](ARCHITECTURE.md) · [VIDEO-PIPELINE.md](VIDEO-PIPELINE.md) · [AGENT-LOOP.md](AGENT-LOOP.md) · [INTENT-ROUTING.md](INTENT-ROUTING.md) · [OBSERVABILITY.md](OBSERVABILITY.md)