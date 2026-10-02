# プロジェクト実装と計画の対照 / Decision Log

> この文書は 2 つの問いに答えます：
> 1. **実際の実装と `docs/` の設計計画との違い** — §2 の各サブシステム対照表を参照
> 2. **変更の理由と意思決定プロセス** — §3 の意思決定履歴（D-α / D-β / P2-1 / P2-4 / P2-5 およびその他の修正）を参照
>
> - 意思決定の命名体系：`D-α` リコール深度の階層化、`D-β` rewriter 閾値、`P2-1` cross-encoder rerank opt-in、`P2-4` チャネル単位診断、`P2-5` 設計段階
> - 権威ある情報源：コード docstring、commit message、`docs/superpowers/specs/`、評価 JSON、`~/.claude/projects/.../memory/`
> - 最終更新：2026-08-07
> - 関連ドキュメント：[ARCHITECTURE_JP.md](ARCHITECTURE_JP.md) · [RAG-RETRIEVAL_JP.md](RAG-RETRIEVAL_JP.md) · [AGENT-LOOP_JP.md](AGENT-LOOP_JP.md) · [INTENT-ROUTING_JP.md](INTENT-ROUTING_JP.md) · [MODEL-GATEWAY_JP.md](MODEL-GATEWAY_JP.md)

---

## 1. 背景と目的 / Background

`docs/` 配下の設計文書（RAG-RETRIEVAL.md / AGENT-LOOP.md / INTENT-ROUTING.md / MODEL-GATEWAY.md）は、システムの**理想形**を描いています：三チャネル検索、CrossEncoder 融合重み、エビデンスのタイムスタンプ + jieba 類似度検証、MCP ツール呼び出し、Checkpoint 二層ストレージなど。しかし、その後の検索品質チューニング（D 段階）とクロス動画分析の反復のなかで、実装は**設計から逸脱**したいくつかの取捨選択を着地させました：階層的リコール、順序修正、cross-encoder 純スコア再ランキング、集合によるハルシネーション防止、並行検索など。

これらの取捨選択は場当たり的ではありません —— その一つひとつが**単変量実験 + 評価エビデンス + 明示的な意思決定**に対応しています。本文書は「計画は何と言ったか → 実際に何を作ったか → なぜそう変えたか → 何のエビデンスで決めたか → 今コードはどこにあるか」を一件ずつ釘付けにし、将来「コードと文書がそれぞれ別のことを言う」事態を防ぎます。

設計文書に描かれているが**未実装**の項目は §4 に一括して収めています。

---

## 2. 計画 vs 実装の対照 / Plan vs. Implementation

### 2.1 RAG 検索パイプライン（RAG-RETRIEVAL.md §2 + INTENT-ROUTING.md §5–7）

| 次元 | 計画（docs） | 実際（コード） | 差異の性質 |
|---|---|---|---|
| オーケストレーション入口 | `MultiChannelRetrieval` が Vector + BM25 + **SQL** の三チャネルを集約 | `HybridRetriever` は Vector + BM25 の**二チャネルのみ** | SQL チャネル未実装（§4 参照） |
| RRF 定数 K | 60 | 60 | 一致 |
| リコール / 下流供給の深さ | チャネル `top_k=50`、`final_top_k=20` の一括方式 | リコール `recall_k=60` と下流供給 `top_k=12` を**階層化**（`recall_k=None` で旧挙動に退化） | ★ D-α（§3.1） |
| expand 順序 | 全体図では `ContextExpander` が `Rerank` の**前** | `search → rerank → expand`（rerank が expand より先） | ★ 順序修正（§3.6） |
| expand 隣接スコア | `score * 0.8` で重み下げ | `score=0.0` で末尾へ、**rerank に参加しない** | ★ 順序修正（§3.6） |
| 再ランキングバックエンド | `DeterministicReranker`（固定重み）+ 任意の `CrossEncoder`（`0.7*det + 0.3*ce` 融合） | `rerank_provider` の三档 `off / api / local`、**純 cross-encoder スコア**、融合なし | ★ P2-1（§3.3） |
| Deterministic 重み | 時間的新しさ 0.2 / ソースの権威性 0.15 / 内容の完全性 0.15 / 意図一致 0.2 | `position 0.3 + source 0.2 + original 0.5` の三段階線形加重 | 重みの簡素化 |
| cross-encoder 融合 | `rerank_score = 0.7*det + 0.3*ce` | 純 `relevance_score`、query/media をまたいで **min-max 正規化** | ★ P2-1 設計変更（§3.3） |
| api rerank `top_n` | `crossencoder_top_k=10` | `RERANK_TOP_N=60` が **recall に追随**（`top_n=min(60, len)`） | ★ D-α（§3.1） |
| BM25 トークナイズ | `jieba.lcut` | `jieba + 2-gram` | ★ CJK 修正（§3.7） |
| 後処理チェーン | `Deduplication / ThresholdFilter(0.3) / DiversitySampling` | 明示的な後処理チェーンなし | 未実装（§4 参照） |
| `HybridRetriever` キャッシュ | 検索のたびに `select(Chunk)` で BM25 を再構築 | プロセス内 `_RETRIEVER_CACHE` を media_id 単位でシングルトン化、TTL なし | 性能最適化 |
| `.env` rerank_provider | — | `.env` は実際に `api` を有効化；`config.py`/`.env.example` の既定は `off` | 実行時設定 |

