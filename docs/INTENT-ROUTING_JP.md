# VideoMind 意図ルーティングとクエリ書き換え設計

> 木構造の意図認識 + LLM クエリ書き換え/サブ問題分割 + マルチチャネル検索オーケストレーション + MCP ツール呼び出し統合
> 主要参考：Ragent `infra-rag/`（IntentRouter + MultiChannelRetrieval）+ vid-lens `internal/rag/`（QueryRewriter + ContextExpander）

---

## 1. 意図ルーティングの全体像

```
ユーザークエリ
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│                      QueryRewriter                              │
│  1. 同義語拡張 + エンティティ正規化                                        │
│  2. サブ問題分解（複雑なクエリ → 2〜4 個の原子的サブ問題）                       │
│  3. ルールでフォールバック（LLM 失敗時は正規表現/キーワード）                   │
└────────────────────────────────┬────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                      IntentRouter                               │
│  入力：元クエリ + サブ問題リスト                                        │
│  木構造意図設定：config/intent_tree.yaml                           │
│  並行分類 → クォータ割り当て → {intent: weight} マッピング出力                  │
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
│  検索された chunk の前後を各 1 個拡張し、文脈の連続性を保証                   │
└────────────────────────────────┬────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                      Reranker                                   │
│  1. DeterministicReranker（時間/ソース/完全性、ゼロレイテンシ）            │
│  2. CrossEncoderReranker（任意、BGE-Reranker-v2-m3、<100ms）       │
└────────────────────────────────┬────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                      AnswerGenerator                            │
│  ChunkEvidenceID による安定した引用 + MCP Tool Calling                     │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 木構造意図設定

```yaml
# config/intent_tree.yaml
version: "1.0"

intents:
  # 一次意図
  - id: "video_qa"
    name: "動画内容Q&A"
    weight: 1.0
    description: "動画の転写/OCR/キーフレームに基づいて事実的な質問に回答する"
    keywords: ["何", "どう", "なぜ", "内容", "話した", "言った"]
    children:
      - id: "video_qa.summary"
        name: "動画要約"
        weight: 0.9
        description: "動画全体/セグメント単位の要約を生成する"
        keywords: ["要約", "まとめ", "概括", "大意"]
      
      - id: "video_qa.detail"
        name: "詳細深掘り"
        weight: 0.8
        description: "特定の時点/人物/事象の詳細"
        keywords: ["何分目", "具体的", "詳細", "原文"]
      
      - id: "video_qa.quote"
        name: "原文引用"
        weight: 0.7
        description: "転写テキストを正確に引用する必要がある"
        keywords: ["原文", "引用", "文字起こし", "逐字"]

  - id: "video_search"
    name: "動画検索・位置特定"
    weight: 0.9
    description: "動画ライブラリ内で一致する区間を検索する"
    keywords: ["探す", "検索", "どこ", "位置", "時点", "区間"]
    children:
      - id: "video_search.topic"
        name: "トピック検索"
        weight: 0.9
        keywords: ["について", "関連", "トピック", "話題"]
      
      - id: "video_search.speaker"
        name: "発話者検索"
        weight: 0.7
        keywords: ["誰が言った", "発話者", "話者", "声"]
      
      - id: "video_search.visual"
        name: "映像検索"
        weight: 0.6
        keywords: ["映像", "画面", "登場", "画面表示", "字幕"]

  - id: "video_analysis"
    name: "動画の深層分析"
    weight: 0.8
    description: "区間をまたぐ推論・比較・因果分析"
    keywords: ["分析", "比較", "推論", "原因", "影響", "トレンド"]
    children:
      - id: "video_analysis.compare"
        name: "比較分析"
        weight: 0.8
        keywords: ["比較", "違い", "差異", "比べて"]
      
      - id: "video_analysis.causal"
        name: "因果推論"
        weight: 0.7
        keywords: ["なぜ", "引き起こす", "原因", "影響"]
      
      - id: "video_analysis.timeline"
        name: "タイムライン整理"
        weight: 0.7
        keywords: ["タイムライン", "順序", "プロセス", "展開"]

  - id: "video_generation"
    name: "動画からの派生コンテンツ生成"
    weight: 0.7
    description: "動画に基づいて台本/ノート/アウトラインを生成する"
    keywords: ["生成", "書く", "作成", "台本", "ノート", "アウトライン", "PPT"]
    children:
      - id: "video_generation.script"
        name: "台本リライト"
        weight: 0.8
        keywords: ["台本", "コピー", "口述原稿"]
      
      - id: "video_generation.notes"
        name: "学習ノート"
        weight: 0.7
        keywords: ["ノート", "知識点", "アウトライン", "マインドマップ"]
      
      - id: "video_generation.highlights"
        name: "ハイライト切り出し"
        weight: 0.6
        keywords: ["ハイライト", "見せ場", "切り出し", "ショート動画"]

  - id: "meta_query"
    name: "メタデータ検索"
    weight: 0.5
    description: "動画の長さ/作者/公開日時などのメタ情報"
    keywords: ["長さ", "作者", "アップロード", "公開", "タイトル", "タグ"]

