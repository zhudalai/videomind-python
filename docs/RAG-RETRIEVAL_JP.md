# VideoMind ハイブリッド検索・RAG パイプライン設計

> 検索プリミティブ連鎖：HybridRetriever（ベクトル+BM25）→ RRFusion（K=60）→ ContextExpander（±1）→ Rerank（BGE-Reranker-v2-m3）
> 主要参考：vid-lens `internal/service/chat.go` + Ragent `bootstrap/rag/retrieve/`

> **本書の位置づけ**：検索**プリミティブとアルゴリズムの詳解**（ベクトル／語彙素融合、RRF の数学的導出、コンテキスト拡張、再ランカーの選定、オフライン評価フレームワーク）。
> **権威ある定義は他書参照**：意図ツリー / `IntentRouter` / マルチチャネル編成入口 `MultiChannelRetrieval` / `evidence_id` / `[EID_xxx]` 引用スタイル / フロントエンドの引用パース —— [INTENT-ROUTING_JP.md](./INTENT-ROUTING_JP.md) を参照。評価シェル `RetrievalPipeline` —— [OBSERVABILITY_JP.md](./OBSERVABILITY_JP.md) rag-eval を参照。本書では再述せず、別途議論もしない。二重ソースのドリフトを避けるためである。
> 呼び出しチェーン（権威）：`IntentRouter → MultiChannelRetrieval → HybridRetriever(+SqlRetriever) → RRF / ContextExpander / Rerank → AnswerGenerator`。

---

## 1. 検索パイプライン全体像（アルゴリズムビュー）

> 編成入口、意図ルーティング、回答生成と引用レンダリングの権威ある定義は [INTENT-ROUTING_JP.md](./INTENT-ROUTING_JP.md) を参照。本図は検索アルゴリズムスタックのみを示す。

```text
ユーザークエリ（INTENT の IntentRouter → MultiChannelRetrieval から）
      │
      ▼
┌──────────────────────────────────────────────────────────┐
│  HybridRetriever —— ベクトル + BM25 単一融合チャネル（本書 §2.3）│
│  ┌────────────────┐      ┌──────────────────┐             │
│  │  VectorSearch   │      │    BM25Search    │             │
│  │   (Qdrant)      │      │ (Rank-BM25 メモリ) │             │
│  └────────┬────────┘      └────────┬─────────┘             │
└───────────┼─────────────────────────┼─────────────────────┘
            └───────────┬─────────────┘
                        ▼
┌──────────────────────────────────────────────────────────┐
│  RRFusion (K=60): score = Σ 1/(K + rank_i + 1)  →  TopK   │
│  （0-based；権威ある公式は INTENT-ROUTING §6 を参照）        │
└──────────────────────┬───────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────┐
│  ContextExpander (±1 chunk、chunk_index 辞書により拡張)     │
│  （本書 §2.5；INTENT §6 と同一実装）                         │
└──────────────────────┬───────────────────────────────────┘
                       ▼
┌──────────────────────────────────────────────────────────┐
│  Rerank                                                    │
│   · DeterministicReranker（モデルなし、重みは INTENT §7 参照）│
│   · CrossEncoderReranker（BGE-Reranker-v2-m3、本書 §2.6）   │
└──────────────────────┬───────────────────────────────────┘
                       ▼
   結果は INTENT の AnswerGenerator に返す
   [EID_xxx] 引用を生成（権威は INTENT make_evidence_id を参照）
```

---

## 2. 中核コンポーネント詳細設計

### 2.1 QueryRewrite（クエリ書き換え + 分割）

```python
class QueryRewriter:
    REWRITE_PROMPT = """
    あなたはクエリ書き換えの専門家です。ユーザーの元の質問と対話履歴が与えられます。次を行ってください：
    1. 照応を解消し、省略された情報を補い、独立した完全な質問を生成する
    2. 元の質問に複数のサブ質問が含まれる場合は、1-3 個のサブ質問に分割する
    3. JSON のみを出力する：{"rewritten": "...", "sub_questions": ["...", "..."]}
    
    対話履歴：{history}
    元の質問：{query}
    """
    
    async def rewrite(self, query: str, history: list[Message]) -> RewriteResult:
        # 1. 用語の正規化（同義語マッピングテーブル）
        normalized = self.term_mapper.normalize(query)
        
        # 2. LLM による書き換え
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
            # 3. ルールによるフォールバック：句読点で分割
            sub_qs = self._rule_split(normalized)
            return RewriteResult(
                original=query,
                rewritten=normalized,
                sub_questions=sub_qs
            )
    
    def _rule_split(self, text: str) -> list[str]:
        # 中英句読点で分割し、意味の完全性を保持
        parts = re.split(r'[？?。！!；;]', text)
        return [p.strip() for p in parts if p.strip()]
```

