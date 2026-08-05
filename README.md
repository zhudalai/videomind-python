<div align="center">

# VideoMind

**📹 Agentic RAG 视频理解平台 / Agentic RAG Video Understanding Platform / Agentic RAG 動画理解プラットフォーム**

> 用户上传视频或链接 → 本地 / API 双路径 ASR + OCR → 混合检索 → 自研 AgentLoop 多轮分析 → 带时间戳证据的结构化结论
>
> Upload a video or link → dual-path ASR + OCR → hybrid retrieval → an in-house AgentLoop multi-round analysis → structured conclusions backed by timestamped evidence
>
> ユーザーが動画またはリンクをアップロード → ローカル / API 二重パス ASR + OCR → ハイブリッド検索 → 自社開発 AgentLoop による多段階分析 → タイムスタンプ付きエビデンスによる構造化された結論


[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Celery](https://img.shields.io/badge/Celery-5.4%2B-37814A?logo=celery&logoColor=white)](https://docs.celeryq.dev/)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=white)](https://react.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

---

## 📑 目次 / Table of Contents / 目次

- [VideoMind](#videomind)
  - [📑 目次 / Table of Contents / 目次](#-目次--table-of-contents---目次)
  - [✨ 核心亮点 / Highlights / 主な特長](#-核心亮点--highlights---主な特長)
    - [🇨🇳 中文](#-中文)
    - [🇬🇧 English](#-english)
    - [🇯🇵 日本語](#-日本語)
  - [🖼️ プロジェクトスクリーンショット / Screenshots / プロジェクトスクリーンショット](#️-プロジェクトスクリーンショット--screenshots---プロジェクトスクリーンショット)
    - [🇨🇳 中文](#-中文-1)
    - [🇬🇧 English](#-english-1)
    - [🇯🇵 日本語](#-日本語-1)
  - [🏗️ アーキテクチャ概要 / Architecture Overview / アーキテクチャ概要](#️-アーキテクチャ概要--architecture-overview---アーキテクチャ概要)
    - [🇨🇳 中文](#-中文-2)
    - [🇬🇧 English](#-english-2)
    - [🇯🇵 日本語](#-日本語-2)
  - [🧰 技術スタック / Tech Stack / 技術スタック](#-技術スタック--tech-stack---技術スタック)
  - [🚀 クイックスタート / Quick Start / クイックスタート](#-クイックスタート--quick-start---クイックスタート)
    - [🇨🇳 中文](#-中文-3)
    - [🇬🇧 English](#-english-3)
    - [🇯🇵 日本語](#-日本語-3)
  - [⚙️ 設定説明 / Configuration / 設定説明](#️-設定説明--configuration---設定説明)
    - [🇨🇳 中文](#-中文-4)
    - [🇬🇧 English](#-english-4)
    - [🇯🇵 日本語](#-日本語-4)
  - [🧪 テスト / Testing / テスト](#-テスト--testing---テスト)
    - [🇨🇳 中文](#-中文-5)
    - [🇬🇧 English](#-english-5)
    - [🇯🇵 日本語](#-日本語-5)
  - [🌐 フロントエンドページ / Frontend Pages / フロントエンドページ](#-フロントエンドページ--frontend-pages---フロントエンドページ)
  - [📁 ディレクトリ構造 / Directory Structure / ディレクトリ構造](#-ディレクトリ構造--directory-structure---ディレクトリ構造)
  - [📚 ドキュメント索引 / Documentation Index / ドキュメント索引](#-ドキュメント索引--documentation-index---ドキュメント索引)
  - [📄 ライセンス / License / ライセンス](#-ライセンス--license---ライセンス)

---

## ✨ 核心亮点 / Highlights / 主な特長

### 🇨🇳 中文

- **🛤️ 推理双路径（架构亮点）**：ASR / OCR / Embedding 三类推理各有一个 `*_PROVIDER` 环境变量（取值 `local` | `api`），同一份接口、env 切后端实现。主力走 API（Groq `whisper-large-v3-turbo`），本地 `small/cpu` 作无网兜底。
- **🧠 自研 AgentLoop**：`Planner → Executor → Critic` 多轮闭环（默认 2 轮、上限 3 轮，支持最多 4 视频跨视频对比），证据强校验，Checkpoint 断点恢复——核心循环自研、边界清晰，而非全盘套 LangChain。
- **🔍 混合检索 + 引用溯源**：向量（Qdrant BGE-M3）+ 关键词（BM25）→ RRF 融合 → CrossEncoder 重排；每个结论绑定 `timestampMs + source(ASR|OCR) + 原文片段`。
- **🎟️ 零外部强依赖运行**：全链路可本地跑，断网可用、成本可控；单卡 8GB 显存串行错峰跑完 Whisper → OCR/Embedding → LLM。
- **🧩 工程化优先**：幂等、重试预算、熔断、首包探测、结构化日志、Prometheus 指标、OpenTelemetry 全链路 Trace——Day 1 落地。
- **🖥️ 全栈工作台**：React 18 + Vite + TypeScript + TailwindCSS 前端，9 个功能页，SSE 流式 Markdown 与证据卡片。
- **⏱️ ASR 中文加标点（opt-in 方案2）**：Whisper 中文不产标点，用轻量 LLM（OpenRouter free 档）给 `full_text` 补标点恢复可读性，默认关、不破坏现状、改字异常自动回退裸原文。

### 🇬🇧 English

- **🛤️ Dual-path inference (architecture highlight)**: ASR / OCR / Embedding each carry a `*_PROVIDER` env (`local` | `api`) — one interface, env-driven backend swap. Production uses the API path (Groq `whisper-large-v3-turbo`) with a local `small/cpu` fallback. 
- **🧠 In-house AgentLoop**: a `Planner → Executor → Critic` multi-round loop (default 2, up to 3, supports cross-video comparison across up to 4 videos) with hard evidence verification and checkpoint resumption — the core loop is hand-built with clear boundaries, not a wholesale LangChain wrapper.
- **🔍 Hybrid retrieval + citation provenance**: vectors (Qdrant BGE-M3) + keywords (BM25) → RRF fusion → CrossEncoder reranking; every conclusion is anchored to `timestampMs + source(ASR|OCR) + raw fragment`.
- **🎟️ Zero hard external dependency**: the full pipeline runs locally, works offline, and stays cost-controlled; a single 8 GB GPU walks Whisper → OCR/Embedding → LLM in serialized, peak-shifted stages.
- **🧩 Engineering-first**: idempotency, retry budget, circuit breaking, first-packet probe, structured logging, Prometheus metrics, OpenTelemetry end-to-end tracing — landed from Day 1.
- **🖥️ Full-stack workbench**: React 18 + Vite + TypeScript + TailwindCSS frontend with 9 feature pages, SSE-streamed Markdown, and evidence cards.
- **⏱️ ASR Chinese punctuation (opt-in, Option 2)**: Whisper emits no Chinese punctuation, so a lightweight LLM (OpenRouter free tier) re-punctuates `full_text` to restore readability. Off by default — non-invasive, and falls back to raw text on any anomaly.

### 🇯🇵 日本語

- **🛤️ 二重推論パス（アーキテクチャのハイライト）**: ASR / OCR / Embedding の各推論に `*_PROVIDER` 環境変数（値: `local` | `api`）を用意し、同一インターフェースで環境変数だけでバックエンドを切り替え可能。本番は API パス（Groq `whisper-large-v3-turbo`）を主力とし、ローカル `small/cpu` をオフライン時のフォールバックとして用意。
- **🧠 自社開発 AgentLoop**: `Planner → Executor → Critic` の多段階クローズドループ（デフォルト 2 往復、上限 3 往復、最大 4 動画のクロスビデオ比較対応）、堅牢なエビデンス検証、チェックポイントによる中断復帰——コアループは自前実装で境界が明確、LangChain の丸ごとラッパーではない。
- **🔍 ハイブリッド検索 + 引用元追跡**: ベクトル（Qdrant BGE-M3）+ キーワード（BM25）→ RRF 融合 → CrossEncoder 再ランキング；全結論に `timestampMs + source(ASR|OCR) + 原文フラグメント` を紐付け。
- **🎟️ ハードな外部依存ゼロ**: パイプライン全体がローカルで完結、オフライン動作可能、コスト管理可能；単一 8 GB GPU で Whisper → OCR/Embedding → LLM を直列・ピークシフト実行。
- **🧩 エンジニアリングファースト**: 冪等性、リトライ予算、サーキットブレーカー、初回パケットプローブ、構造化ログ、Prometheus メトリクス、OpenTelemetry エンドツーエンドトレーシング——初日から導入。
- **🖥️ フルスタックワークベンチ**: React 18 + Vite + TypeScript + TailwindCSS フロントエンド、9 機能ページ、SSE ストリーミング Markdown とエビデンスカード。
- **⏱️ ASR 日本語句読点補完（opt-in、方式 2）**: Whisper は日本語で句読点を出力しないため、軽量 LLM（OpenRouter 無料枠）で `full_text` に句読点を補完し可読性を復元。デフォルト無効で既存動作を壊さず、異常時は生テキストへ自動フォールバック。

---

## 🖼️ プロジェクトスクリーンショット / Screenshots / プロジェクトスクリーンショット

### 🇨🇳 中文

下方为运行时截图，展示 VideoMind 工作台与管线进度：

### 🇬🇧 English

The screenshots below show the VideoMind workbench and pipeline progress at runtime:

### 🇯🇵 日本語

以下は実行時のスクリーンショットで、VideoMind ワークベンチとパイプライン進捗を示しています：

<table>
  <tr>
    <td align="center"><b>截图 1 / Screenshot 1</b></td>
    <td align="center"><b>截图 2 / Screenshot 2</b></td>
    <td align="center"><b>截图 3 / Screenshot 3</b></td>
  </tr>
  <tr>
    <td align="center"><img src="屏幕截图_6-8-2026_55439_127.0.0.1.jpeg" alt="VideoMind Screenshot 1" width="480"></td>
    <td align="center"><img src="屏幕截图_6-8-2026_55518_127.0.0.1.jpeg" alt="VideoMind Screenshot 2" width="480"></td>
    <td align="center"><img src="屏幕截图_6-8-2026_55536_127.0.0.1.jpeg" alt="VideoMind Screenshot 3" width="480"></td>
  </tr>
  <tr>
    <td align="center"><b>截图 4 / Screenshot 4</b></td>
    <td align="center"><b>截图 5 / Screenshot 5</b></td>
    <td align="center"><b>截图 6 / Screenshot 6</b></td>
  </tr>
  <tr>
    <td align="center"><img src="屏幕截图_6-8-2026_61925_127.0.0.1.jpeg" alt="VideoMind Screenshot 4" width="480"></td>
    <td align="center"><img src="屏幕截图_6-8-2026_62147_127.0.0.1.jpeg" alt="VideoMind Screenshot 5" width="480"></td>
    <td align="center"><img src="屏幕截图_6-8-2026_62223_127.0.0.1.jpeg" alt="VideoMind Screenshot 6" width="480"></td>
  </tr>
  <tr>
    <td align="center"><b>截图 7 / Screenshot 7</b></td>
    <td align="center"><b>截图 8 / Screenshot 8</b></td>
    <td align="center"></td>
  </tr>
  <tr>
    <td align="center"><img src="屏幕截图_6-8-2026_72637_127.0.0.1.jpeg" alt="VideoMind Screenshot 7" width="480"></td>
    <td align="center"><img src="屏幕截图_6-8-2026_7472_127.0.0.1.jpeg" alt="VideoMind Screenshot 8" width="480"></td>
    <td align="center"></td>
  </tr>
</table>

> ℹ️ 截图文件名含中文与空格，在 Markdown 中以 URL 编码（`%20`）引用。 / Screenshot filenames contain Chinese characters and spaces; they are referenced URL-encoded (`%20`) in Markdown.

---

## 🏗️ アーキテクチャ概要 / Architecture Overview / アーキテクチャ概要

### 🇨🇳 中文

分层 DDD 架构，每层职责可讲清边界：

```
┌──────────────────────────────────────────────────────────────────┐
│  Interface 层 · FastAPI + SSE 实时进度 / OpenAPI 文档            │
├──────────────────────────────────────────────────────────────────┤
│  Application 层 · Celery 任务编排 + GPU 资源管理 + 状态机/幂等    │
├──────────────────────────────────────────────────────────────────┤
│  Core 层 · 视频管线 / RAG 检索 / 意图路由 / AgentLoop / 模型网关  │
│   ├─ video_pipeline  ASR(local+api) OCR(local+api) 视频分段合并   │
│   ├─ rag             Qdrant+BM25→RRF→CrossEncoder→引用溯源        │
│   ├─ intent          意图识别树 + 查询改写 + 多通道检索编排       │
│   ├─ agent_loop      Planner→Executor→Critic + 证据强校验        │
│   └─ model_gateway   三态熔断 + 优先级路由 + 首包探测 + 计费      │
├──────────────────────────────────────────────────────────────────┤
│  Infrastructure 层 · Postgres+pgvector / Qdrant / MinIO / Redis   │
├──────────────────────────────────────────────────────────────────┤
│  Observability · structlog + Prometheus + OpenTelemetry Trace    │
└──────────────────────────────────────────────────────────────────┘
```

### 🇬🇧 English

A layered DDD architecture — each layer's boundary is defensible in an interview:

```
┌──────────────────────────────────────────────────────────────────┐
│  Interface · FastAPI + real-time SSE progress / OpenAPI docs     │
├──────────────────────────────────────────────────────────────────┤
│  Application · Celery orchestration + GPU management + state/idem│
├──────────────────────────────────────────────────────────────────┤
│  Core · video pipeline / RAG retrieval / intent routing /        │
│        AgentLoop / model gateway                                  │
│   ├─ video_pipeline  ASR(local+api) OCR(local+api) merge          │
│   ├─ rag             Qdrant+BM25→RRF→CrossEncoder→citations       │
│   ├─ intent          intent tree + query rewrite + multi-channel  │
│   ├─ agent_loop      Planner→Executor→Critic + evidence verify    │
│   └─ model_gateway   tri-state breaker + routing + first-packet  │
├──────────────────────────────────────────────────────────────────┤
│  Infrastructure · Postgres+pgvector / Qdrant / MinIO / Redis      │
├──────────────────────────────────────────────────────────────────┤
│  Observability · structlog + Prometheus + OpenTelemetry tracing   │
└──────────────────────────────────────────────────────────────────┘
```

### 🇯🇵 日本語

階層型 DDD アーキテクチャ——各層の境界は面接で説明可能なレベルで明確：

```
┌──────────────────────────────────────────────────────────────────┐
│  Interface 層 · FastAPI + SSE リアルタイム進捗 / OpenAPI ドキュメント│
├──────────────────────────────────────────────────────────────────┤
│  Application 層 · Celery タスクオーケストレーション + GPU 管理 + 状態/冪等│
├──────────────────────────────────────────────────────────────────┤
│  Core 層 · 動画パイプライン / RAG 検索 / 意図ルーティング /     │
│        AgentLoop / モデルゲートウェイ                              │
│   ├─ video_pipeline  ASR(local+api) OCR(local+api) 動画セグメント結合│
│   ├─ rag             Qdrant+BM25→RRF→CrossEncoder→引用追跡         │
│   ├─ intent          意図認識ツリー + クエリ書き換え + マルチチャネル検索│
│   ├─ agent_loop      Planner→Executor→Critic + エビデンス検証      │
│   └─ model_gateway   三態ブレーカー + 優先度ルーティング + 初回パケット検知│
├──────────────────────────────────────────────────────────────────┤
│  Infrastructure 層 · Postgres+pgvector / Qdrant / MinIO / Redis   │
├──────────────────────────────────────────────────────────────────┤
│  Observability · structlog + Prometheus + OpenTelemetry トレーシング│
└──────────────────────────────────────────────────────────────────┘
```

---

## 🧰 技術スタック / Tech Stack / 技術スタック

| 層级 / Layer / レイヤー | 选型 / Choice / 選定 | 关键理由 / Rationale / 選定理由 |
|---|---|---|
| **Web 框架 / Web framework / Web フレームワーク** | FastAPI + Uvicorn | 异步原生、自动 OpenAPI、SSE 原生 / async-native, auto OpenAPI, native SSE / 非同期ネイティブ、自動 OpenAPI、ネイティブ SSE |
| **异步任务 / Async tasks / 非同期タスク** | Celery + Redis | Python 生态承接编排，DB0 缓存 / 独立 broker/backend DB index / Python エコシステムでオーケストレーション、DB0 キャッシュ / 独立 broker/backend DB インデックス |
| **ASR（本地）/ ASR (local) / ASR（ローカル）** | faster-whisper (CTranslate2) | 离线高精度、int8 量化、CPU 可跑 / Offline high accuracy, int8 quantization, runs on CPU / オフライン高精度、int8 量子化、CPU 実行可能 |
| **ASR（API）/ ASR (api) / ASR（API）** | Groq OpenAI-兼容 `whisper-large-v3-turbo` | 低延迟、主力档 / Low latency, production tier / 低レイテンシ、本番用 |
| **OCR（本地）/ OCR (local) / OCR（ローカル）** | PaddleOCR | 中文识别优于 Tesseract / Chinese recognition better than Tesseract / 日本語・中国語認識で Tesseract より優秀 |
| **OCR（API）/ OCR (api) / OCR（API）** | 外部 OCR 服务 / external OCR service | env 切换 / env switch / 環境変数で切り替え |
| **Embedding（本地）/ Embedding (local) / Embedding（ローカル）** | sentence-transformers BAAI/bge-m3 | 1024 维、多语言 / 1024-dim, multilingual / 1024 次元、多言語対応 |
| **Embedding（API）/ Embedding (api) / Embedding（API）** | Ollama / OpenAI | env 切换 / env switch / 環境変数で切り替え |
| **向量检索 / Vector retrieval / ベクトル検索** | Qdrant | 纯 Rust、单节点高性能、Payload 过滤 / Pure Rust, high single-node perf, payload filtering / 純 Rust、単ノード高性能、ペイロードフィルタ |
| **关键词检索 / Keyword retrieval / キーワード検索** | Rank-BM25 + jieba | 纯 Python、CJK 分词、无额外服务 / Pure Python, CJK tokenization, no extra service / 純 Python、CJK 分かち書き、追加サービス不要 |
| **关系型存储 / Relational store / リレーショナルストア** | PostgreSQL + pgvector | 向量字段 + 关系数据一体 / Vector fields + relational data unified / ベクトルフィールド + リレーショナルデータ統合 |
| **对象存储 / Object storage / オブジェクトストレージ** | MinIO | S3 兼容、本地化 / S3 compatible, localizable / S3 互換、ローカル展開可能 |
| **缓存 / Cache / キャッシュ** | Redis | 缓存 + Celery broker/backend / Cache + Celery broker/backend / キャッシュ + Celery broker/backend |
| **加标点 / Punctuation / 句読点補完** | OpenRouter 轻量 LLM（opt-in） | Whisper 中文标点后处理 / Whisper Chinese punctuation post-processing / Whisper 日本語句読点後処理 |
| **前端 / Frontend / フロントエンド** | React 18 + Vite + TypeScript + TailwindCSS | Radix UI + TanStack Query + Zustand + React Router 6 |
| **可观测性 / Observability / 可観測性** | Prometheus + OpenTelemetry + structlog | 指标 + Trace + 结构化日志 / Metrics + Traces + Structured logs / メトリクス + トレース + 構造化ログ |
| **包管理 / Package manager / パッケージマネージャー** | `uv` + hatchling | 极快依赖解析 / Blazing-fast dependency resolution / 超高速依存解決 |

---

## 🚀 クイックスタート / Quick Start / クイックスタート

### 🇨🇳 中文

```bash
# 1. 克隆仓库 / Clone
git clone https://github.com/zhudalai/videomind-python.git
cd videomind-python

# 2. 安装依赖（用 uv）/ Install deps with uv
uv sync

# 3. 复制环境变量模板 / Copy env template
cp .env.example .env
#   按需填写：ASR/OCR/EMBEDDING 的 provider、API key、模型名 / Fill provider, keys, models

# 4. 启动基础设施（Postgres/Redis/Qdrant/MinIO）/ Start infra
docker compose -f docker/docker-compose.yml up -d

# 5. 运行数据库迁移 / Run migrations
alembic upgrade head

# 6. 启动 API 服务 / Start API
uv run uvicorn videomind.interface.app:app --reload --port 8011

# 7. 启动 Celery Worker（新终端）/ Start Celery workers (new terminal)
#    Windows 必须 -P solo（prefork 在 Win 上 _loc race 卡死）
uv run celery -A videomind.application.task_orchestration.celery worker -Q gpu -c 1 -P solo
uv run celery -A videomind.application.task_orchestration.celery worker -Q cpu -c 4 -P solo

# 8. 启动前端（新终端）/ Start frontend (new terminal)
cd frontend
npm install
npm run dev
```

启动后访问：

- 📖 API 文档 / API docs：http://localhost:8011/docs
- 🖥️ 前端工作台 / Frontend workbench：http://localhost:4000

### 🇬🇧 English

```bash
# 1. Clone
git clone https://github.com/zhudalai/videomind-python.git
cd videomind-python

# 2. Install deps with uv
uv sync

# 3. Copy env template
cp .env.example .env
#   Fill in: ASR/OCR/EMBEDDING provider, API key, model name

# 4. Start infra (Postgres/Redis/Qdrant/MinIO)
docker compose -f docker/docker-compose.yml up -d

# 5. Run migrations
alembic upgrade head

# 6. Start API
uv run uvicorn videomind.interface.app:app --reload --port 8011

# 7. Start Celery workers (new terminal)
#    On Windows you MUST use -P solo (prefork hits a _loc race and hangs)
uv run celery -A videomind.application.task_orchestration.celery worker -Q gpu -c 1 -P solo
uv run celery -A videomind.application.task_orchestration.celery worker -Q cpu -c 4 -P solo

# 8. Start frontend (new terminal)
cd frontend
npm install
npm run dev
```

After launch, visit:

- 📖 API docs: http://localhost:8011/docs
- 🖥️ Frontend workbench: http://localhost:4000

### 🇯🇵 日本語

```bash
# 1. クローン / Clone
git clone https://github.com/zhudalai/videomind-python.git
cd videomind-python

# 2. 依存関係インストール（uv 使用）/ Install deps with uv
uv sync

# 3. 環境変数テンプレートコピー / Copy env template
cp .env.example .env
#   必要に応じて記入：ASR/OCR/EMBEDDING の provider、API キー、モデル名 / Fill provider, keys, models

# 4. インフラ起動（Postgres/Redis/Qdrant/MinIO）/ Start infra
docker compose -f docker/docker-compose.yml up -d

# 5. データベースマイグレーション実行 / Run migrations
alembic upgrade head

# 6. API サービス起動 / Start API
uv run uvicorn videomind.interface.app:app --reload --port 8011

# 7. Celery Worker 起動（新規ターミナル）/ Start Celery workers (new terminal)
#    Windows では -P solo 必須（prefork は Win 上で _loc race してハング）
uv run celery -A videomind.application.task_orchestration.celery worker -Q gpu -c 1 -P solo
uv run celery -A videomind.application.task_orchestration.celery worker -Q cpu -c 4 -P solo

# 8. フロントエンド起動（新規ターミナル）/ Start frontend (new terminal)
cd frontend
npm install
npm run dev
```

起動後アクセス：

- 📖 API ドキュメント / API docs：http://localhost:8011/docs
- 🖥️ フロントエンドワークベンチ / Frontend workbench：http://localhost:4000

---

## ⚙️ 設定説明 / Configuration / 設定説明

### 🇨🇳 中文

配置通过 pydantic-settings v2 从 `.env` 注入，模板见 [.env.example](.env.example)。核心是**推理双路径**：

| 变量 / Variable | 取值 / Values | 说明 / Description |
|---|---|---|
| `ASR_PROVIDER` | `local` \| `api` | local=faster-whisper，api=Groq 转写 |
| `OCR_PROVIDER` | `local` \| `api` | local=PaddleOCR，api=外部 OCR |
| `EMBEDDING_PROVIDER` | `local` \| `api` | local=sentence-transformers，api=ollama/openai |
| `ASR_MODEL` | `tiny`/`small`/`...`/`large-v3-turbo` | 本地档位；想要 top 质量需装 CUDA |
| `ASR_DEVICE` | `auto`/`cuda`/`cpu` | 无 CUDA 库时用 cpu |
| `ASR_COMPUTE_TYPE` | `int8`/`int8_float16`/`float16` | CPU=int8 / GPU=int8_float16 |
| `ASR_API_BASE_URL` | URL | API 路径底址（如 Groq） |
| `ASR_API_KEY` | string | API 路径密钥 |
| `ASR_API_MODEL` | `whisper-large-v3-turbo` | API 路径模型 |
| `ASR_PUNCTUATE` | `true`/`false` | 中文加标点后处理（默认关） |
| `ASR_PUNCTUATE_MODEL` | OpenRouter 模型名 | 默认 `inclusionai/ling-3.0-flash:free` |
| `ASR_PUNCTUATE_MAX_TOKENS` | int | 默认 8192（reasoning 模型思考吃 token，须 ≥4096） |
| `RERANK_PROVIDER` | `off` \| `api` \| `local` | off=固定权重；api=OpenRouter cross-encoder；local=BGE-reranker-v2-m3 |
| `RERANK_TOP_N` | int | cross-encoder 重排保留数（默认 60，匹配宽召回） |
| `DATABASE_URL` | 连接串 | PostgreSQL+asyncpg |
| `REDIS_URL` / `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | URL | DB0 缓存 / DB1 broker / DB2 result |

> ⚠️ **dotenv 陷阱**：`.env` 中 `KEY=  # 注释` 会把 `# 注释` 当字面值；空值的注释要单列一行（已在 `.env.example` 处理）。

### 🇬🇧 English

Config is injected from `.env` via pydantic-settings v2; see the template at [.env.example](.env.example). The core is the **dual-path inference**:

| Variable | Values | Description |
|---|---|---|
| `ASR_PROVIDER` | `local` \| `api` | local=faster-whisper; api=Groq transcription |
| `OCR_PROVIDER` | `local` \| `api` | local=PaddleOCR; api=external OCR |
| `EMBEDDING_PROVIDER` | `local` \| `api` | local=sentence-transformers; api=ollama/openai |
| `ASR_MODEL` | `tiny`/`small`/`...`/`large-v3-turbo` | Local tier; top quality needs CUDA |
| `ASR_DEVICE` | `auto`/`cuda`/`cpu` | Use `cpu` without CUDA libs |
| `ASR_COMPUTE_TYPE` | `int8`/`int8_float16`/`float16` | CPU=int8 / GPU=int8_float16 |
| `ASR_API_BASE_URL` | URL | API path base (e.g. Groq) |
| `ASR_API_KEY` | string | API path key |
| `ASR_API_MODEL` | `whisper-large-v3-turbo` | API path model |
| `ASR_PUNCTUATE` | `true`/`false` | Chinese punctuation post-processing (off by default) |
| `ASR_PUNCTUATE_MODEL` | OpenRouter model name | default `inclusionai/ling-3.0-flash:free` |
| `ASR_PUNCTUATE_MAX_TOKENS` | int | default 8192 (reasoning models burn tokens thinking, need ≥4096) |
| `RERANK_PROVIDER` | `off` \| `api` \| `local` | off=fixed-weight; api=OpenRouter cross-encoder; local=BGE-reranker-v2-m3 |
| `RERANK_TOP_N` | int | cross-encoder keep count (default 60, matches wide recall) |
| `DATABASE_URL` | conn str | PostgreSQL+asyncpg |
| `REDIS_URL` / `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | URL | DB0 cache / DB1 broker / DB2 result |

> ⚠️ **dotenv trap**: `KEY=  # comment` in `.env` reads `# comment` as a literal value; put comments for empty keys on their own line (handled in `.env.example`).

### 🇯🇵 日本語

設定は pydantic-settings v2 で `.env` から注入されます。テンプレートは [.env.example](.env.example) を参照。核心は**二重推論パス**：

| 変数 / Variable | 値 / Values | 説明 / Description |
|---|---|---|
| `ASR_PROVIDER` | `local` \| `api` | local=faster-whisper、api=Groq 転写 |
| `OCR_PROVIDER` | `local` \| `api` | local=PaddleOCR、api=外部 OCR |
| `EMBEDDING_PROVIDER` | `local` \| `api` | local=sentence-transformers、api=ollama/openai |
| `ASR_MODEL` | `tiny`/`small`/`...`/`large-v3-turbo` | ローカル階層；最高品質には CUDA 必須 |
| `ASR_DEVICE` | `auto`/`cuda`/`cpu` | CUDA ライブラリなし時は `cpu` |
| `ASR_COMPUTE_TYPE` | `int8`/`int8_float16`/`float16` | CPU=int8 / GPU=int8_float16 |
| `ASR_API_BASE_URL` | URL | API パス基底 URL（例: Groq） |
| `ASR_API_KEY` | string | API パスキー |
| `ASR_API_MODEL` | `whisper-large-v3-turbo` | API パスモデル |
| `ASR_PUNCTUATE` | `true`/`false` | 日本語句読点補完後処理（デフォルト無効） |
| `ASR_PUNCTUATE_MODEL` | OpenRouter モデル名 | デフォルト `inclusionai/ling-3.0-flash:free` |
| `ASR_PUNCTUATE_MAX_TOKENS` | int | デフォルト 8192（推論モデルは思考でトークン消費、≥4096 必須） |
| `RERANK_PROVIDER` | `off` \| `api` \| `local` | off=固定重み；api=OpenRouter cross-encoder；local=BGE-reranker-v2-m3 |
| `RERANK_TOP_N` | int | cross-encoder 保持数（デフォルト 60、広いリコールに対応） |
| `DATABASE_URL` | 接続文字列 | PostgreSQL+asyncpg |
| `REDIS_URL` / `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | URL | DB0 キャッシュ / DB1 broker / DB2 result |

> ⚠️ **dotenv の落とし穴**: `.env` で `KEY=  # コメント` と書くと `# コメント` がリテラル値として読まれる；空値のコメントは別行に記述（`.env.example` で対応済み）。

---

## 🧪 テスト / Testing / テスト

### 🇨🇳 中文

```bash
# 全量 / All tests
uv run pytest

# 仅单测（跳过需真实基础设施的 e2e/infra）/ Unit tests only
uv run pytest -m "not e2e and not infra"

# 覆盖率 / Coverage
uv run pytest --cov=src/videomind
```

测试标记：`e2e`（需真实基础设施的端到端）、`infra`（需真实基础设施的集成）。

### 🇬🇧 English

```bash
# All tests
uv run pytest

# Unit tests only (skip e2e/infra that need real services)
uv run pytest -m "not e2e and not infra"

# Coverage
uv run pytest --cov=src/videomind
```

Markers: `e2e` (end-to-end needing real infra), `infra` (integration needing real infra).

### 🇯🇵 日本語

```bash
# 全テスト / All tests
uv run pytest

# 単体テストのみ（実インフラが必要な e2e/infra をスキップ）/ Unit tests only
uv run pytest -m "not e2e and not infra"

# カバレッジ / Coverage
uv run pytest --cov=src/videomind
```

マーカー: `e2e` (実インフラ必須のエンドツーエンド)、`infra` (実インフラ必須の統合)。

---

## 🌐 フロントエンドページ / Frontend Pages / フロントエンドページ

| 路由 / Route / ルート | 页面 / Page / ページ | 说明 / Description / 説明 |
|---|---|---|
| `/` | Dashboard | 总览仪表盘 / Overview dashboard / 概要ダッシュボード |
| `/upload` | VideoUpload | 视频上传 / Video upload / 動画アップロード |
| `/videos` | VideoLibrary | 视频库 / Video library / 動画ライブラリ |
| `/videos/:id` | VideoDetail | 视频详情 / Video detail / 動画詳細 |
| `/videos/:id/progress` | PipelineProgress | 管线进度（SSE）/ Pipeline progress (SSE) / パイプライン進捗 (SSE) |
| `/chat` | RAGChat | RAG 问答 / RAG chat / RAG チャット |
| `/analysis` | AgentAnalysis | Agent 工作台 / Agent workbench / Agent ワークベンチ |
| `/health` | HealthDashboard | 健康监控 / Health dashboard / ヘルスダッシュボード |
| `/settings` | Settings | 设置 / Settings / 設定 |

前端技术栈：React 18 · Vite · TypeScript · TailwindCSS · Radix UI · TanStack Query · Zustand · React Router 6 · Recharts。

Frontend stack: React 18 · Vite · TypeScript · TailwindCSS · Radix UI · TanStack Query · Zustand · React Router 6 · Recharts.

フロントエンドスタック: React 18 · Vite · TypeScript · TailwindCSS · Radix UI · TanStack Query · Zustand · React Router 6 · Recharts。

---

## 📁 ディレクトリ構造 / Directory Structure / ディレクトリ構造

```
videomind-python/
├── src/videomind/
│   ├── interface/          # FastAPI 路由、SSE、DTO / routes, SSE, DTOs / FastAPI ルート、SSE、DTO
│   ├── application/        # Celery 编排、任务状态机 / Celery orchestration / Celery オーケストレーション、タスク状態機
│   ├── core/
│   │   ├── video_pipeline/ # ASR / OCR / 分段合并 / punctuate / ASR / OCR / セグメント結合 / 句読点補完
│   │   ├── rag/            # 混合检索 + RRF + 重排 + 引用溯源 / hybrid retrieval + RRF + rerank + citations / ハイブリッド検索 + RRF + 再ランキング + 引用追跡
│   │   ├── intent/         # 意图识别树 / 查询改写 / intent tree / query rewrite / 意図認識ツリー / クエリ書き換え
│   │   ├── agent_loop/     # Planner / Executor / Critic + 证据校验 / Planner / Executor / Critic + evidence verify / Planner / Executor / Critic + エビデンス検証
│   │   ├── model_gateway/  # 三态熔断 / 优先级路由 / 首包探测 / tri-state breaker / priority routing / first-packet probe / 三態ブレーカー / 優先度ルーティング / 初回パケット検知
│   │   └── ...
│   ├── infrastructure/     # cache / media / storage / vector / cache / media / storage / vector / キャッシュ / メディア / ストレージ / ベクトル
│   ├── observability/      # structlog / Prometheus / OTel / structlog / Prometheus / OTel
│   └── config.py           # pydantic-settings 双路径配置 / pydantic-settings dual-path config / pydantic-settings 二重パス設定
├── tests/                  # 单测 / 集成 / e2e / unit / integration / e2e / 単体 / 統合 / e2e
├── alembic/                # 数据库迁移 / DB migrations / DB マイグレーション
├── docker/                 # docker-compose（仅存储类服务）/ docker-compose (storage services only) / docker-compose（ストレージ系サービスのみ）
├── docs/                   # 13 篇架构/设计文档 / design docs / 13 架のアーキテクチャ/設計ドキュメント
├── frontend/               # React + Vite + TS 前端 / React + Vite + TS frontend / React + Vite + TS フロントエンド
├── pyproject.toml          # 依赖与工具配置 / deps & tooling / 依存関係とツール設定
├── .env.example            # 环境变量模板 / env template / 環境変数テンプレート
└── README.md
```

---

## 📚 ドキュメント索引 / Documentation Index / ドキュメント索引

中文设计文档位于 [docs/](docs/)：

| 文档 / Doc / ドキュメント | 核心内容 / Core content / 核心内容 |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | 分层、数据流、技术选型理由、GPU 调度 / layering, data flow, tech choice, GPU scheduling / 階層、データフロー、技術選定理由、GPU スケジューリング |
| [DATA-MODEL.md](docs/DATA-MODEL.md) | SQLAlchemy 表、ER 图、索引、迁移 / models, ER, indexes, migrations / SQLAlchemy テーブル、ER 図、インデックス、マイグレーション |
| [VIDEO-PIPELINE.md](docs/VIDEO-PIPELINE.md) | 下载→转码→分段 ASR→关键帧 OCR→合并 / pipeline stages / ダウンロード→トランスコード→セグメント ASR→キーフレーム OCR→結合 |
| [RAG-RETRIEVAL.md](docs/RAG-RETRIEVAL.md) | 向量+BM25→RRF→CrossEncoder→引用溯源 / hybrid retrieval / ベクトル+BM25→RRF→CrossEncoder→引用追跡 |
| [AGENT-LOOP.md](docs/AGENT-LOOP.md) | Planner→Executor→Critic + 证据强校验 + Checkpoint / Planner→Executor→Critic + hard evidence verification + Checkpoint / Planner→Executor→Critic + 堅牢なエビデンス検証 + チェックポイント |
| [MODEL-GATEWAY.md](docs/MODEL-GATEWAY.md) | 三态熔断、优先级路由、首包探测、计费 / model gateway / 三態ブレーカー、優先度ルーティング、初回パケット検知、課金 |
| [TASK-ORCHESTRATION.md](docs/TASK-ORCHESTRATION.md) | Celery+Redis、状态机、幂等、重试预算、SSE / Celery+Redis, state machine, idempotency, retry budget, SSE / Celery+Redis、状態機、冪等性、リトライ予算、SSE |
| [INTENT-ROUTING.md](docs/INTENT-ROUTING.md) | 意图识别树、查询改写、多通道检索、MCP / intent tree, query rewrite, multi-channel retrieval, MCP / 意図認識ツリー、クエリ書き換え、マルチチャネル検索、MCP |
| [FRONTEND.md](docs/FRONTEND.md) | 前端工作台、流式 Markdown、证据卡片 / frontend workbench / フロントエンドワークベンチ、ストリーミング Markdown、エビデンスカード |
| [SECURITY.md](docs/SECURITY.md) | JWT、API Key AES-GCM 加密、限流、审计 / security / JWT、API Key AES-GCM 暗号化、レート制限、監査 |
| [OBSERVABILITY.md](docs/OBSERVABILITY.md) | 结构化日志、Prometheus、Trace、评测框架 / observability / 構造化ログ、Prometheus、トレース、評価フレームワーク |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Docker Compose、环境变量、GPU 直通、本地化部署 / deployment / Docker Compose、環境変数、GPU パススルー、ローカル展開 |
| [INTERVIEW-GUIDE.md](docs/INTERVIEW-GUIDE.md) | 面试话术、STAR 拆解、常见追问 / interview prep / 面接トーク、STAR 分解、よくある深掘り質問 |

---

## 📄 ライセンス / License / ライセンス

[MIT License](LICENSE) © VideoMind