# 意図 → 検索チャネル重みマッピング
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

## 3. クエリ書き換え器

```python
class QueryRewriter:
    """
    三層の書き換え戦略：
    1. LLM 書き換え（主）：同義語拡張 + エンティティ正規化 + サブ問題分解
    2. ルール書き換え（副）：正規表現 + 辞書。LLM 失敗/タイムアウト時のフォールバック
    3. 元クエリ（最下層）：元クエリを最後の一路として保持する
    """
    
    def __init__(self, llm: LLMService, config: RewriterConfig):
        self.llm = llm
        self.config = config
        self.rule_rewriter = RuleRewriter()
    
    async def rewrite(self, query: str, context: RewriteContext) -> RewriteResult:
        # 1. LLM 書き換えを試行
        llm_result = await self._llm_rewrite(query, context)
        if llm_result and llm_result.confidence > self.config.llm_confidence_threshold:
            return llm_result
        
        # 2. ルールでフォールバック
        rule_result = self.rule_rewriter.rewrite(query, context)
        if rule_result.sub_queries:
            return rule_result
        
        # 3. 元クエリ
        return RewriteResult(
            original=query,
            rewritten=query,
            sub_queries=[query],
            method="original",
            confidence=0.5
        )
    
    async def _llm_rewrite(self, query: str, context: RewriteContext) -> RewriteResult | None:
        prompt = f"""あなたは動画理解アシスタントのクエリ書き換え器です。ユーザークエリを書き換え・分解してください。

ユーザークエリ：{query}
動画コンテキスト：{context.video_title or "不明"} | {context.duration_sec or "不明"}秒

タスク：
1. 同義語拡張：専門用語・口語表現を補う
2. エンティティ正規化：人名/地名/機関名を標準化する
3. サブ問題分解：複雑なクエリを 2〜4 個の原子的サブ問題に分解する（各々が独立に検索可能）
4. 元クエリの意味を保持する

JSON を出力：
{{
  "rewritten": "書き換え後の主クエリ",
  "sub_queries": ["サブ問題1", "サブ問題2", ...],
  "entities": {{"元エンティティ": "標準化エンティティ"}},
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
    """ルールフォールバック：キーワード拡張 + 簡易分解"""
    
    SYNONYMS = {
        "動画": ["映像", "録画", "フィルム"],
        "内容": ["何を話した", "何を言った", "テーマ"],
        "要約": ["サマリ", "概括", "大意"],
        "原文": ["逐字", "文字起こし", "転写"],
        "画面": ["動画画面", "画面内容", "画面内"],
    }
    
    def rewrite(self, query: str, context: RewriteContext) -> RewriteResult:
        # 同義語拡張
        expanded = query
        for k, vs in self.SYNONYMS.items():
            if k in query:
                expanded += " " + " ".join(vs)
        
        # 簡易分解："和/或/、/," で区切られた多重意図を検出
        sub_queries = re.split(r"[和或、,，]", query)
        sub_queries = [q.strip() for q in sub_queries if len(q.strip()) > 3]
        if len(sub_queries) == 1:
            sub_queries = [query]
        
        return RewriteResult(
            original=query,
            rewritten=expanded,
            sub_queries=sub_queries[:4],  # 最大 4 個
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

## 4. 意図ルーター

```python
class IntentRouter:
    """
    木構造意図の並行分類：
    1. すべての葉意図をフラット化する
    2. 各意図のマッチスコアを並行計算する（キーワード + LLM 軽量分類）
    3. 親意図へ上方集約する（子意図の最大スコア * 親の重み）
    4. 重みを正規化し、Top-K 意図とその検索クォータを出力する
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
        # すべてのクエリテキストを結合
        all_text = " ".join([query] + sub_queries)
        
        # 1. キーワード高速マッチ（全葉ノードを並行）
        keyword_scores = self._keyword_match(all_text)
        
        # 2. LLM 軽量分類（Top-10 キーワード候補）
        top_candidates = sorted(keyword_scores.items(), key=lambda x: x[1], reverse=True)[:10]
        llm_scores = await self._llm_classify(all_text, [n for n, _ in top_candidates])
        
        # 3. スコア融合
        fused = {}
        for node_id in self.tree.nodes:
            kw = keyword_scores.get(node_id, 0)
            llm = llm_scores.get(node_id, 0)
            fused[node_id] = 0.4 * kw + 0.6 * llm
        
        # 4. 上方集約
        for node in self.tree.nodes.values():
            if node.children:
                child_max = max(fused.get(c.id, 0) for c in node.children)
                fused[node.id] = max(fused.get(node.id, 0), child_max * node.weight)
        
        # 5. 正規化 & クォータ割り当て
        total = sum(fused.values()) or 1.0
        intent_weights = {k: v / total for k, v in fused.items() if v > 0.05}
        
        # 6. 各チャネルのクォータを計算
        channel_quota = self._compute_channel_quota(intent_weights)
        
        return IntentRouteResult(
            intent_weights=intent_weights,
            channel_quota=channel_quota,
            primary_intent=max(intent_weights, key=intent_weights.get) if intent_weights else "video_qa"
        )
    
    def _keyword_match(self, text: str) -> dict[str, float]:
        scores = {}
        for node in self.tree.nodes.values():
            if not node.children:  # 葉ノードのみ
                hits = sum(1 for kw in node.keywords if kw in text)
                if hits > 0:
                    scores[node.id] = hits / len(node.keywords)
        return scores
    
    async def _llm_classify(self, text: str, candidates: list[str]) -> dict[str, float]:
        if not candidates:
            return {}
        
        prompt = f"""クエリがどの意図に属するか判定してください（複数選択可、0-1 のスコア）。

