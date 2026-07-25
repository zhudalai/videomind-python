# VideoMind 意图路由与查询改写设计

> 树形意图识别 + LLM 查询改写/子问题拆分 + 多通道检索编排 + MCP 工具调用集成
> 核心参考：Ragent `infra-rag/` (IntentRouter + MultiChannelRetrieval) + vid-lens `internal/rag/` (QueryRewriter + ContextExpander)

---

## 1. 意图路由总览

```
用户查询
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│                      QueryRewriter                              │
│  1. 同义词扩展 + 实体归一化                                        │
│  2. 子问题拆解（复杂查询 → 2-4 个原子子问题）                       │
│  3. 规则兜底（LLM 失败时正则/关键词回退）                           │
└────────────────────────────────┬────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                      IntentRouter                               │
│  输入：原查询 + 子问题列表                                        │
│  树形意图配置：config/intent_tree.yaml                           │
│  并行分类 → 配额分配 → 输出 {intent: weight} 映射                  │
└────────────────────────────────┬────────────────────────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
      ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
      │ VectorSearch │   │  BM25Search  │   │  SQL/Struct  │
      │   Channel    │   │   Channel    │   │   Channel    │
      └──────────────┘   └──────────────┘   └──────────────┘
              │                  │                  │
              └──────────────────┼──────────────────┘
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                      RRFusion (k=60)                            │
│  Reciprocal Rank Fusion：score = Σ 1/(k + rank_i)                │
└────────────────────────────────┬────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                      ContextExpander (±1 chunk)                 │
│  检索到的 chunk 前后各扩展 1 个，保证上下文连贯                   │
└────────────────────────────────┬────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Reranker                                   │
│  1. DeterministicReranker（时间/来源/完整性打分，零延迟）            │
│  2. CrossEncoderReranker（可选，BGE-Reranker-v2-m3，<100ms）       │
└────────────────────────────────┬────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                      AnswerGenerator                            │
│  ChunkEvidenceID 稳定引用 + MCP Tool Calling                     │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 树形意图配置

```yaml
# config/intent_tree.yaml
version: "1.0"

intents:
  # 一级意图
  - id: "video_qa"
    name: "视频内容问答"
    weight: 1.0
    description: "基于视频转录/OCR/关键帧回答事实性问题"
    keywords: ["什么", "怎么", "为什么", "内容", "讲了", "说了"]
    children:
      - id: "video_qa.summary"
        name: "视频摘要"
        weight: 0.9
        description: "生成视频整体/分段摘要"
        keywords: ["摘要", "总结", "概括", "大意"]
      
      - id: "video_qa.detail"
        name: "细节追问"
        weight: 0.8
        description: "具体时间点/人物/事件的细节"
        keywords: ["第几分钟", "具体", "详细", "原话"]
      
      - id: "video_qa.quote"
        name: "原文引用"
        weight: 0.7
        description: "需要精确引用转录文本"
        keywords: ["原话", "引用", "文字记录", "逐字"]

  - id: "video_search"
    name: "视频检索定位"
    weight: 0.9
    description: "在视频库中搜索匹配的片段"
    keywords: ["找", "搜", "哪里", "位置", "时间点", "片段"]
    children:
      - id: "video_search.topic"
        name: "主题检索"
        weight: 0.9
        keywords: ["关于", "相关", "主题", "话题"]
      
      - id: "video_search.speaker"
        name: "发言人检索"
        weight: 0.7
        keywords: ["谁说", "发言人", "说话人", "声音"]
      
      - id: "video_search.visual"
        name: "画面检索"
        weight: 0.6
        keywords: ["画面", "画面里", "出现", "画面显示", "字幕"]

  - id: "video_analysis"
    name: "视频深度分析"
    weight: 0.8
    description: "跨片段推理、对比、因果分析"
    keywords: ["分析", "对比", "推理", "原因", "影响", "趋势"]
    children:
      - id: "video_analysis.compare"
        name: "对比分析"
        weight: 0.8
        keywords: ["对比", "区别", "差异", "相比"]
      
      - id: "video_analysis.causal"
        name: "因果推理"
        weight: 0.7
        keywords: ["为什么", "导致", "原因", "影响"]
      
      - id: "video_analysis.timeline"
        name: "时间线梳理"
        weight: 0.7
        keywords: ["时间线", "顺序", "过程", "发展"]

  - id: "video_generation"
    name: "视频衍生内容生成"
    weight: 0.7
    description: "基于视频生成脚本/笔记/大纲"
    keywords: ["生成", "写", "制作", "脚本", "笔记", "大纲", "PPT"]
    children:
      - id: "video_generation.script"
        name: "脚本改写"
        weight: 0.8
        keywords: ["脚本", "文案", "口播稿"]
      
      - id: "video_generation.notes"
        name: "学习笔记"
        weight: 0.7
        keywords: ["笔记", "知识点", "大纲", "思维导图"]
      
      - id: "video_generation.highlights"
        name: "精华切片"
        weight: 0.6
        keywords: ["精华", "高光", "切片", "短视频"]

  - id: "meta_query"
    name: "元数据查询"
    weight: 0.5
    description: "视频时长/作者/发布时间等元信息"
    keywords: ["时长", "作者", "上传", "发布", "标题", "标签"]