---

### 2.2 意図識別 —— 権威は INTENT-ROUTING_JP.md を参照

意図ツリー（`video_qa / video_search / video_analysis / video_generation / meta_query`、各サブ意図とチャネルクォータを含む）と `IntentRouter`（0.4 キーワード + 0.6 LLM 融合 + チャネルクォータ）の**権威ある定義は [INTENT-ROUTING_JP.md §意図ツリー + IntentRouter](./INTENT-ROUTING_JP.md)** にあり、二重ソースのドリフトを避けるため本書では再述しない。以下 §2.3 の `HybridRetriever` は、まさにこの意図ルーティング編成に消費される検索プリミティブの一つである。

> 意図分類の**権威ある実装は [INTENT-ROUTING_JP.md](./INTENT-ROUTING_JP.md) の `IntentRouter`** である（0.4 キーワード + 0.6 LLM 融合 + チャネルクォータ）。早期の `IntentClassifier` を置き換えるものであり（`MIN_SCORE` / `MAX_INTENTS` などのクォータロジックは `IntentRouter` に統合済み）、二重ソースのドリフトを避けるため本節では定義を繰り返さない。

---

### 2.3 HybridRetriever（ベクトル + BM25 単一融合チャネル）

> マルチチャネル編成入口 `MultiChannelRetrieval`（HybridRetriever + SqlRetriever を集約し、RRF / ContextExpander / 再ランキングを実行）の権威ある定義は [INTENT-ROUTING_JP.md §MultiChannelRetrieval](./INTENT-ROUTING_JP.md) にある。本節では、それに消費される単一融合チャネルのプリミティブのみを記述する。

```python
class HybridRetriever:
    def __init__(self):
        self.channels: list[SearchChannel] = [
            VectorSearchChannel(top_k=50),
            BM25SearchChannel(top_k=50),
            # GraphSearchChannel(top_k=30),  # 予約
        ]
        self.post_processors: list[SearchResultPostProcessor] = [
            DeduplicationProcessor(),
            ThresholdFilterProcessor(min_score=0.3),
            DiversitySamplingProcessor(diversity_factor=0.3),
        ]
    
    async def retrieve(self, ctx: RetrievalContext) -> list[SearchResult]:
        # 1. 有効化された全チャネルを並列実行
        enabled = [c for c in self.channels if c.is_enabled(ctx)]
        channel_results = await asyncio.gather(*[
            c.search(ctx) for c in enabled
        ])
        
        # 2. 結果をマージ（チャネル由来の情報を保持）
        merged = []
        for ch_name, results in zip([c.name for c in enabled], channel_results):
            for r in results:
                r.channel = ch_name
                merged.append(r)
        
        # 3. ポストプロセッサチェーン
        for processor in self.post_processors:
            if processor.is_enabled(ctx):
                merged = processor.process(merged, channel_results, ctx)
        
        return merged
```

**SearchChannel インターフェース**（ストラテジパターン）：
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
        # 各サブ質問に対してベクトルを生成して検索
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
        # BM25 インデックスをロード／構築（メモリキャッシュ）
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

### 2.4 RRFusion（逆数ランク融合）

