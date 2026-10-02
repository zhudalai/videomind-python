# VideoMind 全体アーキテクチャ設計

> 全体アーキテクチャ図、レイヤード設計、データフロー、技術選定理由、Java 版との比較、GPU スケジューリング戦略

---

## 1. 全体アーキテクチャ図

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              VideoMind Platform                              │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │
│  │   Frontend   │  │   Admin UI   │  │  API Gateway │  │   WebSocket  │    │
│  │  (Vue 3)     │  │  (Vue 3)     │  │  (FastAPI)   │  │   / SSE      │    │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘    │
│         │                 │                 │                 │            │
├─────────┼─────────────────┼─────────────────┼─────────────────┼────────────┤
│         ▼                 ▼                 ▼                 ▼            │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                      Application Layer (FastAPI)                      │   │
│  │  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌──────────────────┐  │   │
│  │  │   Auth     │  │  Video     │  │   RAG      │  │     Agent      │  │   │
│  │  │   Module   │  │  Module    │  │  Module    │  │     Module     │  │   │
│  │  └─────┬──────┘ └─────┬──────┘ └─────┬──────┘ └────────┬─────────┘  │   │
│  └────────┼──────────────┼──────────────┼─────────────────┼────────────┘   │
│           │              │              │                 │                │
├───────────┼──────────────┼──────────────┼─────────────────┼────────────────┤
│           ▼              ▼              ▼                 ▼                │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                    Core Services Layer (Python)                       │   │
│  │ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐  │   │
│  │ │ Downloader│ │  ASR     │ │  OCR     │ │  Embedding│ │   LLM      │  │   │
│  │ │ (yt-dlp) │ │ (Whisper)│ │(PaddleOCR)│ │(BGE-M3)  │ │  (Ollama)  │  │   │
│  │ └──────────┘ └──────────┘ └──────────┘ └──────────┘ └────────────┘  │   │
│  │ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐  │   │
│  │ │  Hybrid  │ │  Agent   │ │  Model   │ │  Task    │ │  Intent    │  │   │
│  │ │ Retriever│ │  Loop    │ │ Gateway  │ │ Engine   │ │  Router    │  │   │
│  │ └──────────┘ └──────────┘ └──────────┘ └──────────┘ └────────────┘  │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│           │              │              │                 │                │
├───────────┼──────────────┼──────────────┼─────────────────┼────────────────┤
│           ▼              ▼              ▼                 ▼                │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                      Infrastructure Layer                             │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐ ┌────────┐  │   │
│  │  │ PostgreSQL│  │  Redis   │  │  Qdrant  │  │  MinIO   │ │ Ollama │  │   │
│  │  │ (Meta/Task)│  │(Cache/Lock│  │ (Vector) │  │ (Object) │ │ (LLM)  │  │   │
│  │  │          │  │ /Queue)  │  │          │  │          │ │        │  │   │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘ └────────┘  │   │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐             │   │
│  │  │ Celery   │  │ Prometheus│  │ Grafana  │  │  FFmpeg  │             │   │
│  │  │ Workers  │  │  + Loki   │  │ Dashboards│ │ (Media)  │             │   │
│  │  └──────────┘  └──────────┘  └──────────┘  └──────────┘             │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. レイヤードアーキテクチャ設計

### 2.1 4 層アーキテクチャ

| レイヤー | 責務 | 主要コンポーネント | 設計原則 |
|------|------|----------|----------|
| **Interface** | 外部公開：REST API、SSE ストリーミング、WebSocket、Admin パネル | FastAPI Router、SSE Broadcaster、OpenAPI Schema | プロトコル非依存、バージョニング、統一エラーラッピング |
| **Application** | ビジネスユースケースのオーケストレーション：認証、動画管理、RAG 検索、Agent 分析、タスクスケジューリング | `VideoService`、`RAGService`、`AgentService`、`TaskService` | 単一責務、依存性逆転、テスト容易 |
| **Core Services** | コア能力の実装：ダウンロード、ASR、OCR、Embedding、LLM、検索、AgentLoop、モデルゲートウェイ | `Downloader`、`ASRProcessor`、`HybridRetriever`、`AgentLoop`、`RoutingLLMService` | 高凝集、交換可能、可観測 |
| **Infrastructure** | インフラストラクチャ：データベース、キャッシュ、ベクトル DB、オブジェクトストレージ、メッセージキュー、監視、GPU スケジューリング | SQLAlchemy、Redis、Qdrant、MinIO、Celery、Prometheus、Ollama | 統一抽象、コネクションプール、ヘルスチェック |