# 意图 → 检索通道权重映射
intent_channel_weights:
  video_qa:
    vector: 0.6
    bm25: 0.3
    sql: 0.1
  video_search:
    vector: 0.4
    bm25: 0.5
    sql: 0.1
  video_analysis:
    vector: 0.7
    bm25: 0.2
    sql: 0.1
  video_generation:
    vector: 0.5
    bm25: 0.3
    sql: 0.2
  meta_query:
    vector: 0.1
    bm25: 0.1
    sql: 0.8
```

---

## 3. 查询改写器

```python
class QueryRewriter:
    """
    三层改写策略：
    1. LLM 改写（主）：同义词扩展 + 实体归一化 + 子问题拆解
    2. 规则改写（备）：正则 + 词典，LLM 失败/超时时兜底
    3. 原始查询（底）：保留原查询作为最后一路
    """
    
    def __init__(self, llm: LLMService, config: RewriterConfig):
        self.llm = llm
        self.config = config
        self.rule_rewriter = RuleRewriter()
    
    async def rewrite(self, query: str, context: RewriteContext) -> RewriteResult:
        # 1. 尝试 LLM 改写
        llm_result = await self._llm_rewrite(query, context)
        if llm_result and llm_result.confidence > self.config.llm_confidence_threshold:
            return llm_result
        
        # 2. 规则兜底
        rule_result = self.rule_rewriter.rewrite(query, context)
        if rule_result.sub_queries:
            return rule_result
        
        # 3. 原始查询
        return RewriteResult(
            original=query,
            rewritten=query,
            sub_queries=[query],
            method="original",
            confidence=0.5
        )
    
    async def _llm_rewrite(self, query: str, context: RewriteContext) -> RewriteResult | None:
        prompt = f"""你是视频理解助手的查询改写器。请对用户查询进行改写和拆解。

用户查询：{query}
视频上下文：{context.video_title or "未知"} | {context.duration_sec or "未知"}秒

任务：
1. 同义词扩展：补充专业术语、口语变体
2. 实体归一化：人名/地名/机构名标准化
3. 子问题拆解：复杂查询拆为 2-4 个原子子问题（每个可独立检索）
4. 保留原始查询语义

输出 JSON：
{{
  "rewritten": "改写后的主查询",
  "sub_queries": ["子问题1", "子问题2", ...],
  "entities": {{"原始实体": "标准化实体"}},
  "confidence": 0.0-1.0
}}"""
        
        try:
            response = await self.llm.chat(ChatRequest(
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=512,
                thinking=False
            ))
            data = json.loads(response.content)
            return RewriteResult(
                original=query,
                rewritten=data["rewritten"],
                sub_queries=data["sub_queries"],
                entities=data.get("entities", {}),
                method="llm",
                confidence=data.get("confidence", 0.8)
            )
        except Exception as e:
            logger.warning(f"LLM rewrite failed: {e}")
            return None