### 2.2 AgentLoop（AGENT-LOOP.md）

| 次元 | 計画（docs） | 実際（コード） | 差異の性質 |
|---|---|---|---|
| 最大ラウンド数 | `MAX_ROUNDS = 2`（固定定数） | `loop.run(..., max_rounds=2)` が既定；API/フロントエンドは `le=3` で引き上げ可能 | ★ 910695a（§3.5） |
| Planner サブタスク数 | 1–5（`tasks[:5]`） | `MAX_TASKS = 5` | 一致 |
| Executor が LLM に供給する件数 | `top_k=10`（単一動画） | `LLM_CONTEXT_TOP_K=12`、リコール `recall_k=60` | ★ D-α（§3.1） |
| Executor の検索方式 | `retrieve_by_time_range` または `retrieve(top_k=10)` の単一動画 | `rag_search` が **M 動画をまたいで `asyncio.gather` で並行** | ★ マルチ動画 + 並行（§3.2） |
| `media_ids` | 単一動画 `ctx.media_id` | `state.media_ids` リスト、フロントエンド `max_length=4` | ★ 910695a（§3.5） |
| エビデンスの厳格検証 | `EvidenceVerifier`：タイムスタンプ範囲 + **jieba 内容類似度 0.7** + source 分流（asr/ocr/frame） | 純関数：タイムスタンプ範囲 + **content 非空** | ★ 簡素化（§3.8） |
| ハルシネーション防止機構 | Critic が `EvidenceVerifier.verify_all` を統合 | `conclusion.evidence_ids ⊆ retrieved_evidence_ids` の**集合包含**検証 | ★ 設計変更（§3.8） |
| Checkpoint ストレージ | PostgreSQL 真のソース + **Redis ホットキャッシュ**、`resume_analysis` 復帰入口 | `_checkpoint` が直接 PostgreSQL に書き込み（`AgentCheckpoint` テーブル）；Redis ホットキャッシュ基盤は `infrastructure/cache/redis.py` に構築済み | 部分実装（§4） |
| `_run_agent_loop` の統合 | docs では `AgentLoop.run` 内部が checkpoint を自前管理 | 本番経路は `routes/agent.py` で編成、**単一 DB セッションが全ラウンドを貫通**、段階ごとに checkpoint を永続化 | 実装上の注記 |

### 2.3 意図ルーティングとクエリ書き換え（INTENT-ROUTING.md）

| 次元 | 計画（docs） | 実際（コード） | 差異の性質 |
|---|---|---|---|
| 意図ツリーの出所 | `config/intent_tree.yaml` 外部ファイル | コード内 `DEFAULT_INTENT_TREE` 定数 | 実装上の選択（ファイル依存を一つ削減） |
| ルーティング融合重み | kw 0.4 + LLM 0.6 | `kw_weight=0.4`（kw 0.4 + LLM 0.6） | 一致 |
| チャネルクォータ基数 | 60 | 60 | 一致 |
| チャネル重み表 | `intent_channel_weights`（yaml） | `DEFAULT_CHANNEL_WEIGHTS`（コード定数） | 一致 |
| QueryRewriter `confidence_threshold` | 0.7（`intent_routing.yaml`） | 0.7（D-β ロールバック後の確定値） | ★ D-β（§3.4） |
| QueryRewriter `thinking/reasoning` | `thinking=False` | `reasoning=False`（思考チェーンをオフ） | ★ 66a1864（§3.9） |
| QueryRewriter `db` の透過 | 言及なし | `db` を `RoutingLLMService.chat` へ透過し課金を永続化 | ★ d4692e1（§3.9） |
| MCP ツール呼び出し | §8 で `MCPToolRegistry` + `VideoSegmentTool` + `VideoSearchTool` を設計 | **未実装** | 未実装（§4） |

### 2.4 モデルゲートウェイ（MODEL-GATEWAY.md）

| 次元 | 計画（docs） | 実際（コード） | 差異の性質 |
|---|---|---|---|
| 三態サーキットブレーカー | CLOSED → OPEN → HALF_OPEN | 同じ | 一致 |
| `ChatRequest.reasoning` の透過 | — | 透過：`reasoning` → `body {"reasoning":{"enabled":...}}` | ★ 66a1864（§3.9） |
| サーキットブレーカーの失敗閾値 | — | `failure_threshold=3` | 一致 |
| `llm_first_packet_timeout_s` | — | 設定は存在（10.0）、現在メイン経路では未消費 | §4 参照 |

### 2.5 動画パイプライン OCR（VIDEO-PIPELINE.md §2.4）