クエリ：{text}
候補意図：
{chr(10).join(f"- {c}: {self.tree.nodes[c].description}" for c in candidates)}

JSON を出力：{{"intent_id": 0.0-1.0, ...}} スコア>0.3 のもののみ出力"""
        
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
        """意図の重みで加重平均して各チャネルのクォータを得る"""
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
    channel_quota: ChannelQuota           # 各チャネルの検索クォータ
    primary_intent: str                   # 重みが最大の意図


@dataclass
class ChannelQuota:
    vector: int  # ベクトル検索の返却件数
    bm25: int    # BM25 検索の返却件数
    sql: int     # 構造化クエリの返却件数
```

---

## 5. マルチチャネル検索オーケストレーション

```python
class MultiChannelRetrieval:
    """マルチチャネル検索を並行実行し、候補集合を集約する"""
    
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
        # 並行実行
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
    """ベクトル検索チャネル：Qdrant + BGE-M3"""
    
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
        
        # 主クエリ + サブクエリを並行埋め込み
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
        
        # 重複排除（chunk_id 単位）
        return self._dedupe(all_hits, top_k)


class BM25SearchChannel:
    """BM25 検索チャネル：Rank-BM25 ローカルインデックス"""
    
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
        # 中国語のトークナイズ
        tokens = jieba.lcut(query)
        sub_tokens = [jieba.lcut(q) for q in sub_queries]
        
        all_scores = []
        for tokens_set in [tokens] + sub_tokens:
            scores = self.bm25.get_scores(tokens_set)
            all_scores.append(scores)
        
        # 加重融合：主クエリ 0.6 + サブクエリ 0.4/len
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
    
    K = 60  # RRF 定数
    
    def fuse(self, results: MultiChannelResult, final_k: int = 20) -> list[RetrievalHit]:
        # すべての hits を収集し、各チャネルの順位を記録
        channel_ranks = {
            "vector": {hit.chunk_id: i for i, hit in enumerate(results.vector_results)},
            "bm25": {hit.chunk_id: i for i, hit in enumerate(results.bm25_results)},
            "sql": {hit.chunk_id: i for i, hit in enumerate(results.sql_results)}
        }
        
        all_chunk_ids = set()
        for ranks in channel_ranks.values():
            all_chunk_ids.update(ranks.keys())
        
        # RRF スコアを計算
        rrf_scores = {}
        for chunk_id in all_chunk_ids:
            score = 0.0
            for channel, ranks in channel_ranks.items():
                if chunk_id in ranks:
                    score += 1.0 / (self.K + ranks[chunk_id] + 1)
            rrf_scores[chunk_id] = score
        
        # Top-K を取得
        top_ids = sorted(rrf_scores, key=rrf_scores.get, reverse=True)[:final_k]
        
        # メタデータをマージ（最高スコアのチャネルの payload を採用）
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
                best_hit.score = rrf_scores[chunk_id]  # RRF スコアで上書き
                hits.append(best_hit)
        
        return hits


class ContextExpander:
    """±1 chunk 拡張：検索された chunk の前後を各 1 個拡張し、コンテキストの連続性を保証する。

    隣接 chunk の特定には「同一 segment 内の chunk_index 辞書」による検索を用い、chunk_id の
    文字列解析には**依存しない**——DATA-MODEL_JP.md の UUID 形式の chunk.id と互換。規範的な辞書
    拡張アルゴリズムは RAG-RETRIEVAL_JP.md §2.5 ContextExpander（all_chunks リスト注入版、単体テスト/評価に便利）を参照；
    本文は本番のアセンブリ層：`ChunkStore.get_by_segment_index(segment_id, neighbor_idx)` を経て隣接 chunk を取得し、
    RAG §2.5 の chunk_index 辞書アルゴリズムと意味的に一致する。依存の注入方式が異なるだけで、二重ソースのドリフトを避ける。"""

    def __init__(self, chunk_store: ChunkStore):
        self.chunk_store = chunk_store

    async def expand(self, hits: list[RetrievalHit], window: int = 1) -> list[RetrievalHit]:
        expanded = []
        seen = set()

        for hit in hits:
            # 現在の chunk
            if hit.chunk_id not in seen:
                expanded.append(hit)
                seen.add(hit.chunk_id)

            center_idx = hit.metadata.get("chunk_index")
            if center_idx is None:
                continue

            # 同一 segment 内で chunk_index の前後へ拡張（±window）
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
                        score=hit.score * 0.8,  # 拡張 chunk は重みを減衰
                        source="expanded",
                        metadata=neighbor.metadata
                    ))
                    seen.add(neighbor.id)

        return expanded
```

---

## 7. 再ランキング器

```python
class DeterministicReranker:
    """ゼロレイテンシの決定論的再ランキング：時間/ソース/完全性/意図マッチ度"""
    
    def rerank(self, hits: list[RetrievalHit], query: str, intent: str) -> list[RetrievalHit]:
        for hit in hits:
            score = 0.0
            
            # 1. 時間的新しさ（動画の公開日時、新しいほど高い）
            if hit.metadata.get("published_at"):
                days_ago = (datetime.utcnow() - hit.metadata["published_at"]).days
                score += max(0, 1 - days_ago / 365) * 0.2
            
            # 2. ソースの権威性（公式/認証アカウント > 一般）
            source_tier = hit.metadata.get("source_tier", 3)
            score += (4 - source_tier) / 3 * 0.15
            
            # 3. 内容の完全性（chunk 長、OCR/ASR の二重カバレッジ）
            content_len = len(hit.content)
            score += min(content_len / 500, 1) * 0.15
            if hit.metadata.get("has_ocr") and hit.metadata.get("has_asr"):
                score += 0.1
            
            # 4. 意図マッチ度（キーワード命中）
            if intent in ("video_search.topic", "video_qa.detail"):
                # 正確なキーワードが必要
                keywords = jieba.lcut(query)
                hits_kw = sum(1 for kw in keywords if kw in hit.content)
                score += min(hits_kw / max(len(keywords), 1), 1) * 0.2
            
            # 5. 区間の連続性（同一 segment の複数 chunk が連続）
            if hit.metadata.get("segment_chunk_count", 1) > 1:
                score += 0.05
            
            hit.rerank_score = score + hit.score * 0.15  # 元スコアを少量保持
        
        return sorted(hits, key=lambda h: h.rerank_score, reverse=True)


class CrossEncoderReranker:
    """任意の Cross-Encoder：BGE-Reranker-v2-m3、<100ms"""
    
    def __init__(self, model_path: str = "BAAI/bge-reranker-v2-m3"):
        self.model = CrossEncoder(model_path, device="cuda")
    
    async def rerank(self, hits: list[RetrievalHit], query: str, top_k: int = 10) -> list[RetrievalHit]:
        if not hits:
            return hits
        
        # Top-20 のみ Cross-Encoder を実行
        candidates = hits[:min(20, len(hits))]
        pairs = [(query, hit.content) for hit in candidates]
        
        scores = await asyncio.to_thread(self.model.predict, pairs)
        
        for hit, score in zip(candidates, scores):
            hit.rerank_score = float(score)
        
        # マージ：Cross-Encoder のスコアを主とし、残りは元の順序を保持
        reranked = sorted(candidates, key=lambda h: h.rerank_score, reverse=True)
        remaining = hits[len(candidates):]
        
        return reranked[:top_k] + remaining
```

---

## 8. MCP ツール呼び出し統合

```python
class MCPToolRegistry:
    """MCP ツールの登録と呼び出し：検索拡張生成時の外部ツール"""
    
    def __init__(self):
        self.tools: dict[str, MCPTool] = {}
    
    def register(self, tool: MCPTool):
        self.tools[tool.name] = tool
    
    async def call(self, name: str, args: dict, context: ToolCallContext) -> ToolResult:
        tool = self.tools.get(name)
        if not tool:
            return ToolResult(success=False, error=f"Tool {name} not found")
        
        # アドミッション制御チェック
        if not await tool.check_admission(context):
            return ToolResult(success=False, error="Admission denied")
        
        try:
            result = await tool.execute(args, context)
            await tool.record_usage(context)
            return ToolResult(success=True, data=result)
        except Exception as e:
            return ToolResult(success=False, error=str(e))


# 組み込みツール
class VideoSegmentTool(MCPTool):
    """動画の指定時間帯の詳細内容を取得する"""
    name = "video.get_segment"
    description = "動画の指定時間範囲内の転写テキスト、OCR 文字、キーフレーム記述を取得する"
    
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
    """動画ライブラリのセマンティック検索"""
    name = "video.search"
    description = "動画ライブラリ内で主題に一致する動画区間を検索する"
    
    async def execute(self, args: dict, context: ToolCallContext) -> dict:
        query = args["query"]
        top_k = args.get("top_k", 5)
        
        # RAG 検索パイプラインを再利用
        results = await context.rag_pipeline.search(query, top_k=top_k)
        return {"results": results}
```

---

## 9. 回答生成器

```python
class AnswerGenerator:
    """ChunkEvidenceID 引用付きの構造化回答を生成する"""
    
    def __init__(self, llm: LLMService):
        self.llm = llm
    
    async def generate(
        self,
        query: str,
        hits: list[RetrievalHit],
        intent: str,
        follow_up_context: FollowUpContext | None = None
    ) -> AnswerResult:
        
        # 証拠コンテキストを構築
        evidence_blocks = []
        for i, hit in enumerate(hits[:8]):  # 最大 8 個の証拠
            eid = make_evidence_id(hit.chunk_id, i)
            evidence_blocks.append(f"[{eid}] {hit.content[:500]}")
        
        evidence_text = "\n\n".join(evidence_blocks)
        
        # 意図を考慮したシステムプロンプト
        system_prompt = self._get_system_prompt(intent)
        
        user_prompt = f"""ユーザーの質問：{query}