class RuleRewriter:
    """规则兜底：关键词扩展 + 简单拆解"""
    
    SYNONYMS = {
        "视频": ["影片", "录像", "片子"],
        "内容": ["讲了什么", "说什么", "主题"],
        "总结": ["摘要", "概括", "大意"],
        "原话": ["逐字", "文字记录", "转录"],
        "画面": ["视频画面", "画面内容", "画面里"],
    }
    
    def rewrite(self, query: str, context: RewriteContext) -> RewriteResult:
        # 同义词扩展
        expanded = query
        for k, vs in self.SYNONYMS.items():
            if k in query:
                expanded += " " + " ".join(vs)
        
        # 简单拆解：检测 "和/或/、/," 分隔的多意图
        sub_queries = re.split(r"[和或、,，]", query)
        sub_queries = [q.strip() for q in sub_queries if len(q.strip()) > 3]
        if len(sub_queries) == 1:
            sub_queries = [query]
        
        return RewriteResult(
            original=query,
            rewritten=expanded,
            sub_queries=sub_queries[:4],  # 最多 4 个
            method="rule",
            confidence=0.6
        )


@dataclass
class RewriteResult:
    original: str
    rewritten: str
    sub_queries: list[str]
    entities: dict[str, str] = field(default_factory=dict)
    method: str = "original"
    confidence: float = 0.5