### 2.2 モジュール依存ルール

```
Interface → Application → Core Services → Infrastructure
     ↑                                               │
     └────────────── 逆方向依存の禁止 ──────────────────┘
```

- **Interface** は **Application** の抽象インターフェース（Protocol/ABC）のみに依存
- **Application** は **Core Services** の抽象インターフェースのみに依存
- **Core Services** は **Infrastructure** のアダプタ実装のみに依存
- いかなるレイヤーも、下位レイヤーの具象実装クラスを直接 import することは**禁止**

---

## 3. コアデータフロー

### 3.1 動画取り込みフロー

```
ユーザーがアップロード／リンクを貼り付け
      │
      ▼
POST /api/videos  (MediaFile レコードを作成、status=PENDING)
      │
      ▼
Celery Task: download_video_task
      │  ├─ yt-dlp でダウンロード → MinIO に保存
      │  ├─ FFmpeg で音声を抽出(16kHz mono) + シーン検出によるキーフレーム抽出
      │  └─ MediaFile を更新: duration, size, minio_path, status=DOWNLOADED
      ▼
Celery Task: transcribe_task (セグメント単位の並列化が可能)
      │  ├─ Whisper でセグメント ASR (60s/セグメント、GPU)
      │  ├─ TranscriptionChunk レコードを生成
      │  └─ 全テキストを結合 → VideoTranscription
      ▼
Celery Task: ocr_task (並列化可能)
      │  ├─ PaddleOCR でキーフレーム認識
      │  ├─ 知覚ハッシュによる重複排除
      │  └─ FrameOCR レコードを生成
      ▼
Celery Task: build_context_task
      │  ├─ 60s ウィンドウで ASR+OCR を統合 → VideoSegment リスト
      │  ├─ 5min Chunk の要約＋キーワード＋Embedding
      │  ├─ ベクトルを Qdrant へ + キーワードを BM25 インデックスへ
      │  └─ MediaFile.status=READY を更新
      ▼
完了：フロントエンドが SSE で段階イベントを受信 → Agent 分析に移行可能
```

### 3.2 Agent 分析フロー

```
ユーザーが分析を開始 (POST /api/analysis)
      │
      ▼
AnalysisTask(record) を作成 → Celery: analyze_task
      │
      ▼
AgentLoop.run(goal, video_context)
      │
      ├─▶ Planner: ゴール → 実行可能なサブタスク 1〜5 件
      │
      ├─▶ Executor(ループ ≤2 ラウンド):
      │      ├─ 検索: HybridRetriever → TopK Evidence
      │      ├─ 生成: 構造化された結論 {title, conclusions[], evidence[], suggestions[]}
      │      └─ エビデンス紐付け: 各 conclusion には必ず timestampMs + source(ASR|OCR) + text を付与
      │
      ├─▶ Critic: ゴール網羅度 + 構造の完全性 + タイムスタンプによるエビデンス検証 + ハルシネーションなし
      │      ├─ passed → 最終結果を出力
      │      └─ failed → feedback + requiredTimestamps → Executor に戻りエビデンスを補強 (最大 2 ラウンド)
      │
      └─▶ Checkpoint: 毎ラウンド AgentState を永続化(PostgreSQL+Redis)
            ├─ チェックポイントによる中断・復帰：最後のラウンドから再開
            └─ 追加質問：VideoContext を再利用し、goal だけ差し替えて Loop を再実行
      ▼
SSE プッシュ通知: PLANNING → EXECUTING → CRITIC_CHECK → COMPLETED/FAILED
      │
      ▼
フロントエンド描画：Markdown + エビデンスカード(タイムスタンプへジャンプ可能) + マインドマップ
```

### 3.3 RAG 質疑応答／追加質問フロー

```
ユーザーが質問 (WebSocket/SSE)
      │
      ▼
QueryRewriter: クエリ書き換え + サブ質問への分割 (LLM + ルールフォールバック)
      │
      ▼
IntentRouter: ツリー型の意図分類 → ナレッジベース／ツールへ紐付け
      │
      ▼
MultiChannelRetrieval (並列):
      ├─ VectorSearch(Qdrant, topK*2)
      ├─ BM25Search(Rank-BM25 メモリ内インデックス, topK*2)
      └─ (オプション) GraphSearch / MCPToolCall
      │
      ▼
RRFusion → TopK
      │
      ▼
ContextExpander: ±1 chunk でコンテキストを拡張
      │
      ▼
Rerank: Deterministic(位置/出所/スコア) → オプションの CrossEncoder
      │
      ▼
AnswerGenerator: Prompt(Context + Citations) → LLM Stream
      │
      ▼
SSE ストリーミング応答: Answer + Citations(ChunkEvidenceID からタイムスタンプへの引用追跡が可能)
```