| 次元 | 計画（docs） | 実際（コード） | 差異の性質 |
|---|---|---|---|
| OCR バックエンド | PaddleOCR ローカル単一パス | `OCR_PROVIDER=local\|api` の二重パス：local=PaddleOCR / api=ocr.space リモート | ★ §3.12（py3.14 に paddle wheel なしの解決） |
| `paddleocr`/`paddlepaddle` 依存の位置 | メイン実行時依存 | `[project.optional-dependencies].ocr` extras へ移動 | ★ §3.12（py3.14 で uv sync の全体解決が失敗する問題の解決） |
| api パス端点 | — | ocr.space `POST /parse/image`（`OCR_API_BASE_URL` 内部で `/parse/image` を結合） | §3.12 |
| api エンジン/言語 | — | `OCR_API_ENGINE` 1/2/3、`OCR_API_LANGUAGE` 三文字コード（engine2/3 は `auto` 対応） | §3.12 |
| フレーム重複排除 | 毎フレーム認識 | phash 重複排除：同一 phash のフレームは `[duplicate frame]` を付して認識しない（local/api 共通の `_download_and_dedup`） | §3.12 |
| `ocr_task` の例外デグレード | — | 三档：`NotImplementedError`(known-skip は静黙) / `ImportError`+その他 Exception(Error 級で真因を露出、それでも INDEX へデグレード) | ★ §3.12（ImportError の静黙飲み込みを修正） |
| `frame_ocr.model_name` | — | `paddle-ocr`（local）/ `ocr.space`（api） | §3.12 |

---

## 3. 意思決定史 / Decision Log

各意思決定は **背景 → 決定 → 評価エビデンス → 最終状態 → コード位置** を含みます。★ 付きは設計計画から逸脱した重要決定です。

### 3.1 D-α：リコール深度の階層化　★★

**背景** — P2-4 のチャネル単位診断（`diag_retrieval.py`、`top_k=100`）により、2 件の hard query の gold は「検索失敗」ではなく**検索深度による打ち切り**であることが判明しました：

| qid | gold RRF rank | 命中チャネル |
|---|---|---|
| ja_002 | 25 | Vector 31 / BM25 84 / RRF 25 |
| ja_004 | 57 | Vector 26 / BM25 MISS / RRF 57 |

三層の `top_k` は実値が異なります：本番 `executor.py TOP_K_PER_VIDEO=5`（rank 25/57 はリコールにすら入れない）、`run_eval.py top_k=20`（rank≤20 のみカバー）、`diag top_k=100`（ここで初めて gold が見える）。**単純な「5→20」ではどの hard も救えない**（25、57 はいずれも > 20）。さらに隠れた殺し手があります：api rerank `top_n=min(rerank_top_n=20, len)` は広いリコールに対して cross-encoder top 20 しか返しません —— gold は弱関連ゆえ top 20 に入れず、広いリコールがプールに引き入れても rerank が再び追い出すのです。

**決定** — `pipeline.search` の `top_k`（リコール深度と下流供給件数を一括で担う）を**二つのパラメータに分割**：
- `recall_k`：リコール/RRF の広窓（新パラメータ、`None` で `top_k` に退化、後方互換）
- `top_k`：rerank+expand 後に下流へ供給する最終件数

`executor.py` は二つの定数に分割：`RECALL_TOP_K=60`（gold rank 25 と 57 をカバー）、`LLM_CONTEXT_TOP_K=12`。同時に `RERANK_TOP_N 20→60` とし、api rerank が全 60 件を精排し、広いリコールで入った gold が rerank top-20 で切られるのを防ぎます。

**評価エビデンス** — `run_eval.py --providers off,api --recall-k 60 --top-k 20`、対照は P2-4 ベースライン（`api: raw_MRR_hard=0.0303, ev_MRR_hard=0.3333`）。設計文書 §5 の成功判定：`ev_recall@5_hard` と `ev_mrr_hard`（top_k=20 档）がベースラインより向上、ja_002/ja_004 が evidence top-20 に入る（`ev_hit_rank` が None でなくなる）；退化防止判定：normal query の macro MRR が低下しないこと。二口径の理由：executor の本番 LLM 供給は 12、eval は 20 で P2-4 ベースラインと比較可能。

**最終状態** — D 段階の品質データが、リコール水位こそボトルネックであることを証明し、ja_002/ja_004 が候補プールに入った → 品質のために B 段階（ローカル rerank + GPU）を起動する必要はない。