```

---

## 4. 意图路由器

```python
class IntentRouter:
    """
    树形意图并行分类：
    1. 将所有叶子意图展平
    2. 并行计算每个意图的匹配分数（关键词 + LLM 轻量分类）
    3. 向上聚合到父意图（取子意图最大分数 * 父权重）
    4. 归一化权重，输出 Top-K 意图及其检索配额
    """
    
    def __init__(self, config: IntentRouterConfig, llm: LLMService):
        self.tree = self._load_tree(config.tree_path)
        self.llm = llm
        self.channel_weights = config.channel_weights
    
    def _load_tree(self, path: str) -> IntentTree:
        with open(path) as f:
            data = yaml.safe_load(f)
        return self._build_tree(data["intents"])
    
    def _build_tree(self, intents: list[dict], parent: IntentNode | None = None) -> IntentTree:
        nodes = {}
        for item in intents:
            node = IntentNode(
                id=item["id"],
                name=item["name"],
                weight=item.get("weight", 1.0),
                description=item.get("description", ""),
                keywords=item.get("keywords", []),
                parent=parent,
                children=[]
            )
            if "children" in item:
                node.children = self._build_tree(item["children"], node)
            nodes[node.id] = node
        return IntentTree(nodes)
    
    async def route(self, query: str, sub_queries: list[str]) -> IntentRouteResult:
        # 合并所有查询文本
        all_text = " ".join([query] + sub_queries)
        
        # 1. 关键词快速匹配（所有叶子节点并行）
        keyword_scores = self._keyword_match(all_text)
        
        # 2. LLM 轻量分类（Top-10 关键词候选）
        top_candidates = sorted(keyword_scores.items(), key=lambda x: x[1], reverse=True)[:10]
        llm_scores = await self._llm_classify(all_text, [n for n, _ in top_candidates])
        
        # 3. 融合分数
        fused = {}
        for node_id in self.tree.nodes:
            kw = keyword_scores.get(node_id, 0)
            llm = llm_scores.get(node_id, 0)
            fused[node_id] = 0.4 * kw + 0.6 * llm
        
        # 4. 向上聚合
        for node in self.tree.nodes.values():
            if node.children:
                child_max = max(fused.get(c.id, 0) for c in node.children)
                fused[node.id] = max(fused.get(node.id, 0), child_max * node.weight)
        
        # 5. 归一化 & 分配配额
        total = sum(fused.values()) or 1.0
        intent_weights = {k: v / total for k, v in fused.items() if v > 0.05}
        
        # 6. 计算各通道配额
        channel_quota = self._compute_channel_quota(intent_weights)
        
        return IntentRouteResult(
            intent_weights=intent_weights,
            channel_quota=channel_quota,
            primary_intent=max(intent_weights, key=intent_weights.get) if intent_weights else "video_qa"
        )
    
    def _keyword_match(self, text: str) -> dict[str, float]:
        scores = {}
        for node in self.tree.nodes.values():
            if not node.children:  # 仅叶子节点
                hits = sum(1 for kw in node.keywords if kw in text)
                if hits > 0:
                    scores[node.id] = hits / len(node.keywords)
        return scores
    
    async def _llm_classify(self, text: str, candidates: list[str]) -> dict[str, float]:
        if not candidates:
            return {}
        
        prompt = f"""判断查询属于哪些意图（多选，0-1 分数）。

查询：{text}
候选意图：
{chr(10).join(f"- {c}: {self.tree.nodes[c].description}" for c in candidates)}

输出 JSON：{{"intent_id": 0.0-1.0, ...}} 仅输出分数>0.3的"""
        
        try:
            response = await self.llm.chat(ChatRequest(
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=256
            ))
            return json.loads(response.content)
        except Exception:
            return {}
    
    def _compute_channel_quota(self, intent_weights: dict[str, float]) -> ChannelQuota:
        """按意图权重加权平均得到各通道配额"""
        vector = sum(
            intent_weights.get(intent, 0) * self.channel_weights.get(intent, {}).get("vector", 0)
            for intent in intent_weights
        )
        bm25 = sum(
            intent_weights.get(intent, 0) * self.channel_weights.get(intent, {}).get("bm25", 0)
            for intent in intent_weights
        )
        sql = sum(
            intent_weights.get(intent, 0) * self.channel_weights.get(intent, {}).get("sql", 0)
            for intent in intent_weights
        )
        total = vector + bm25 + sql or 1
        return ChannelQuota(
            vector=int(vector / total * 60),
            bm25=int(bm25 / total * 60),
            sql=int(sql / total * 60)
        )


@dataclass
class IntentRouteResult:
    intent_weights: dict[str, float]      # {intent_id: weight}
    channel_quota: ChannelQuota           # 各通道检索配额
    primary_intent: str                   # 权重最高的意图


@dataclass
class ChannelQuota:
    vector: int  # 向量检索返回条数
    bm25: int    # BM25 检索返回条数
    sql: int     # 结构化查询返回条数
```

---

## 5. 多通道检索编排

```python
class MultiChannelRetrieval:
    """并行执行多通道检索，汇总候选集"""
    
    def __init__(
        self,
        vector_channel: VectorSearchChannel,
        bm25_channel: BM25SearchChannel,
        sql_channel: SQLSearchChannel
    ):
        self.channels = {
            "vector": vector_channel,
            "bm25": bm25_channel,
            "sql": sql_channel
        }
    
    async def search(
        self,
        query: str,
        sub_queries: list[str],
        quota: ChannelQuota,
        filters: SearchFilters
    ) -> MultiChannelResult:
        # 并行执行
        tasks = {
            "vector": self.channels["vector"].search(query, sub_queries, quota.vector, filters),
            "bm25": self.channels["bm25"].search(query, sub_queries, quota.bm25, filters),
            "sql": self.channels["sql"].search(query, sub_queries, quota.sql, filters)
        }
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        
        channel_results = {}
        for (name, _), result in zip(tasks.items(), results):
            if isinstance(result, Exception):
                logger.error(f"Channel {name} failed: {result}")
                channel_results[name] = []
            else:
                channel_results[name] = result
        
        return MultiChannelResult(
            query=query,
            sub_queries=sub_queries,
            vector_results=channel_results["vector"],
            bm25_results=channel_results["bm25"],
            sql_results=channel_results["sql"]
        )