---

## 4. 技術選定理由表

| 領域 | 選定 | コアとなる理由 | 代替候補と不採用の理由 |
|------|------|----------|----------------|
| **Web フレームワーク** | FastAPI | ネイティブ非同期、自動 OpenAPI、ネイティブ SSE、型ヒントに優しい | Flask(非同期なし)、Django(重量級)、Starlette(低レベルすぎる) |
| **タスクキュー** | Celery + Redis | Python エコシステムの定番、優先度／リトライ／定期実行をサポート、Flower スケジューラで可視化 | RQ(機能が弱い)、Dramatiq(コミュニティが小さい)、自社開発(車輪の再発明) |
| **ASR** | **Whisper (openai-whisper)** | **ローカルオフライン、SOTA 精度、GPU アクセラレーション、API 費用ゼロ、多言語対応** | Alibaba Cloud ASR(有料／ネット接続必須)、FunASR(デプロイが重量級)、Whisper.cpp(C++ 統合が複雑) |
| **動画ダウンロード** | yt-dlp | 1800+ プラットフォーム対応、活発にメンテナンス、Python から直接呼び出し可能、フォーマット選択が豊富 | youtube-dl(更新停止)、自社開発(メンテナンスコストが極めて高い) |
| **OCR** | PaddleOCR | 中国語認識で最強、PP-OCRv4 は軽量、GPU アクセラレーション、Python パッケージを直接インストール | Tesseract(中国語に弱い)、EasyOCR(精度がやや低い)、クラウド API(有料) |
| **ベクトル DB** | Qdrant | 純 Rust で高性能、Payload フィルタリング、単一ノードで依存なし、Python クライアントが成熟 | Milvus(重量級、K8s 必須)、pgvector(性能が弱い)、Chroma(永続化が弱い) |
| **キーワード検索** | Rank-BM25 | 純 Python、サービス不要、BM25 の標準実装、メモリ使用量を制御可能 | Elasticsearch(重量級)、Tantivy(Rust FFI が複雑) |
| **RAG オーケストレーション** | LlamaIndex | 検索パイプラインが豊富、モジュール性が高い、Query Engine の抽象化が優秀、コミュニティが活発 | LangChain(検索が弱い、抽象の漏洩)、Haystack(Java 系) |
| **Agent オーケストレーション** | **自社開発 AgentLoop** | **コアの競争力：Planner/Executor/Critic のクローズドループ、エビデンスの厳格な検証、Checkpoint** | LangGraph(ブラックボックス、説明が困難)、AutoGen(重量級)、CrewAI(重量級) |
| **ローカル LLM** | Ollama + Qwen2.5-7B-INT4 | 6GB VRAM で動作可能、OpenAI 互換 API、モデル管理が簡単、費用ゼロ | vLLM(より多くの VRAM が必要)、LM Studio(API なし)、TGI(重量級) |
| **ベクトルモデル** | BGE-M3 / BAAI シリーズ | 中国語・英語のバイリンガルに強い、Sentence-Transformers によるラップ、多粒度検索 | E5(英語系に強い)、OpenAI Embedding(有料／ネット接続必須) |
| **フロントエンド** | Vue 3 + Vite | Java 版と設計を共用、Composition API、エコシステムが成熟、SSR が選択可能 | React(チームに馴染みがない)、Svelte(エコシステムが小さい) |
| **監視** | Prometheus + Grafana + Loki | クラウドネイティブの標準、Query が強力、Dashboard が豊富、コストが低い | Datadog(高額)、自社開発(メンテナンスが重量級) |

---

## 5. Java 版 (DOVideo-AI) との比較