**コード位置** —
- [src/videomind/core/rag/pipeline.py:48-89](src/videomind/core/rag/pipeline.py#L48-L89) `search()` に `recall_k: int | None = None` を追加、`recall = recall_k if recall_k is not None else top_k`
- [src/videomind/core/agent_loop/executor.py:37-41](src/videomind/core/agent_loop/executor.py#L37-L41) `RECALL_TOP_K = 60` / `LLM_CONTEXT_TOP_K = 12`
- [.env:101](.env#L101) `RERANK_TOP_N=60`（`.env.example` と同期）
- 設計上の主要参考：[superpowers/specs/2026-08-04-retrieval-depth-tuning-design.md](superpowers/specs/2026-08-04-retrieval-depth-tuning-design.md)
- commit：`6059abc` / `0ebf246`（`run_eval.py --recall-k` CLI）

### 3.2 マルチ動画の並行検索　★

**背景** — 旧 Executor は `state.media_ids` を逐次走査し、N タスク × M 動画 × L 検索遅延 → ウォールクロック `O(N×M×L)`。三者/四者比較タスクへ拡張すると逐次コストが線形に増幅する。

**決定** — M 軸を `asyncio.gather` で並行化：各 `_RagRetriever.search` が**独立した `AsyncSessionLocal`** を開く（純 async safe）。ウォールクロックは `O(N×M×L)` から `O(N×L)` へ低下し、実測 ~3× 高速化。

**最終状態** — メディア次元の並行検索が Executor の固定形態となる；`memory_ids` の上限はフロントエンド 4、バックエンドで検証。

**コード位置** — [src/videomind/core/agent_loop/executor.py:92-129](src/videomind/core/agent_loop/executor.py#L92-L129) `asyncio.gather(*[_search_one_mid(mid) for mid in state.media_ids])`
- commit：`16e8638`

### 3.3 P2-1：Cross-encoder rerank opt-in（三档バックエンド）　★★

**背景** — 設計文書（RAG-RETRIEVAL.md §2.6 / INTENT-ROUTING.md §7）の再ランカーは `DeterministicReranker`（固定重み）+ 任意の `CrossEncoderReranker`、融合 `rerank_score = 0.7*det + 0.3*ce`、`CrossEncoder` の既定デバイスは `cuda`。問題：固定重みの再ランキングは **query と無関係**であり、query-文書の関連性で精排できない；また cuda ローカル再ランキングには追加 GPU が必要。

**決定** — 新抽象 `Reranker` プロトコル `rerank(query, hits) -> list[VectorHit]`（cross-encoder は query の参加が必須のため、**プロトコル署名を再設計**）。`get_reranker()` が `config.rerank_provider` により三档ルーティング：

| provider | バックエンド | 説明 |
|---|---|---|
| `off` | `DeterministicRerankerAdapter` | 既存の固定重み器を包み、query を受け取ったら無視して委譲；opt-in 昇格前は**現状を壊さない** |
| `api` | `OpenRouterRerankBackend` | POST `{base_url}/rerank` を叩く、純テキスト経路 `documents=[{"text":...}]`、`relevance_score` 降順で再構成 |
| `local` | `LocalBGERerankBackend` | ローカル `BAAI/bge-reranker-v2-m3`（BGE-M3 embedder と同源、CJK に強く、CPU で動作、API コストゼロ）、import/ロード失敗時はファクトリがデグレード |

デグレードは一律 `DeterministicRerankerAdapter` を通り、再ランキング経路に常に利用可能なバックエンドがあることを保証する。

**設計からの逸脱** — **`0.7*det+0.3*ce` の融合をやめ**、純 cross-encoder `relevance_score` に変更；query/media をまたぐ尺度不一致（cross-encoder Nemotron 中国語 ~0.16 vs デグレード Deterministic ~0.5-0.95）の問題は **min-max 正規化**で解決。

**最終状態** — `config.py`/`.env.example` の既定は `off`（opt-in 前は現状を壊さない）；`.env` は実際に `api` を有効化（ローカル実行は OpenRouter rerank 端点を通る）。

**コード位置** —
- [src/videomind/core/rag/rerank_backend.py](src/videomind/core/rag/rerank_backend.py) プロトコル + Adapter + OpenRouter バックエンド + ファクトリ
- [src/videomind/core/rag/rerank.py](src/videomind/core/rag/rerank.py) `DeterministicReranker`（`position 0.3 + source 0.2 + original 0.5`）
- [src/videomind/config.py:113-129](src/videomind/config.py#L113-L129)
- memory：`rag-cross-encoder-rerank`

### 3.4 D-β：QueryRewriter `confidence_threshold` のロールバック　★★

**背景** — 旧 `QueryRewriter.confidence_threshold = 0.7` では、LLM 書き換えの多くが信頼度不足で **fallback rule** となり（実測 P2-4 は `method=rule` が一色、書き換えは形骸化）。D-β は `0.7→0.5` を提案し LLM 書き換えを実際に効かせようとした（commit `0ebf246`）。

**しかし副作用が生じる**：`base_rank` が既に正確（≤4）なクエリに対し、拡散サブクエリがノイズを注入 —— 9 件の評価セットで **1UP / 3DN の純減**、極端な例では rank 1 が 21 まで落ちる（`results_queryrewriter.json` 参照）。悪化防止のアンカー `zh_001` は P2-4 baseline `rank=52 → multi rank=136` と悪化。

**決定 + エビデンス** — `0.5→0.7` へロールバック（commit `8b7f478`）。判定：LLM の非決定性による分散が 0.5↔0.7 の閾値差よりはるかに大きい。「閾値をいじる」ことは解薬ではない —— 0.7 は保守的で、高信頼のときだけ発火し、`zh_001` のような指定不足クエリの UP を残しつつ、既に正確なクエリの拡散による悪化を回避する。

**最終状態** — `confidence_threshold` の既定は 0.7、docstring 原文に D-β の理由を記録。

**コード位置** — [src/videomind/core/intent/rewriter.py:54-62](src/videomind/core/intent/rewriter.py#L54-L62) docstring；`:60` 既定値
- commit：`0ebf246`（ハード変更による検証）→ `8b7f478`（保守的な確定値）
- memory：`rewriter-threshold-not-cure`

### 3.5 max_rounds 2→3 + media_ids 2→4　★

**背景** — クロス動画比較タスク（三者/四者）が AgentLoop のラウンド数とメディア数に高い要求を突きつける：Critic の feedback は 2 ラウンド内では実際に吸収されないことが多く、比較対象も 2 個から 4 個へ拡張された。

**決定** — `AnalyzeRequest.max_rounds: le=2 → le=3`（既定 2 は維持、引き上げを許可）；`media_ids: max_length=2 → max_length=4`。フロントエンドの Zod schema も同期：`max(2)→max(3)`、`min(1).max(4)`；i18n に `threeRounds` / `maxVideosReached` を追加。

**最終状態** — バックエンド `models.py` の `max_rounds` 既定は 2；API + フロントエンドは 3 ラウンド / 4 動画を許可；新規テスト 3 件（`max_rounds=3`、`4`、`4 videos`）が 8/8 グリーン。

**コード位置** — [src/videomind/interface/routes/agent.py:49](src/videomind/interface/routes/agent.py#L49)（`le=3`）、[src/videomind/infrastructure/storage/models.py:503](src/videomind/infrastructure/storage/models.py#L503)
- commit：`910695a`

### 3.6 search→rerank→expand の順序 + 隣接が rerank に参加しない　★

**背景** — 旧実装の `expand → rerank` では、拡張で生まれた隣接 chunk が `position(0.3)+source(0.2)` によって真の命中の順位を奪い、再ランキング結果を汚染していた。これは「隣接はあくまで文脈の補完」という設計意図に反する。

**決定** — **`search → rerank → expand`** へ変更：まず真の検索命中を再ランキングし、その並びの上で前後 ±1 の隣接を拡張する。隣接は `score=0.0` で再ランキング列の**末尾**に追加され、rerank に参加しない；挿入位置は、それが関連づく真の命中の再ランキング後の位置で決まる。

**不変条件** — `raw_hits` は rerank に汚染されない：pipeline が先に `dataclasses.replace(h)` でコピーを渡し、各 rerank バックエンドは新しいリストを返す（二重の保険、memory `rag-pipeline-rerank-expand-order` と呼応）。

**最終状態** — 隣接と真の命中の口径が分離され、正規化時に隣接は `0.0` で自然に最下位となり真の命中と競合しない。

**コード位置** — [src/videomind/core/rag/pipeline.py:91-118](src/videomind/core/rag/pipeline.py#L91-L118)
- memory：`rag-pipeline-rerank-expand-order`

### 3.7 BM25 の CJK トークナイズ（jieba + 2-gram）　★

**背景** — 旧 `BM25Okapi([c.split() for c in corpus])` は CJK に対して `str.split()` で分割せず、文全体が単一トークンと見なされ BM25 の語項マッチが失効する（単なる「句読点なし」問題ではなく、トークナイズの欠落）。中国語/日本語の検索が深刻に退化した。

**決定** — `jieba` トークナイズ + 2-gram に変更し、CJK の語項リコールを修正。`pyproject.toml` に `jieba` 依存を追加。

**最終状態** — BM25 が CJK 内容に対して語項マッチ能力を回復。

**コード位置** — [src/videomind/core/rag/retriever.py](src/videomind/core/rag/retriever.py)（BM25 インデックス構築/スコアリング）
- memory：`bm25-cjk-tokenize` / `asr-no-punctuation-cjk`

### 3.8 エビデンス検証の簡素化 + 集合によるハルシネーション防止　★

**背景** — 設計ドキュメントの `EvidenceVerifier` は `timestamp_ms` の範囲 + jieba による文字レベルの類似度（閾値 0.7）+ source 分流（asr/ocr/frame がそれぞれ `_verify_asr/_verify_ocr/_verify_frame` を通る）を担う。これは segment のロードと ASR/OCR 原文の突き合わせに依存し、重いうえに映像パイプラインと密結合する。

**決定** — 二層に簡素化：
1. `EvidenceVerifier`（[verifier.py](src/videomind/core/agent_loop/verifier.py)）を**純関数**へ降格：タイムスタンプ ∈ `[0, duration_ms]` と content 非空のみを検証し、DB/ASR/OCR に依存しない。
2. ハルシネーション防止は**集合の包含関係**へ切替：Executor が実検索 hit の id をすべて `state.retrieved_evidence_ids` に集約；Critic は `evidence.id ∈ retrieved` かつ各 `conclusion.evidence_ids ⊆ retrieved` を検証 —— LLM が捏造した EID は実検索集合に無いため、そのままハルシネーションと判定される。

**最終状態** — `Critic.passed = llm_passed AND hard_passed`（`hard_passed = evidence_real AND conclusions_real`）。集合検証は原文類似度より強力：偽造 ID は 100% 捕捉され、jieba 類似度のようなグレーゾーンも無い。

**コード位置** — [src/videomind/core/agent_loop/critic.py:113-118](src/videomind/core/agent_loop/critic.py#L113-L118)、[src/videomind/core/agent_loop/executor.py:161-165](src/videomind/core/agent_loop/executor.py#L161-L165)、[src/videomind/core/agent_loop/verifier.py](src/videomind/core/agent_loop/verifier.py)

### 3.9 reasoning の透過 + rewriter の思考連鎖オフ + db 透過　★

**背景** — OpenRouter の reasoning モデル（例：nemotron-3-ultra）は既定で思考連鎖と末尾の JSON を出力し、`json.loads(全体)` が必ず失敗する → 書き換え器が全件 fallback rule となり（D-β の閾値がそもそも発火する機会を得ない）。加えて書き換え器が LLM を呼ぶ際に `db` を透過しないため、課金・記帳の経路が評価パス上で断絶していた。

**決定** —
1. `ChatRequest.reasoning` をゲートウェイのリクエスト body `{"reasoning":{"enabled":...}}` へ透過（commit `66a1864`）。
2. `QueryRewriter` の LLM 呼び出しで明示的に `reasoning=False` とし思考連鎖をオフ、モデルに純粋な JSON を直接出力させる（commit `66a1864`）。
3. `rewriter.rewrite(..., db)` が `db` を `RoutingLLMService.chat(req, db)` へ透過し課金を記帳；`db=None` のとき accounting は静かに記帳をスキップし（LLM 呼び出し自体は継続）、評価や session 無しのパスを阻害しない（commit `d4692e1`）。

**最終状態** — 書き換え JSON がパース可能となり、D-β の閾値がようやく機能する；課金経路は疎通しつつ評価は阻害されない。

**コード位置** — [src/videomind/core/intent/rewriter.py:81-91](src/videomind/core/intent/rewriter.py#L81-L91)
- commit：`66a1864` / `d4692e1`

### 3.10 score の min-max 正規化　★

**背景** — cross-encoder と降格時の Deterministic を跨 query/media で統合すると尺度が揃わない（Nemotron raw の中国語は ~0.16、Deterministic の線形加重は ~0.5–0.95）。降格側の高スコアが真に高関連な hit を押し出し、フロントエンドが `score*100` で表示する百分率にも `[0,1]` が必要となる。

**決定** — 真の命中（`score>0`）を min-max で `[0,1]` に正規化；隣接は `score=0.0` で統計に含めず 0.0 を維持し、跨 query 統合時に自然と最下位となる。`span≈0`（真の命中がすべて同点）は 1.0 へ退化させ高スコアの概念を保持。`raw_hits` と trace には正規化前の原値を記録し真値を保つ。

**最終状態** — フロントエンドの百分率が意味を持ち、跨 query/media 統合で尺度のずれによる命中の取りこぼしがなくなる。

**コード位置** — [src/videomind/core/rag/pipeline.py:112-143](src/videomind/core/rag/pipeline.py#L112-L143)

### 3.11 D → B の決定点（B 段階は保留）

**背景** — 設計ドキュメント §9 が D→B の決定点を定める：D-α+D-β の評価後、hard query が evidence top-20 に入るかを見る。
- 命中情况 1：`ev_recall@5_hard` が上昇し ja_002/ja_004 が top-20 に入る → **リコール水位がボトルネックであり、B は品質目的では不要**
- 命中情况 2：広いリコール + `rerank_top_n=60` でも ranks が入らない → B を起動（local rerank を非打ち切り + GPU）
- 中間状態：B + D の重ね掛け

**決定 + エビデンス** — D 段階の実測は**情况 1** に命中（hard が候補プールに入り、ev_MRR がベースライン 0.3333 から改善）→ B 段階（ローカル rerank の GPU 化）は**実施しない**と判定。三種類の因子が反転した場合（品質の後退 / 遅延が許容上限まで悪化 / コストが制御不能）にのみ再起動する。

**最終状態** — B 段階は保留；`rerank_provider=api` が OpenRouter のリモート cross-encoder で「ローカル + GPU」経路を代替する。

**コード位置** — 設計上の主要参考 §9；memory：`stage-b-local-rerank-gpu-not-now`

### 3.12 OCR 二重パス + ImportError の静黙吞み込み修正　★★

**背景** — 「OCR モジュールが機能していないようだ」。systematic-debugging（Phase 1 の根本原因追溯）で二層の根本原因を特定（いずれか一方だけでも OCR が全期間空産出となり、かつエラーが表面化しない）：

1. **依存解決の層**：プロジェクトに `.python-version` が無く、`requires-python = ">=3.11"` に上限が無い → `uv` が `.venv` の Python 3.14 を自動選択。`paddlepaddle` は `cp39–cp313` の wheel のみで `cp314` が無い → `uv sync` が**解決全体で失敗**（逆検証：`uv pip install --dry-run paddlepaddle` が "only found wheels for cp39…cp313" を報告）→ `.venv` には **0 パッケージ**（`httpx` すら無い）が入る。
2. **エラー処理の層（増幅器）**：`tasks.py` の `ocr_task` が単一の `except Exception` で**ImportError を含む全例外**を一律に `logger.warning(... results=[])` へ降格していた。「依存の欠落」が「フレーム上に文字が無い」と完全に同形の WARNING に埋没 → 実行時は正常に見え、`frame_ocr` テーブルは永遠に書かれず → chunk は全て `source_type=asr` → 「OCR が機能しない」かつエラーゼロ。anaconda `base`（3.13.5）には paddleocr 3.3.1（3.13≤cp313）があり、3.14 が断点であることの反証となる。

**決定** — ユーザーは OCR を ocr.space の無料 API（key `K858…8957`）で通し、ローカル paddle の環境問題を迂回することを選択：

| 変更 | 選型理由 |
|---|---|
| `OCR_PROVIDER=local\|api` の二重パス | ASR/Embedding/rerank の二重パス范式と揃え、env でバックエンドを切替えても契約を壊さない |
| `_recognize_api` を ocr.space `/parse/image` へ接続 | 無料枠 500 req/日/IP、ローカルの重い依存が不要、`base64Image + language + OCREngine + isOverlayRequired` フォーム |
| `paddleocr`/`paddlepaddle` を `[project.optional-dependencies].ocr` へ移動し uv sync の停止を解消 | cp314 wheel の無いパッケージを主依存に残すと py3.14 の解決全体が失敗し 0 パッケージ導入となる；extras に入れれば `uv sync`（api 経路）が成立し、ローカルは `uv sync --extra ocr`（py≤3.13 が必要） |
| `_download_and_dedup` を抽出（local/api 共用） | 二重パスで重複する「ダウンロード + phash 重複排除」ロジックを排除し、単一の真のソースとする |
| `ocr_task` の例外を三档に（known-skip は静黙 / `ImportError` + その他 Error 級で露出） | 根本原因層の増幅器を修正：`except Exception` の一律静黙を廃止；真の依存問題や想定外の問題は Error 級で浮上させ、known-skip（paddle PIR/oneDNN の上流バグ、api の key 欠落）のみ静黙 |
| OAuth 側は tasks.py 以外の降格契約を変更しない | OCR は依然として非クリティカルパス —— いずれの档でも `results=[]` へ降格して INDEX へ入る（映像には既に ASR テキストがある）。変えるのは「顕現性」のみで「継続可否」ではない |

**評価エビデンス** — RAG eval ではなく**二軌のスモークテスト**で確認：
1. **実 ocr.space のエンドツーエンド**：本番 `OCREngine`（`http_client=None` で実 httpx、実 base64、実 phash、実ネットワーク）で中英混排の画像（"Sales 2026 销售额 +58% / 第二季度 Q2 revenue growth"）を認識 → `model_name=ocr.space`、`frame_ms=0`、文字は正確。api 経路の全链路契約が通ることを証明。
2. **7/7 ユニットテスト**（mock httpx + mock MinIO）：正常解析 / トップレベルエラーの降格 / 5xx で単一フレーム降格しバッチ全体を壊さない / 重複フレームの排除で API を呼ばない / 空フレーム / key 欠落で NotImplementedError / 複数フレームの順序。インフラ依存は皆無。
- 反証：`OCR_PROVIDER=api` へ切替える前は認識不能（.venv 0 パッケージ）；切替後にエンドツーエンドで通る → 根本原因は OCR ロジック自体ではなく依存解決の層にあることを証明。

**最終状態** — `.env` で `OCR_PROVIDER=api` + ocr.space の key/engine/language/timeout を有効化；`config.py` に OCR API 設定 5 項目を追加（既定 `OCR_API_BASE_URL=https://api.ocr.space`、`engine=2`、`language=auto`）；`paddleocr`/`paddlepaddle` は `ocr` extras へ；`ocr.py` は二重パス + 共用の重複排除；`tasks.py` の三档例外は ImportError を静黙吞み込みしない；`test_ocr_api.py` の 7 ケースが api 契約をカバー。ローカル経路はオフライン/クォータ枯渇時のフォールバックとして保持（py≤3.13 + `uv sync --extra ocr` が必要）。

**コード位置** — `src/videomind/core/video_pipeline/ocr.py`（`_recognize_api` / `_download_and_dedup` / 三バックエンドの model_name）、`src/videomind/config.py:85-93`（OCR API 設定）、`src/videomind/application/task_orchestration/tasks.py:468-485`（三档例外）、`pyproject.toml`（`ocr` extras）；新規テスト `tests/core/video_pipeline/test_ocr_api.py`；memory：`asr-quality-local-turbo-gpu`（同族の二重パス范式の参照）

---

## 4. 未実装 / 簡素化された計画項目 / Deferred or Simplified

設計ドキュメントが描いたが実装で**未実装**または**簡素化**された項目をここに集約する（バグではなく、明示的な取捨）：

| 項目 | 計画の出典 | 現在の状態 | 備考 |
|---|---|---|---|
| **SQL / 構造化検索チャネル** | RAG-RETRIEVAL §2.3 / INTENT-ROUTING §5 | 未実装 | `IntentRouter` は依然 sql のクォータを計算する（`ChannelQuota.sql`）が `SQLSearchChannel` が無く、`HybridRetriever` は vector+bm25 のみを実行 |
| **MCP ツール呼び出し統合** | INTENT-ROUTING §8 | 未実装 | `MCPToolRegistry` / `VideoSegmentTool` / `VideoSearchTool` は未実装；ゲートウェイは上流の `tool_calls` delta を透過するのみ |
| **ポストプロセッサチェーン** | RAG-RETRIEVAL §2.3 | 未実装 | `Deduplication / ThresholdFilter(0.3) / DiversitySampling` に明示的な実装が無い |
| **Checkpoint の Redis ホットキャッシュ + `resume_analysis`** | AGENT-LOOP §5 | 一部実装 | `infrastructure/cache/redis.py` に checkpoint キャッシュ基盤は構築済み；本番の `_checkpoint`（`routes/agent.py`）は**現在 PostgreSQL へ直接書き込み**（`AgentCheckpoint` テーブル）；`resume_analysis` の中断復帰エントリは独立実装が見当たらない |
| **`EvidenceVerifier` の source 分流 + jieba 類似度** | AGENT-LOOP §3.4 | 簡素化済み | §3.8 を参照。純関数 + 集合によるハルシネーション防止へ変更 |
| **追問の `follow_up` 再利用** | AGENT-LOOP §6 | 未実装 | 前ラウンドの `AgentState` / `VideoContext` をロードして再利用する独立エントリが無い |
| **CrossEncoder の `0.7*det+0.3*ce` 融合 + cuda 既定** | RAG-RETRIEVAL §2.6 | 設計変更 | §3.3 を参照。純 cross-encoder スコア + min-max 正規化へ変更 |
| **`llm_first_packet_timeout_s` 初回パケットタイムアウト** | MODEL-GATEWAY | 未消費 | 設定は存在する（10.0）が、現在の主経路では消費されていない |
| **意図ツリーの yaml ファイル化** | INTENT-ROUTING §2 | 実装上の選択 | コード内の `DEFAULT_INTENT_TREE` 定数へ変更し、ファイル依存を一つ削減 |
| **ローカル OCR（PaddleOCR）を既定の主依存とする** | VIDEO-PIPELINE §2.4 | 任意へ降格 | `paddleocr`/`paddlepaddle` を `ocr` extras へ移動（cp314 wheel が無く、主依存に置くと py3.14 の `uv sync` が解決全体で失敗）；既定は `OCR_PROVIDER=api`（ocr.space）で、ローカル OCR には `uv sync --extra ocr`（py≤3.13）が必要。§3.12 を参照 |

> 注記：以上の「未実装」項目に再開の必要が生じた場合、対応する設計ドキュメントの章が依然として主要参考 specs であり、再設計は不要。

---

## 5. 意思決定の出典索引 / Source Index

| カテゴリ | 位置 |
|---|---|
| D-α / D-β / P2-5 の設計 | `docs/superpowers/specs/2026-08-04-retrieval-depth-tuning-design.md` |
| P2-4 の診断スクリプト | `scripts/eval/diag_retrieval.py` / `eval_queryrewriter.py` |
| 評価結果 JSON | `results_off_api_topk20_recallk60.json` / `results_queryrewriter.json` |
| コード内の意思決定記録 | `rewriter.py:54-62`（D-β）、`executor.py:37-41`（D-α）、`pipeline.py:37-117`（順序/階層化/正規化）、`rerank_backend.py:1-19`（P2-1）、`ocr.py`（§3.12 OCR 二重パス）、`tasks.py:468-485`（§3.12 三档例外） |
| Commit 系列 | `0ebf246`(D-β ハード変更) → `66a1864`(reasoning 透過) → `8b7f478`(D-β ロールバック) → `16e8638`(並行検索:D-α) → `910695a`(max_rounds/media_ids) → `6059abc`(D-α 階層化+二重パス再ランキング) |
| 永続メモリ | `~/.claude/projects/d--shu-e-Documents-Video-MInd-python/memory/`（`rag-pipeline-rerank-expand-order` / `rag-cross-encoder-rerank` / `rewriter-threshold-not-cure` / `bm25-cjk-tokenize` / `stage-b-local-rerank-gpu-not-now` / `asr-quality-local-turbo-gpu` など） |

---

> 本文書は実装と意思決定の進展に伴い継続的に更新する；設計から逸脱する新たな決定は歴史を書き換えずここへ**追記**し、追跡可能な決定の連鎖を保つ。
