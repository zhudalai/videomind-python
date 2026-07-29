<div align="center">

# VideoMind

**📹 Agentic RAG 视频理解平台 / Agentic RAG Video Understanding Platform**

> 用户上传视频或链接 → 本地 / API 双路径 ASR + OCR → 混合检索 → 自研 AgentLoop 多轮分析 → 带时间戳证据的结构化结论
>
> Upload a video or link → dual-path ASR + OCR → hybrid retrieval → an in-house AgentLoop multi-round analysis → structured conclusions backed by timestamped evidence



[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Celery](https://img.shields.io/badge/Celery-5.4%2B-37814A?logo=celery&logoColor=white)](https://docs.celeryq.dev/)
[![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=white)](https://react.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

</div>

---

## 📑 目录 / Table of Contents

- [✨ 核心亮点 / Highlights](#-核心亮点--highlights)
- [🖼️ 项目截图 / Screenshots](#️-项目截图--screenshots)
- [🏗️ 架构概览 / Architecture Overview](#️-架构概览--architecture-overview)
- [🧰 技术栈 / Tech Stack](#-技术栈--tech-stack)
- [🚀 快速开始 / Quick Start](#-快速开始--quick-start)
- [⚙️ 配置说明 / Configuration](#️-配置说明--configuration)
- [🧪 测试 / Testing](#-测试--testing)
- [🌐 前端页面 / Frontend Pages](#-前端页面--frontend-pages)
- [📁 目录结构 / Directory Structure](#-目录结构--directory-structure)
- [📚 文档索引 / Documentation Index](#-文档索引--documentation-index)
- [📄 许可证 / License](#-许可证--license)

---

## ✨ 核心亮点 / Highlights

### 🇨🇳 中文

- **🛤️ 推理双路径（架构亮点）**：ASR / OCR / Embedding 三类推理各有一个 `*_PROVIDER` 环境变量（取值 `local` | `api`），同一份接口、env 切后端实现。主力走 API（Groq `whisper-large-v3-turbo`），本地 `small/cpu` 作无网兜底——求职面试可讲清「为什么不用单路径，双路径的代价与收益」。
- **🧠 自研 AgentLoop**：`Planner → Executor → Critic` 两轮闭环，证据强校验，Checkpoint 断点恢复——核心循环自研、边界清晰，而非全盘套 LangChain。
- **🔍 混合检索 + 引用溯源**：向量（Qdrant BGE-M3）+ 关键词（BM25）→ RRF 融合 → CrossEncoder 重排；每个结论绑定 `timestampMs + source(ASR|OCR) + 原文片段`。
- **🎟️ 零外部强依赖运行**：全链路可本地跑，断网可用、成本可控；单卡 8GB 显存串行错峰跑完 Whisper → OCR/Embedding → LLM。
- **🧩 工程化优先**：幂等、重试预算、熔断、首包探测、结构化日志、Prometheus 指标、OpenTelemetry 全链路 Trace——Day 1 落地。
- **🖥️ 全栈工作台**：React 18 + Vite + TypeScript + TailwindCSS 前端，9 个功能页，SSE 流式 Markdown 与证据卡片。
- **⏱️ ASR 中文加标点（opt-in 方案2）**：Whisper 中文不产标点，用轻量 LLM（OpenRouter free 档）给 `full_text` 补标点恢复可读性，默认关、不破坏现状、改字异常自动回退裸原文。

### 🇬🇧 English

- **🛤️ Dual-path inference (architecture highlight)**: ASR / OCR / Embedding each carry a `*_PROVIDER` env (`local` | `api`) — one interface, env-driven backend swap. Production uses the API path (Groq `whisper-large-v3-turbo`) with a local `small/cpu` fallback. Interview-ready: "why not single-path, and the trade-offs of dual-path."
- **🧠 In-house AgentLoop**: a `Planner → Executor → Critic` two-round loop with hard evidence verification and checkpoint resumption — the core loop is hand-built with clear boundaries, not a wholesale LangChain wrapper.
- **🔍 Hybrid retrieval + citation provenance**: vectors (Qdrant BGE-M3) + keywords (BM25) → RRF fusion → CrossEncoder reranking; every conclusion is anchored to `timestampMs + source(ASR|OCR) + raw fragment`.
- **🎟️ Zero hard external dependency**: the full pipeline runs locally, works offline, and stays cost-controlled; a single 8 GB GPU walks Whisper → OCR/Embedding → LLM in serialized, peak-shifted stages.
- **🧩 Engineering-first**: idempotency, retry budget, circuit breaking, first-packet probe, structured logging, Prometheus metrics, OpenTelemetry end-to-end tracing — landed from Day 1.
- **🖥️ Full-stack workbench**: React 18 + Vite + TypeScript + TailwindCSS frontend with 9 feature pages, SSE-streamed Markdown, and evidence cards.
- **⏱️ ASR Chinese punctuation (opt-in, Option 2)**: Whisper emits no Chinese punctuation, so a lightweight LLM (OpenRouter free tier) re-punctuates `full_text` to restore readability. Off by default — non-invasive, and falls back to raw text on any anomaly.

---

## 🖼️ 项目截图 / Screenshots

### 🇨🇳 中文

下方为运行时截图，展示 VideoMind 工作台与管线进度：

### 🇬🇧 English

The screenshots below show the VideoMind workbench and pipeline progress at runtime:

<table>
  <tr>
    <td align="center"><b>截图 1 / Screenshot 1</b></td>
    <td align="center"><b>截图 2 / Screenshot 2</b></td>
  </tr>
  <tr>
    <td align="center"><img src="屏幕截图%202026-07-29%20073534.png" alt="VideoMind Screenshot 1" width="480"></td>
    <td align="center"><img src="屏幕截图%202026-07-29%20073624.png" alt="VideoMind Screenshot 2" width="480"></td>
  </tr>
</table>

> ℹ️ 截图文件名含中文与空格，在 Markdown 中以 URL 编码（`%20`）引用。 / Screenshot filenames contain Chinese characters and spaces; they are referenced URL-encoded (`%20`) in Markdown.

---

## 🏗️ 架构概览 / Architecture Overview

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

---

## 🧰 技术栈 / Tech Stack

| 层级 / Layer | 选型 / Choice | 关键理由 / Rationale |
|---|---|---|
| **Web 框架 / Web framework** | FastAPI + Uvicorn | 异步原生、自动 OpenAPI、SSE 原生 / async-native, auto OpenAPI, native SSE |
| **异步任务 / Async tasks** | Celery + Redis | Python 生态承接编排，DB0 缓存 / 独立 broker/backend DB index |
| **ASR（本地）/ ASR (local)** | faster-whisper (CTranslate2) | 离线高精度、int8 量化、CPU 可跑 |
| **ASR（API）/ ASR (api)** | Groq OpenAI-兼容 `whisper-large-v3-turbo` | 低延迟、主力档 |
| **OCR（本地）/ OCR (local)** | PaddleOCR | 中文识别优于 Tesseract |
| **OCR（API）/ OCR (api)** | 外部 OCR 服务 / external OCR service | env 切换 |
| **Embedding（本地）/ Embedding (local)** | sentence-transformers BAAI/bge-m3 | 1024 维、多语言 |
| **Embedding（API）/ Embedding (api)** | Ollama / OpenAI | env 切换 |
| **向量检索 / Vector retrieval** | Qdrant | 纯 Rust、单节点高性能、Payload 过滤 |
| **关键词检索 / Keyword retrieval** | Rank-BM25 | 纯 Python、无额外服务 |
| **关系型存储 / Relational store** | PostgreSQL + pgvector | 向量字段 + 关系数据一体 |
| **对象存储 / Object storage** | MinIO | S3 兼容、本地化 |
| **缓存 / 缓存** | Redis | 缓存 + Celery broker/backend |
| **加标点 / Punctuation** | OpenRouter 轻量 LLM（opt-in） | Whisper 中文标点后处理 |
| **前端 / Frontend** | React 18 + Vite + TypeScript + TailwindCSS | Radix UI + TanStack Query + Zustand + React Router 6 |
| **可观测性 / Observability** | Prometheus + OpenTelemetry + structlog | 指标 + Trace + 结构化日志 |
| **包管理 / Package manager** | `uv` + hatchling | 极快依赖解析 |

---

## 🚀 快速开始 / Quick Start

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
uv run uvicorn videomind.interface.app:app --reload

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

- 📖 API 文档 / API docs：http://localhost:8000/docs
- 🖥️ 前端工作台 / Frontend workbench：http://localhost:5173

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
uv run uvicorn videomind.interface.app:app --reload

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

- 📖 API docs: http://localhost:8000/docs
- 🖥️ Frontend workbench: http://localhost:5173

---

## ⚙️ 配置说明 / Configuration

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
| `DATABASE_URL` | conn str | PostgreSQL+asyncpg |
| `REDIS_URL` / `CELERY_BROKER_URL` / `CELERY_RESULT_BACKEND` | URL | DB0 cache / DB1 broker / DB2 result |

> ⚠️ **dotenv trap**: `KEY=  # comment` in `.env` reads `# comment` as a literal value; put comments for empty keys on their own line (handled in `.env.example`).

---

## 🧪 测试 / Testing

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

---

## 🌐 前端页面 / Frontend Pages

| 路由 / Route | 页面 / Page | 说明 / Description |
|---|---|---|
| `/` | Dashboard | 总览仪表盘 / Overview dashboard |
| `/upload` | VideoUpload | 视频上传 / Video upload |
| `/videos` | VideoLibrary | 视频库 / Video library |
| `/videos/:id` | VideoDetail | 视频详情 / Video detail |
| `/videos/:id/progress` | PipelineProgress | 管线进度（SSE）/ Pipeline progress (SSE) |
| `/chat` | RAGChat | RAG 问答 / RAG chat |
| `/analysis` | AgentAnalysis | Agent 工作台 / Agent workbench |
| `/health` | HealthDashboard | 健康监控 / Health dashboard |
| `/settings` | Settings | 设置 / Settings |

前端技术栈：React 18 · Vite · TypeScript · TailwindCSS · Radix UI · TanStack Query · Zustand · React Router 6 · Recharts。

Frontend stack: React 18 · Vite · TypeScript · TailwindCSS · Radix UI · TanStack Query · Zustand · React Router 6 · Recharts.

---

## 📁 目录结构 / Directory Structure

```
videomind-python/
├── src/videomind/
│   ├── interface/          # FastAPI 路由、SSE、DTO / routes, SSE, DTOs
│   ├── application/        # Celery 编排、任务状态机 / Celery orchestration
│   ├── core/
│   │   ├── video_pipeline/ # ASR / OCR / 分段合并 / punctuate
│   │   ├── rag/            # 混合检索 + RRF + 重排 + 引用溯源
│   │   ├── intent/         # 意图识别树 / 查询改写
│   │   ├── agent_loop/     # Planner / Executor / Critic + 证据校验
│   │   ├── model_gateway/  # 三态熔断 / 优先级路由 / 首包探测
│   │   └── ...
│   ├── infrastructure/     # cache / media / storage / vector
│   ├── observability/      # structlog / Prometheus / OTel
│   └── config.py           # pydantic-settings 双路径配置
├── tests/                  # 单测 / 集成 / e2e
├── alembic/                # 数据库迁移 / DB migrations
├── docker/                 # docker-compose（仅存储类服务）
├── docs/                   # 13 篇架构/设计文档 / design docs
├── frontend/               # React + Vite + TS 前端
├── pyproject.toml          # 依赖与工具配置 / deps & tooling
├── .env.example            # 环境变量模板 / env template
└── README.md
```

---

## 📚 文档索引 / Documentation Index

中文设计文档位于 [docs/](docs/)：

| 文档 / Doc | 核心内容 / Core content |
|---|---|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | 分层、数据流、技术选型理由、GPU 调度 / layering, data flow, tech choice, GPU scheduling |
| [DATA-MODEL.md](docs/DATA-MODEL.md) | SQLAlchemy 表、ER 图、索引、迁移 / models, ER, indexes, migrations |
| [VIDEO-PIPELINE.md](docs/VIDEO-PIPELINE.md) | 下载→转码→分段 ASR→关键帧 OCR→合并 / pipeline stages |
| [RAG-RETRIEVAL.md](docs/RAG-RETRIEVAL.md) | 向量+BM25→RRF→CrossEncoder→引用溯源 / hybrid retrieval |
| [AGENT-LOOP.md](docs/AGENT-LOOP.md) | Planner→Executor→Critic + 证据强校验 + Checkpoint |
| [MODEL-GATEWAY.md](docs/MODEL-GATEWAY.md) | 三态熔断、优先级路由、首包探测、计费 / model gateway |
| [TASK-ORCHESTRATION.md](docs/TASK-ORCHESTRATION.md) | Celery+Redis、状态机、幂等、重试预算、SSE |
| [INTENT-ROUTING.md](docs/INTENT-ROUTING.md) | 意图识别树、查询改写、多通道检索、MCP |
| [FRONTEND.md](docs/FRONTEND.md) | 前端工作台、流式 Markdown、证据卡片 / frontend workbench |
| [SECURITY.md](docs/SECURITY.md) | JWT、API Key AES-GCM 加密、限流、审计 / security |
| [OBSERVABILITY.md](docs/OBSERVABILITY.md) | 结构化日志、Prometheus、Trace、评测框架 / observability |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Docker Compose、环境变量、GPU 直通、本地化部署 / deployment |
| [INTERVIEW-GUIDE.md](docs/INTERVIEW-GUIDE.md) | 面试话术、STAR 拆解、常见追问 / interview prep |

---

## 📄 许可证 / License

[MIT License](LICENSE) © VideoMind