| 観点 | Java 版 (DOVideo-AI) | Python 版 | 面接トークでの強み |
|------|---------------------|-----------|-------------|
| **ASR** | Alibaba Cloud ASR (商用 API) | **ローカル Whisper large-v3** | 「1h の動画を 2 分で文字起こし。完全オフライン、コストゼロ、データは域外に出ない」 |
| **GPU 活用** | 遊休状態 | **直列オフピーク：Whisper→OCR/Embedding→Ollama** | 「RTX 4060 8GB 1 枚で全リンクを OOM なしで実行。Java 版では GPU が遊休状態」 |
| **Agent コア** | LangChain4j Agent | **自社開発 AgentLoop** | 「Planner/Executor/Critic のコードを一行ずつ説明でき、どこにフレームワークを使いどこを自社開発したか語れる」 |
| **RAG 検索** | Qdrant + キーワード重み付け | LlamaIndex マルチチャネル + RRF + CrossEncoder | 「検索パイプラインの各層が交換可能なコンポーネントであることを理解している。ブラックボックス呼び出しではない」 |
| **並行モデル** | 仮想スレッド + スレッドプール | **asyncio(IO) + multiprocessing/Celery(CPU)** | 「GIL をどう回避するか語れる：IO は async、CPU 密集はプロセスプールに投げる」 |
| **サーキットブレーカー／ルーティング** | なし | **Ragent から移植した三状態サーキットブレーカー＋初回応答プローブ** | 「プロダクション級のモデルゲートウェイ：ステートマシンのロジックは言語非依存」 |
| **意図認識** | なし | **ツリー型意図＋信頼度しきい値＋曖昧性のガイド** | 「Ragent から移植し、複合問題の分解とルーティングを解決」 |
| **評価体系** | なし | **rag-eval オフライン評価フレームワーク** | 「データ駆動で RAG を反復改善。感覚頼みのパラメータ調整ではない」 |
| **デプロイ複雑度** | Maven + Docker + K8s | **Docker Compose ワンコマンド起動** | 「個人プロジェクト／面接 Demo のデプロイが極めてシンプルで、評価コストが低い」 |
| **フロントエンド再利用** | Vue 3 単独 | **同一のフロントエンド** | 「フロントとバックを疎結合に設計。バックエンドを入れ替えてもフロントは変更不要」 |

---

## 6. GPU リソーススケジューリング戦略（単一 GPU 8GB がコアの強み）

```
┌─────────────────────────────────────────────────────────────────┐
│                    GPU Memory Timeline (8GB)                    │
├─────────────────────────────────────────────────────────────────┤
│ Phase 1: Whisper ASR (60s segments, batch=1)                   │
│   ████████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  │
│   ~6.2GB VRAM  →  完了後に明示的に del model + torch.cuda.empty_cache()│
│                                                                 │
│ Phase 2: PaddleOCR + BGE-M3 Embedding (並列／直列どちらでも可)           │
│   ████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  │
│   ~2GB VRAM (OCR) + ~1.5GB (Embedding) → 短時間で完了し即時解放          │
│                                                                 │
│ Phase 3: Ollama Qwen2.5-7B INT4 (Agent 推論)                    │
│   ████████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  │
│   ~5.8GB VRAM  →  長期常駐、推論時のみ占有                          │
└─────────────────────────────────────────────────────────────────┘

主要な実装：
- `core/video/gpu_scheduler.py`: GPUResourceManager シングルトン
- `acquire(phase: str) -> contextmanager`: 排他的に取得、自動解放
- Whisper: `device_map="auto", low_cpu_mem_usage=True`
- Ollama: `num_gpu=1, num_ctx=4096` 固定コンテキスト
- プロセス分離：Celery worker に `worker_concurrency=1` + `prefetch_multiplier=1` を設定
```

---

## 7. 主要な非機能設計判断

| 判断ポイント | 選択 | 理由 |
|--------|------|------|
| **データベース** | PostgreSQL (本番) / SQLite (開発) | JSONB サポート、全文検索、成熟していて安定；開発期は SQLite で設定ゼロ |
| **ベクトル ID** | UUID v5 (namespace=media_id) | 決定論的、冪等性、追跡可能、集中型 ID ジェネレータが不要 |
| **冪等キー** | `content_hash + goal_hash` | 同一動画＋同一ゴールの分析は重複実行しない、コンテンツレベルの重複排除 |
| **検索融合** | RRF (k=60) | パラメータ調整が不要、理論的保証あり、業界標準の手法 |
| **エビデンス引用 ID** | `chunk_{media_id}_{hash12}_{index}` | 安定、可読、PostgreSQL/Qdrant/評価の三者で共有 |
| **ストリーミングプロトコル** | SSE (Server-Sent Events) | 単方向ストリーム、自動再接続、ファイアウォールに優しい、ブラウザネイティブ対応 |
| **設定管理** | Pydantic Settings + `.env` | 型安全、環境変数による上書き、バリデーションしやすい |
| **ログフォーマット** | JSON + trace_id + span_id | 構造化、横断的に関連付け可能、Loki に直接投入 |
| **エラーコード** | 統一された `ErrorCode` Enum + HTTP マッピング | フロントエンドでの統一処理、国際化しやすい、切り分けが速い |