```python
class RRFusion:
    def __init__(self, k: int = 60, final_top_k: int = 20):
        self.k = k
        self.final_top_k = final_top_k
    
    def fuse(self, channel_results: list[SearchResult]) -> list[SearchResult]:
        # chunk_id でグループ化し、各チャネルのランクを収集
        by_chunk: dict[UUID, dict[str, int]] = defaultdict(dict)
        
        # 各チャネルの結果にランクを割り当て
        by_channel = defaultdict(list)
        for r in channel_results:
            by_channel[r.channel].append(r)
        
        for ch_name, results in by_channel.items():
            for rank, r in enumerate(results):
                by_chunk[r.chunk_id][ch_name] = rank  # 0-based（INTENT §6 と整合）
        
        # RRF スコアを計算
        fused = []
        for chunk_id, ranks in by_chunk.items():
            rrf_score = sum(1.0 / (self.k + rank + 1) for rank in ranks.values())  # score = Σ 1/(K + rank + 1)、0-based
            # いずれかのチャネルの内容／メタデータを取得
            sample = next(r for r in channel_results if r.chunk_id == chunk_id)
            fused.append(SearchResult(
                chunk_id=chunk_id,
                content=sample.content,
                score=rrf_score,
                metadata=sample.metadata,
                channel="rrf",
                channel_ranks=ranks
            ))
        
        # ソートして TopK を取得
        fused.sort(key=lambda x: x.score, reverse=True)
        return fused[:self.final_top_k]
```

---

### 2.5 ContextExpander（コンテキスト拡張）

```python
class ContextExpander:
    def __init__(self, expand_window: int = 1):
        self.expand_window = expand_window  # ±N 個の隣接 chunk
    
    def expand(self, results: list[SearchResult], all_chunks: list[Chunk]) -> list[SearchResult]:
        """前方／後方へそれぞれ expand_window 個の chunk を拡張し、コンテキストの連続性を保証"""
        # chunk_index → chunk のマッピングを構築
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
            
            # 隣接 chunk を拡張
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
                            score=r.score * 0.8,  # 拡張 chunk は重みを下げる
                            metadata={"chunk_index": neighbor.chunk_index, "expanded": True},
                            channel="expanded"
                        ))
        
        return expanded
```

---

### 2.6 Rerank（再ランキング）

```python
class Reranker(Protocol):
    def rerank(self, query: str, results: list[SearchResult]) -> list[SearchResult]: ...


# ↓ DeterministicReranker（モデルを使わない決定論的再ランキング）の実装は INTENT-ROUTING §7 の単一権威に集約済み：
#   from core.intent.rerank import DeterministicReranker
# 重み：時間的新しさ 0.2 / ソースの権威性 0.15 / 内容の完全性 0.15 / 意図マッチ度 0.2（詳細は INTENT-ROUTING §7）。
# 本書ではアルゴリズムの動機説明のみを残し、実装は繰り返さない。二重ソースのドリフトを避けるためである。


class CrossEncoderReranker:
    """Cross-Encoder 再ランキング（オプション。精度はより高いが追加 GPU が必要）"""
    
    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3", device: str = "cuda"):
        self.model = CrossEncoder(model_name, device=device, max_length=512)
    
    def rerank(self, query: str, results: list[SearchResult]) -> list[SearchResult]:
        pairs = [(query, r.content[:512]) for r in results]
        scores = self.model.predict(pairs, batch_size=16)
        
        for r, score in zip(results, scores):
            r.cross_encoder_score = float(score)
            r.rerank_score = 0.7 * r.rerank_score + 0.3 * score  # 確定的スコアと融合
        
        results.sort(key=lambda x: x.rerank_score, reverse=True)
        return results
```

---

### 2.7 AnswerGenerator（回答生成 + 引用追跡）

**証拠 ID の安定した引用アンカー**（権威は INTENT-ROUTING_JP.md `make_evidence_id` を参照）：

> 統一フォーマット：`evidence_id = EID_{chunk_id[:8]}_{idx:02d}`。引用の表記は `[EID_xxx]`。フロントエンドのパース正規表現は `\[EID_[a-z0-9]{8}_\d{2}\]`。
> 本書では `make_evidence_id` の定義を繰り返さない。AnswerGenerator は INTENT-ROUTING の実装をそのまま再利用し、二重ソースのドリフトを避ける。