class VectorSearchChannel:
    """向量检索通道：Qdrant + BGE-M3"""
    
    def __init__(self, qdrant: QdrantClient, embedder: EmbeddingService):
        self.qdrant = qdrant
        self.embedder = embedder
    
    async def search(
        self,
        query: str,
        sub_queries: list[str],
        top_k: int,
        filters: SearchFilters
    ) -> list[RetrievalHit]:
        all_hits = []
        
        # 主查询 + 子查询并行嵌入
        queries = [query] + sub_queries
        embeddings = await self.embedder.embed_batch(queries)
        
        for q_emb in embeddings:
            hits = await self.qdrant.search(
                collection_name="video_chunks",
                query_vector=q_emb,
                limit=top_k,
                query_filter=self._build_filter(filters),
                with_payload=True
            )
            all_hits.extend([
                RetrievalHit(
                    chunk_id=hit.payload["chunk_id"],
                    video_id=hit.payload["video_id"],
                    segment_id=hit.payload["segment_id"],
                    content=hit.payload["content"],
                    score=hit.score,
                    source="vector",
                    metadata=hit.payload
                )
                for hit in hits
            ])
        
        # 去重（按 chunk_id）
        return self._dedupe(all_hits, top_k)


class BM25SearchChannel:
    """BM25 检索通道：Rank-BM25 本地索引"""
    
    def __init__(self, bm25_index: BM25Okapi, chunk_store: ChunkStore):
        self.bm25 = bm25_index
        self.chunk_store = chunk_store
    
    async def search(
        self,
        query: str,
        sub_queries: list[str],
        top_k: int,
        filters: SearchFilters
    ) -> list[RetrievalHit]:
        # 中文分词
        tokens = jieba.lcut(query)
        sub_tokens = [jieba.lcut(q) for q in sub_queries]
        
        all_scores = []
        for tokens_set in [tokens] + sub_tokens:
            scores = self.bm25.get_scores(tokens_set)
            all_scores.append(scores)
        
        # 加权融合：主查询 0.6 + 子查询 0.4/len
        fused = all_scores[0] * 0.6
        if len(all_scores) > 1:
            for s in all_scores[1:]:
                fused += s * (0.4 / (len(all_scores) - 1))
        
        top_indices = np.argsort(fused)[-top_k:][::-1]
        
        hits = []
        for idx in top_indices:
            chunk = await self.chunk_store.get_by_idx(idx)
            if chunk and self._match_filters(chunk, filters):
                hits.append(RetrievalHit(
                    chunk_id=chunk.id,
                    video_id=chunk.video_id,
                    segment_id=chunk.segment_id,
                    content=chunk.content,
                    score=float(fused[idx]),
                    source="bm25",
                    metadata=chunk.metadata
                ))
        return hits
```

---

## 6. RRF 融合 + ContextExpander

```python
class RRFusion:
    """Reciprocal Rank Fusion: score = Σ 1/(k + rank_i)"""
    
    K = 60  # RRF 常数
    
    def fuse(self, results: MultiChannelResult, final_k: int = 20) -> list[RetrievalHit]:
        # 收集所有 hits 并记录各通道排名
        channel_ranks = {
            "vector": {hit.chunk_id: i for i, hit in enumerate(results.vector_results)},
            "bm25": {hit.chunk_id: i for i, hit in enumerate(results.bm25_results)},
            "sql": {hit.chunk_id: i for i, hit in enumerate(results.sql_results)}
        }
        
        all_chunk_ids = set()
        for ranks in channel_ranks.values():
            all_chunk_ids.update(ranks.keys())
        
        # 计算 RRF 分数
        rrf_scores = {}
        for chunk_id in all_chunk_ids:
            score = 0.0
            for channel, ranks in channel_ranks.items():
                if chunk_id in ranks:
                    score += 1.0 / (self.K + ranks[chunk_id] + 1)
            rrf_scores[chunk_id] = score
        
        # 取 Top-K
        top_ids = sorted(rrf_scores, key=rrf_scores.get, reverse=True)[:final_k]
        
        # 合并元数据（取最高分通道的 payload）
        hits = []
        for chunk_id in top_ids:
            best_hit = None
            best_score = -1
            for channel_results in [results.vector_results, results.bm25_results, results.sql_results]:
                for hit in channel_results:
                    if hit.chunk_id == chunk_id and hit.score > best_score:
                        best_hit = hit
                        best_score = hit.score
            if best_hit:
                best_hit.score = rrf_scores[chunk_id]  # 覆盖为 RRF 分数
                hits.append(best_hit)
        
        return hits