検索された証拠：
{evidence_text}

要件：
1. 証拠に基づいて回答し、捏造しない
2. 重要な結論には必ず証拠 ID を付与する。形式：[EID_xxx]
3. 証拠が不足する場合は「現有資料では確定できない」と明示する
4. 構造化出力：結論 + 主要な証拠 + 信頼度"""
        
        response = await self.llm.chat(ChatRequest(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.2,
            max_tokens=2048,
            thinking=(intent == "video_analysis")
        ))
        
        # 引用を解析
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
            "video_qa": "あなたは動画内容Q&Aアシスタントで、転写/OCR/キーフレームに基づいて事実的な質問に回答します。",
            "video_search": "あなたは動画検索アシスタントで、動画内の関連区間の特定を支援します。",
            "video_analysis": "あなたは動画分析の専門家で、区間をまたぐ推論・比較・因果分析を行います。深い思考が必要です。",
            "video_generation": "あなたはコンテンツ制作アシスタントで、動画に基づいて台本/ノート/アウトラインを生成します。",
            "meta_query": "あなたは動画メタデータアシスタントで、長さ/作者/公開日時などの情報を提供します。"
        }
        return prompts.get(intent, prompts["video_qa"])
    
    def _extract_evidence_ids(self, text: str) -> list[str]:
        return re.findall(r"\[EID_[a-z0-9]{8}_\d{2}\]", text)
    
    def _estimate_confidence(self, cited: list, all_hits: list) -> float:
        if not cited:
            return 0.3
        # 引用カバレッジ率 + 証拠の平均スコア
        coverage = len(cited) / max(len(all_hits), 1)
        avg_score = sum(h.score for h in cited) / len(cited)
        return min(0.3 + coverage * 0.4 + avg_score * 0.3, 1.0)


def make_evidence_id(chunk_id: str, index: int) -> str:
    """安定した証拠 ID：chunk_id の先頭8文字 + インデックス"""
    return f"EID_{chunk_id[:8]}_{index:02d}"
```

---

## 10. 設定

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

> **関連ドキュメント**：[RAG-RETRIEVAL_JP.md](RAG-RETRIEVAL_JP.md) · [AGENT-LOOP_JP.md](AGENT-LOOP_JP.md) · [MODEL-GATEWAY_JP.md](MODEL-GATEWAY_JP.md) · [OBSERVABILITY_JP.md](OBSERVABILITY_JP.md)