**Prompt 構築**：
```python
class AnswerGenerator:
    SYSTEM_PROMPT = """
    あなたは動画コンテンツ分析アシスタントです。提供された証拠フラグメントに基づいてユーザーの質問に回答します。
    
    規則：
    1. 証拠に含まれる情報のみで回答し、捏造してはならない
    2. すべての結論に証拠を引用すること。フォーマット：[EID_xxx]
    3. 証拠にはタイムスタンプが含まれるため、回答ではおおよその時間範囲を併記できる
    4. 証拠が不足する場合は「既存の内容からは判断できない」と明示する
    5. Markdown フォーマットで構造化出力する
    """
    
    async def generate(self, query: str, results: list[SearchResult]) -> AnswerStream:
        # 証拠ブロックを構築
        evidence_blocks = []
        for i, r in enumerate(results):
            eid = make_evidence_id(r.chunk_id, i)  # INTENT-ROUTING 由来：EID_{chunk_id[:8]}_{idx:02d}
            evidence_blocks.append(f"""
    [{eid}] [{ms_to_ts(r.metadata.get('start_ms', 0))}-{ms_to_ts(r.metadata.get('end_ms', 0))}]
    {r.content[:800]}
    """)
        
        context = "\n---\n".join(evidence_blocks)
        
        prompt = f"""
    ユーザーの質問：{query}
    
    証拠フラグメント：
    {context}
    
    上記の証拠に基づいて回答してください。各結論の後には [EID_xxx] のような引用番号を付記すること。
    """
        
        # ストリーミング生成
        async for chunk in self.llm.stream_chat([
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ]):
            yield chunk
```

**フロントエンドでの引用カードのレンダリング**：
```typescript
// フロントエンドで引用番号をパースし、クリック可能なカードをレンダリング
const parseCitations = (markdown: string) => {
  const citationRegex = /\[EID_[a-z0-9]{8}_\d{2}\]/g;  // INTENT-ROUTING make_evidence_id と整合
  // クリック可能なコンポーネントに置き換え、クリックで動画の該当タイムスタンプへジャンプ
};
```

---

## 3. オフライン評価の統合（rag-eval スタイル）

```python
class RetrievalEvaluator:
    """CI 統合：検索パイプラインの変更ごとに自動でベースライン比較を実行"""
    
    async def evaluate(self, dataset_version: str, config_name: str) -> EvalReport:
        # 1. クローズドテストセットをロード（Token 認証が必要）
        dataset = await self.load_dataset(dataset_version, split="test")
        
        # 2. 証拠を凍結（PostgreSQL/Qdrant からサンプリング → 正規化 JSON → SHA256）
        frozen_evidence = await self.freeze_evidence(dataset)
        
        # 3. パイプラインを実行
        predictions = []
        for sample in dataset:
            ctx = RetrievalContext(
                query=sample.query,
                media_id=sample.media_id,
                sub_questions=[sample.query],  # 簡略化
                embedder=self.embedder
            )
            results = await self.pipeline.retrieve(ctx)
            predictions.append(self._format_prediction(results))
        
        # 4. 指標を計算
        metrics = {
            "recall@5": self._recall_at_k(dataset, predictions, 5),
            "recall@10": self._recall_at_k(dataset, predictions, 10),
            "mrr": self._mrr(dataset, predictions),
            "latency_p50": self._latency_percentile(predictions, 50),
            "latency_p95": self._latency_percentile(predictions, 95),
            "no_result_rate": self._no_result_rate(predictions)
        }
        
        # 5. 単変数アブレーション（config_name と baseline のみ比較）
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

## 4. 主要設定

```yaml
# config/rag_pipeline.yaml
rag:
  query_rewrite:
    enabled: true
    model: "qwen2.5-7b"
    temperature: 0.1
    max_sub_questions: 3
  
  # 意図ツリー / IntentRouter の設定は INTENT-ROUTING_JP.md で一元管理。ここでは管理せず、二重ソースを避ける。
  
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
      # 重み（時間的新しさ 0.2 / ソースの権威性 0.15 / 内容の完全性 0.15 / 意図マッチ 0.2）は INTENT-ROUTING §7 を参照
    cross_encoder:
      enabled: false  # 追加 GPU が必要な場合に有効化
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

> **関連ドキュメント**：[ARCHITECTURE_JP.md](ARCHITECTURE_JP.md) · [VIDEO-PIPELINE_JP.md](VIDEO-PIPELINE_JP.md) · [AGENT-LOOP_JP.md](AGENT-LOOP_JP.md) · [INTENT-ROUTING_JP.md](INTENT-ROUTING_JP.md) · [OBSERVABILITY_JP.md](OBSERVABILITY_JP.md)