class ContextExpander:
    """±1 chunk 扩展：检索到的 chunk 前后各扩展 1 个，保证上下文连贯。

    定位相邻 chunk 使用「同一 segment 内按 chunk_index 字典」查找，**不**依赖
    chunk_id 字符串解析——兼容 DATA-MODEL.md 中 UUID 形式的 chunk.id。规范的字典
    扩展算法见 RAG-RETRIEVAL.md §2.5 ContextExpander（all_chunks 列表注入版，便于单测/评测）；
    本文为生产装配层：经 `ChunkStore.get_by_segment_index(segment_id, neighbor_idx)` 取相邻 chunk，
    与 RAG §2.5 的 chunk_index 字典算法语义一致，仅依赖注入方式不同，避免双源漂移。"""

    def __init__(self, chunk_store: ChunkStore):
        self.chunk_store = chunk_store

    async def expand(self, hits: list[RetrievalHit], window: int = 1) -> list[RetrievalHit]:
        expanded = []
        seen = set()

        for hit in hits:
            # 当前 chunk
            if hit.chunk_id not in seen:
                expanded.append(hit)
                seen.add(hit.chunk_id)

            center_idx = hit.metadata.get("chunk_index")
            if center_idx is None:
                continue

            # 同一 segment 内按 chunk_index 前后扩展（±window）
            for offset in range(-window, window + 1):
                if offset == 0:
                    continue
                neighbor_idx = center_idx + offset
                if neighbor_idx < 0:
                    continue
                neighbor = await self.chunk_store.get_by_segment_index(
                    hit.segment_id, neighbor_idx
                )
                if neighbor and neighbor.id not in seen:
                    expanded.append(RetrievalHit(
                        chunk_id=neighbor.id,
                        video_id=neighbor.video_id,
                        segment_id=neighbor.segment_id,
                        content=neighbor.content,
                        score=hit.score * 0.8,  # 扩展 chunk 权重衰减
                        source="expanded",
                        metadata=neighbor.metadata
                    ))
                    seen.add(neighbor.id)

        return expanded
```

---

## 7. 重排序器

```python
class DeterministicReranker:
    """零延迟确定性重排：时间/来源/完整性/意图匹配度"""
    
    def rerank(self, hits: list[RetrievalHit], query: str, intent: str) -> list[RetrievalHit]:
        for hit in hits:
            score = 0.0
            
            # 1. 时间新近度（视频发布时间，越新越高）
            if hit.metadata.get("published_at"):
                days_ago = (datetime.utcnow() - hit.metadata["published_at"]).days
                score += max(0, 1 - days_ago / 365) * 0.2
            
            # 2. 来源权威性（官方/认证账号 > 普通）
            source_tier = hit.metadata.get("source_tier", 3)
            score += (4 - source_tier) / 3 * 0.15
            
            # 3. 内容完整性（chunk 长度、OCR/ASR 双覆盖）
            content_len = len(hit.content)
            score += min(content_len / 500, 1) * 0.15
            if hit.metadata.get("has_ocr") and hit.metadata.get("has_asr"):
                score += 0.1
            
            # 4. 意图匹配度（关键词命中）
            if intent in ("video_search.topic", "video_qa.detail"):
                # 需要精确关键词
                keywords = jieba.lcut(query)
                hits_kw = sum(1 for kw in keywords if kw in hit.content)
                score += min(hits_kw / max(len(keywords), 1), 1) * 0.2
            
            # 5. 片段连贯性（同一 segment 的多 chunk 连续）
            if hit.metadata.get("segment_chunk_count", 1) > 1:
                score += 0.05
            
            hit.rerank_score = score + hit.score * 0.15  # 保留少量原始分数
        
        return sorted(hits, key=lambda h: h.rerank_score, reverse=True)