---

## 8. 拡張性のための予約ポイント

| 拡張方向 | 予約されたインターフェース／設計 |
|----------|---------------|
| **マルチモーダルモデル** | `EmbeddingProvider`/`LLMProvider` Protocol。新しいクラスを実装するだけで追加可能 |
| **分散デプロイ** | Celery はマルチ Worker 対応、Qdrant/MinIO/Redis は自然にクラスタ構成可能、PostgreSQL はマスター・スレーブ |
| **マルチテナント SaaS** | `user_ai_config` テーブルでユーザーごとのモデル設定を分離、`API Key` は AES-GCM で暗号化保存 |
| **プラグイン型ツール** | `MCPToolRegistry` が `@mcp_tool` デコレータ付き関数を自動検出 |
| **評価駆動** | `rag-eval` オフライン評価を CI に統合し、検索パイプラインを変更するたびにベースライン比較を実行 |
| **エッジ推論** | `RoutingLLMService` は `device=cpu/cuda/mps` のタグルーティングをサポート |

---

## 9. ディレクトリ構成（計画）

```
videomind/
├── AGENTS.md                    # 本ドキュメントの索引
├── docker-compose.yml           # すべてのインフラをワンコマンドで起動
├── .env.example                 # 環境変数テンプレート
├── pyproject.toml               # 依存関係管理
├── alembic/                     # データベースマイグレーション
├── core/                        # コアビジネス層（パッケージ）
│   ├── __init__.py
│   ├── config.py                # Pydantic Settings
│   ├── auth/                    # JWT、API Key 管理
│   ├── video/                   # ダウンロード、ASR、OCR、Context 構築
│   ├── rag/                     # 検索パイプライン
│   ├── agent/                   # AgentLoop、Planner、Executor、Critic
│   ├── llm/                     # モデルゲートウェイ、サーキットブレーカー、ルーティング
│   ├── task/                    # Celery タスク、ステートマシン、SSE
│   ├── intent/                  # 意図ツリー、クエリ書き換え、MCP
│   ├── storage/                 # SQLAlchemy Models、Repository
│   └── observability/           # ログ、メトリクス、Trace
├── api/                         # FastAPI ルーティング層
│   ├── v1/
│   │   ├── videos.py
│   │   ├── analysis.py
│   │   ├── rag.py
│   │   ├── chat.py
│   │   └── admin.py
│   └── deps.py                  # 依存性注入
├── frontend/                    # Vue 3 + Vite
│   ├── src/
│   │   ├── views/
│   │   ├── components/
│   │   ├── api/
│   │   └── stores/
│   └── package.json
├── tests/                       # pytest + テストファクトリ
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── scripts/                     # 運用スクリプト
│   ├── init_db.py
│   ├── download_models.py
│   └── benchmark.py
└── docs/                        # 設計ドキュメント（本ディレクトリ配下のすべての .md）
```

---

> **ドキュメントバージョン**：v0.1（計画段階）  
> **メンテナー**：VideoMind コアチーム  
> **関連ドキュメント**：[DATA-MODEL_JP.md](DATA-MODEL_JP.md) · [VIDEO-PIPELINE_JP.md](VIDEO-PIPELINE_JP.md) · [RAG-RETRIEVAL_JP.md](RAG-RETRIEVAL_JP.md) · [AGENT-LOOP_JP.md](AGENT-LOOP_JP.md) · [MODEL-GATEWAY_JP.md](MODEL-GATEWAY_JP.md) · [TASK-ORCHESTRATION_JP.md](TASK-ORCHESTRATION_JP.md) · [INTENT-ROUTING_JP.md](INTENT-ROUTING_JP.md) · [FRONTEND_JP.md](FRONTEND_JP.md) · [SECURITY_JP.md](SECURITY_JP.md) · [OBSERVABILITY_JP.md](OBSERVABILITY_JP.md) · [DEPLOYMENT_JP.md](DEPLOYMENT_JP.md) · [INTERVIEW-GUIDE.md](INTERVIEW-GUIDE.md)