class CrossEncoderReranker:
    """可选 Cross-Encoder：BGE-Reranker-v2-m3，<100ms"""
    
    def __init__(self, model_path: str = "BAAI/bge-reranker-v2-m3"):
        self.model = CrossEncoder(model_path, device="cuda")
    
    async def rerank(self, hits: list[RetrievalHit], query: str, top_k: int = 10) -> list[RetrievalHit]:
        if not hits:
            return hits
        
        # 仅对 Top-20 跑 Cross-Encoder
        candidates = hits[:min(20, len(hits))]
        pairs = [(query, hit.content) for hit in candidates]
        
        scores = await asyncio.to_thread(self.model.predict, pairs)
        
        for hit, score in zip(candidates, scores):
            hit.rerank_score = float(score)
        
        # 合并：Cross-Encoder 分数为主，其余保持原序
        reranked = sorted(candidates, key=lambda h: h.rerank_score, reverse=True)
        remaining = hits[len(candidates):]
        
        return reranked[:top_k] + remaining
```

---

## 8. MCP 工具调用集成

```python
class MCPToolRegistry:
    """MCP 工具注册与调用：检索增强生成时的外部工具"""
    
    def __init__(self):
        self.tools: dict[str, MCPTool] = {}
    
    def register(self, tool: MCPTool):
        self.tools[tool.name] = tool
    
    async def call(self, name: str, args: dict, context: ToolCallContext) -> ToolResult:
        tool = self.tools.get(name)
        if not tool:
            return ToolResult(success=False, error=f"Tool {name} not found")
        
        # 准入检查
        if not await tool.check_admission(context):
            return ToolResult(success=False, error="Admission denied")
        
        try:
            result = await tool.execute(args, context)
            await tool.record_usage(context)
            return ToolResult(success=True, data=result)
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# 预置工具
class VideoSegmentTool(MCPTool):
    """获取视频指定时间段的详细内容"""
    name = "video.get_segment"
    description = "获取视频指定时间范围内的转录文本、OCR 文字、关键帧描述"
    
    async def execute(self, args: dict, context: ToolCallContext) -> dict:
        video_id = args["video_id"]
        start_sec = args["start_sec"]
        end_sec = args["end_sec"]
        
        segments = await context.chunk_store.get_segments(video_id, start_sec, end_sec)
        return {
            "segments": [
                {
                    "start": s.start_sec,
                    "end": s.end_sec,
                    "transcript": s.transcript,
                    "ocr_texts": s.ocr_texts,
                    "frame_descriptions": s.frame_descriptions
                }
                for s in segments
            ]
        }


class VideoSearchTool(MCPTool):
    """视频库语义搜索"""
    name = "video.search"
    description = "在视频库中搜索匹配主题的视频片段"
    
    async def execute(self, args: dict, context: ToolCallContext) -> dict:
        query = args["query"]
        top_k = args.get("top_k", 5)
        
        # 复用 RAG 检索管线
        results = await context.rag_pipeline.search(query, top_k=top_k)
        return {"results": results}
```

---

## 9. 答案生成器

```python
class AnswerGenerator:
    """生成带 ChunkEvidenceID 引用的结构化答案"""
    
    def __init__(self, llm: LLMService):
        self.llm = llm
    
    async def generate(
        self,
        query: str,
        hits: list[RetrievalHit],
        intent: str,
        follow_up_context: FollowUpContext | None = None
    ) -> AnswerResult:
        
        # 构建证据上下文
        evidence_blocks = []
        for i, hit in enumerate(hits[:8]):  # 最多 8 个证据
            eid = make_evidence_id(hit.chunk_id, i)
            evidence_blocks.append(f"[{eid}] {hit.content[:500]}")
        
        evidence_text = "\n\n".join(evidence_blocks)
        
        # 意图感知的系统提示
        system_prompt = self._get_system_prompt(intent)
        
        user_prompt = f"""用户问题：{query}

检索到的证据：
{evidence_text}

要求：
1. 基于证据回答，不要编造
2. 关键结论必须标注证据 ID，格式：[EID_xxx]
3. 若证据不足，明确说明"根据现有资料无法确定"
4. 结构化输出：结论 + 关键证据 + 置信度"""
        
        response = await self.llm.chat(ChatRequest(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.2,
            max_tokens=2048,
            thinking=(intent == "video_analysis")
        ))
        
        # 解析引用
        cited_ids = self._extract_evidence_ids(response.content)
        evidence_map = {make_evidence_id(h.chunk_id, i): h for i, h in enumerate(hits[:8])}
        cited_evidence = [evidence_map[eid] for eid in cited_ids if eid in evidence_map]
        
        return AnswerResult(
            answer=response.content,
            cited_evidence=cited_evidence,
            confidence=self._estimate_confidence(cited_evidence, hits)
        )
    
    def _get_system_prompt(self, intent: str) -> str:
        prompts = {
            "video_qa": "你是视频内容问答助手，基于转录/OCR/关键帧回答事实性问题。",
            "video_search": "你是视频检索助手，帮用户定位视频中的相关片段。",
            "video_analysis": "你是视频分析专家，进行跨片段推理、对比、因果分析。需要深度思考。",
            "video_generation": "你是内容创作助手，基于视频生成脚本/笔记/大纲。",
            "meta_query": "你是视频元数据助手，提供时长/作者/发布时间等信息。"
        }
        return prompts.get(intent, prompts["video_qa"])
    
    def _extract_evidence_ids(self, text: str) -> list[str]:
        return re.findall(r"\[EID_[a-z0-9]{8}_\d{2}\]", text)
    
    def _estimate_confidence(self, cited: list, all_hits: list) -> float:
        if not cited:
            return 0.3
        # 引用覆盖率 + 证据平均分数
        coverage = len(cited) / max(len(all_hits), 1)
        avg_score = sum(h.score for h in cited) / len(cited)
        return min(0.3 + coverage * 0.4 + avg_score * 0.3, 1.0)


def make_evidence_id(chunk_id: str, index: int) -> str:
    """稳定证据 ID：chunk_id 前8位 + 索引"""
    return f"EID_{chunk_id[:8]}_{index:02d}"
```

---

## 10. 配置

```yaml
# config/intent_routing.yaml
rewriter:
  llm_confidence_threshold: 0.7
  max_sub_queries: 4
  timeout_seconds: 5

router:
  tree_path: "config/intent_tree.yaml"
  keyword_weight: 0.4
  llm_weight: 0.6
  min_intent_score: 0.05

retrieval:
  rrf_k: 60
  final_top_k: 20
  expand_window: 1
  crossencoder_enabled: true
  crossencoder_top_k: 10
  crossencoder_model: "BAAI/bge-reranker-v2-m3"

generator:
  max_evidence: 8
  evidence_max_chars: 500
  temperature: 0.2

mcp_tools:
  enabled: true
  timeout_seconds: 30
```

---

> **关联文档**：[RAG-RETRIEVAL.md](RAG-RETRIEVAL.md) · [AGENT-LOOP.md](AGENT-LOOP.md) · [MODEL-GATEWAY.md](MODEL-GATEWAY.md) · [OBSERVABILITY.md](OBSERVABILITY.md)