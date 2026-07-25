# VideoMind 总架构设计

> 顶层架构视图、分层设计、数据流、技术选型理由、与 Java 版对比、GPU 调度策略

---

## 1. 总览架构图

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

## 2. 分层架构设计

### 2.1 四层架构

| 层级 | 职责 | 关键组件 | 设计原则 |
|------|------|----------|----------|
| **Interface** | 对外暴露：REST API、SSE 流式、WebSocket、Admin 面板 | FastAPI Router、SSE Broadcaster、OpenAPI Schema | 协议无关、版本化、统一错误包装 |
| **Application** | 业务用例编排：认证、视频管理、RAG 检索、Agent 分析、任务调度 | `VideoService`、`RAGService`、`AgentService`、`TaskService` | 单一职责、依赖倒置、可测试 |
| **Core Services** | 核心能力实现：下载、ASR、OCR、Embedding、LLM、检索、AgentLoop、模型网关 | `Downloader`、`ASRProcessor`、`HybridRetriever`、`AgentLoop`、`RoutingLLMService` | 高内聚、可替换、可观测 |
| **Infrastructure** | 基础设施：数据库、缓存、向量库、对象存储、消息队列、监控、GPU 调度 | SQLAlchemy、Redis、Qdrant、MinIO、Celery、Prometheus、Ollama | 统一抽象、连接池、健康检查 |

### 2.2 模块依赖规则

```
Interface → Application → Core Services → Infrastructure
     ↑                                               │
     └────────────── 禁止反向依赖 ──────────────────┘
```

- **Interface** 只依赖 **Application** 抽象接口（Protocol/ABC）
- **Application** 只依赖 **Core Services** 抽象接口
- **Core Services** 只依赖 **Infrastructure** 的适配器实现
- 任何层**不得**直接 import 下层具体实现类

---

## 3. 核心数据流

### 3.1 视频入库流程

```
用户上传/粘贴链接
      │
      ▼
POST /api/videos  (创建 MediaFile 记录，状态=PENDING)
      │
      ▼
Celery Task: download_video_task
      │  ├─ yt-dlp 下载 → MinIO 存储
      │  ├─ FFmpeg 提取音频(16kHz mono) + 场景检测关键帧
      │  └─ 更新 MediaFile: duration, size, minio_path, status=DOWNLOADED
      ▼
Celery Task: transcribe_task (可并行分段)
      │  ├─ Whisper 分段 ASR (60s/段，GPU)
      │  ├─ 生成 TranscriptionChunk 记录
      │  └─ 合并全量文本 → VideoTranscription
      ▼
Celery Task: ocr_task (可并行)
      │  ├─ PaddleOCR 关键帧识别
      │  ├─ 感知哈希去重
      │  └─ 生成 FrameOCR 记录
      ▼
Celery Task: build_context_task
      │  ├─ 60s 窗口合并 ASR+OCR → VideoSegment 列表
      │  ├─ 5min Chunk 摘要+关键词+Embedding
      │  ├─ 向量入 Qdrant + 关键词入 BM25 索引
      │  └─ 更新 MediaFile.status=READY
      ▼
完成：前端 SSE 接收阶段事件 → 可进入 Agent 分析
```

### 3.2 Agent 分析流程

```
用户发起分析 (POST /api/analysis)
      │
      ▼
创建 AnalysisTask(record) → Celery: analyze_task
      │
      ▼
AgentLoop.run(goal, video_context)
      │
      ├─▶ Planner: 目标 → 1-5 个可执行子任务
      │
      ├─▶ Executor(循环 ≤2轮):
      │      ├─ 检索: HybridRetriever → TopK Evidence
      │      ├─ 生成: 结构化结论 {title, conclusions[], evidence[], suggestions[]}
      │      └─ 证据绑定: 每条 conclusion 必带 timestampMs + source(ASR|OCR) + text
      │
      ├─▶ Critic: 目标覆盖度 + 结构完整性 + 时间戳证据核验 + 无幻觉
      │      ├─ passed → 输出最终结果
      │      └─ failed → feedback + requiredTimestamps → 回 Executor 补证据 (最多 2 轮)
      │
      └─▶ Checkpoint: 每轮持久化 AgentState(PostgreSQL+Redis)
            ├─ 断点恢复：从最后一轮继续
            └─ 追问：复用 VideoContext，只换 goal 重跑 Loop
      ▼
SSE 推送: PLANNING → EXECUTING → CRITIC_CHECK → COMPLETED/FAILED
      │
      ▼
前端渲染：Markdown + 证据卡片(可跳转时间戳) + 思维导图
```

### 3.3 RAG 问答/追问流程

```
用户提问 (WebSocket/SSE)
      │
      ▼
QueryRewriter: 改写 + 子问题拆分 (LLM + 规则兜底)
      │
      ▼
IntentRouter: 树形意图分类 → 绑定知识库/工具
      │
      ▼
MultiChannelRetrieval (并行):
      ├─ VectorSearch(Qdrant, topK*2)
      ├─ BM25Search(Rank-BM25 内存索引, topK*2)
      └─ (可选) GraphSearch / MCPToolCall
      │
      ▼
RRFusion → TopK
      │
      ▼
ContextExpander: ±1 chunk 扩展上下文
      │
      ▼
Rerank: Deterministic(位置/来源/分数) → 可选 CrossEncoder
      │
      ▼
AnswerGenerator: Prompt(Context + Citations) → LLM Stream
      │
      ▼
SSE 流式返回: Answer + Citations(ChunkEvidenceID 可溯源到时间戳)
```

---

## 4. 技术选型理由表

| 领域 | 选型 | 核心理由 | 备选及放弃原因 |
|------|------|----------|----------------|
| **Web 框架** | FastAPI | 异步原生、自动 OpenAPI、SSE 原生、类型提示友好 | Flask(无异步)、Django(重)、Starlette(太底层) |
| **任务队列** | Celery + Redis | Python 生态标配、支持优先级/重试/定时、花花调度器可视化 | RQ(功能弱)、Dramatiq(社区小)、自研(造轮子) |
| **ASR** | **Whisper (openai-whisper)** | **本地离线、SOTA 精度、GPU 加速、零 API 费用、多语言** | 阿里云 ASR(收费/联网)、FunASR(部署重)、Whisper.cpp(C++集成复杂) |
| **视频下载** | yt-dlp | 1800+ 平台、活跃维护、Python 直调、格式选择丰富 | youtube-dl(停更)、自研(维护成本极高) |
| **OCR** | PaddleOCR | 中文识别最强、PP-OCRv4 轻量、GPU 加速、Python 包直装 | Tesseract(中文弱)、EasyOCR(精度略低)、云 API(收费) |
| **向量库** | Qdrant | 纯 Rust 高性能、Payload 过滤、单节点无依赖、Python 客户端成熟 | Milvus(重、需 K8s)、pgvector(性能弱)、Chroma(持久化弱) |
| **关键词检索** | Rank-BM25 | 纯 Python、无服务、BM25 标准实现、内存占用可控 | Elasticsearch(重)、Tantivy(Rust FFI复杂) |
| **RAG 编排** | LlamaIndex | 检索管线丰富、模块化强、Query Engine 抽象好、社区活跃 | LangChain(检索弱、抽象泄漏)、Haystack(Java系) |
| **Agent 编排** | **自研 AgentLoop** | **核心竞争力：Planner/Executor/Critic 闭环、证据强校验、Checkpoint** | LangGraph(黑盒、难解释)、AutoGen(重)、CrewAI(重) |
| **本地 LLM** | Ollama + Qwen2.5-7B-INT4 | 6GB VRAM 可跑、OpenAI 兼容 API、模型管理简单、零费用 | vLLM(需更多显存)、LM Studio(无 API)、TGI(重) |
| **向量模型** | BGE-M3 / BAAI 系列 | 中英双语强、Sentence-Transformers 封装、多粒度检索 | E5(英文强)、OpenAI Embedding(收费/联网) |
| **前端** | Vue 3 + Vite | 与 Java 版共用设计、Composition API、生态成熟、SSR 可选 | React(团队不熟)、Svelte(生态小) |
| **监控** | Prometheus + Grafana + Loki | 云原生标准、Query 强、Dashboard 丰富、成本低 | Datadog(贵)、自研(维护重) |

---

## 5. 与 Java 版 (DOVideo-AI) 对比

| 维度 | Java 版 (DOVideo-AI) | Python 版 | 面试话术优势 |
|------|---------------------|-----------|-------------|
| **ASR** | 阿里云 ASR (商业 API) | **本地 Whisper large-v3** | "1h 视频 2 分钟转完，完全离线、零成本、数据不出域" |
| **GPU 利用** | 闲置 | **串行错峰：Whisper→OCR/Embedding→Ollama** | "单张 RTX 4060 8GB 跑全链路无 OOM，Java 版 GPU 闲置" |
| **Agent 核心** | LangChain4j Agent | **自研 AgentLoop** | "能讲清 Planner/Executor/Critic 每行代码，哪里用框架哪里自研" |
| **RAG 检索** | Qdrant + 关键词加权 | LlamaIndex 多通道 + RRF + CrossEncoder | "理解检索管线每层可替换组件，非黑盒调用" |
| **并发模型** | 虚拟线程 + 线程池 | **asyncio(IO) + multiprocessing/Celery(CPU)** | "能讲清 GIL 如何绕过：IO 用 async，CPU 密集丢进程池" |
| **熔断/路由** | 无 | **移植 Ragent 三态熔断+首包探测** | "生产级模型网关：状态机逻辑语言无关" |
| **意图识别** | 无 | **树形意图+置信度阈值+歧义引导** | "从 Ragent 移植，解决复合问题拆解路由" |
| **评测体系** | 无 | **rag-eval 离线评测框架** | "数据驱动迭代 RAG，非凭感觉调参" |
| **部署复杂度** | Maven + Docker + K8s | **Docker Compose 一键起** | "个人项目/面试 Demo 部署极简，评估成本低" |
| **前端复用** | Vue 3 独立 | **同一套前端** | "前后端解耦设计，后端可替换不改前端" |

---

## 6. GPU 资源调度策略（单卡 8GB 核心优势）

```
┌─────────────────────────────────────────────────────────────────┐
│                    GPU Memory Timeline (8GB)                    │
├─────────────────────────────────────────────────────────────────┤
│ Phase 1: Whisper ASR (60s segments, batch=1)                   │
│   ████████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  │
│   ~6.2GB VRAM  →  完成后显式 del model + torch.cuda.empty_cache()│
│                                                                 │
│ Phase 2: PaddleOCR + BGE-M3 Embedding (并行/串行均可)           │
│   ████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  │
│   ~2GB VRAM (OCR) + ~1.5GB (Embedding) → 瞬时完成即释放          │
│                                                                 │
│ Phase 3: Ollama Qwen2.5-7B INT4 (Agent 推理)                    │
│   ████████████████████████░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░  │
│   ~5.8GB VRAM  →  长期驻留，仅推理时占用                          │
└─────────────────────────────────────────────────────────────────┘

关键实现：
- `core/video/gpu_scheduler.py`: GPUResourceManager 单例
- `acquire(phase: str) -> contextmanager`: 申请独占、自动释放
- Whisper: `device_map="auto", low_cpu_mem_usage=True`
- Ollama: `num_gpu=1, num_ctx=4096` 固定上下文
- 进程隔离：Celery worker 设置 `worker_concurrency=1` + `prefetch_multiplier=1`
```

---

## 7. 关键非功能性设计决策

| 决策点 | 选择 | 理由 |
|--------|------|------|
| **数据库** | PostgreSQL (生产) / SQLite (开发) | JSONB 支持、全文检索、成熟稳定；开发期 SQLite 零配置 |
| **向量 ID** | UUID v5 (namespace=media_id) | 确定性、幂等、可追溯、无需中心化 ID 生成器 |
| **幂等键** | `content_hash + goal_hash` | 同视频同目标不重复分析，内容级去重 |
| **检索融合** | RRF (k=60) | 无需调参、理论有保证、工业界标准做法 |
| **证据引用 ID** | `chunk_{media_id}_{hash12}_{index}` | 稳定、可读、PostgreSQL/Qdrant/评测三端共享 |
| **流式协议** | SSE (Server-Sent Events) | 单向流、自动重连、防火墙友好、原生浏览器支持 |
| **配置管理** | Pydantic Settings + `.env` | 类型安全、环境变量覆盖、验证友好 |
| **日志格式** | JSON + trace_id + span_id | 结构化、可关联、Loki 直接入库 |
| **错误码** | 统一 `ErrorCode` Enum + HTTP 映射 | 前端统一处理、国际化友好、排障快 |

---

## 8. 扩展性预留点

| 扩展方向 | 预留接口/设计 |
|----------|---------------|
| **多模态模型** | `EmbeddingProvider`/`LLMProvider` Protocol，新增类实现即可 |
| **分布式部署** | Celery 支持多 Worker、Qdrant/MinIO/Redis 天然集群、PostgreSQL 主从 |
| **多租户 SaaS** | `user_ai_config` 表隔离用户模型配置、`API Key` AES-GCM 加密存储 |
| **插件化工具** | `MCPToolRegistry` 自动发现 `@mcp_tool` 装饰器函数 |
| **评测驱动** | `rag-eval` 离线评测集成 CI，每次检索管线变更跑基线对比 |
| **边缘推理** | `RoutingLLMService` 支持 `device=cpu/cuda/mps` 标签路由 |

---

## 9. 目录结构（规划）

```
videomind/
├── AGENTS.md                    # 本文档索引
├── docker-compose.yml           # 一键起所有基础设施
├── .env.example                 # 环境变量模板
├── pyproject.toml               # 依赖管理
├── alembic/                     # 数据库迁移
├── core/                        # 核心业务层（包）
│   ├── __init__.py
│   ├── config.py                # Pydantic Settings
│   ├── auth/                    # JWT、API Key 管理
│   ├── video/                   # 下载、ASR、OCR、Context 构建
│   ├── rag/                     # 检索管线
│   ├── agent/                   # AgentLoop、Planner、Executor、Critic
│   ├── llm/                     # 模型网关、熔断、路由
│   ├── task/                    # Celery 任务、状态机、SSE
│   ├── intent/                  # 意图树、查询改写、MCP
│   ├── storage/                 # SQLAlchemy Models、Repository
│   └── observability/           # 日志、指标、Trace
├── api/                         # FastAPI 路由层
│   ├── v1/
│   │   ├── videos.py
│   │   ├── analysis.py
│   │   ├── rag.py
│   │   ├── chat.py
│   │   └── admin.py
│   └── deps.py                  # 依赖注入
├── frontend/                    # Vue 3 + Vite
│   ├── src/
│   │   ├── views/
│   │   ├── components/
│   │   ├── api/
│   │   └── stores/
│   └── package.json
├── tests/                       # pytest + 测试工厂
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── scripts/                     # 运维脚本
│   ├── init_db.py
│   ├── download_models.py
│   └── benchmark.py
└── docs/                        # 设计文档（本目录下的所有 .md）
```

---

> **文档版本**：v0.1（规划阶段）  
> **维护者**：VideoMind 核心团队  
> **关联文档**：[DATA-MODEL.md](DATA-MODEL.md) · [VIDEO-PIPELINE.md](VIDEO-PIPELINE.md) · [RAG-RETRIEVAL.md](RAG-RETRIEVAL.md) · [AGENT-LOOP.md](AGENT-LOOP.md) · [MODEL-GATEWAY.md](MODEL-GATEWAY.md) · [TASK-ORCHESTRATION.md](TASK-ORCHESTRATION.md) · [INTENT-ROUTING.md](INTENT-ROUTING.md) · [FRONTEND.md](FRONTEND.md) · [SECURITY.md](SECURITY.md) · [OBSERVABILITY.md](OBSERVABILITY.md) · [DEPLOYMENT.md](DEPLOYMENT.md) · [INTERVIEW-GUIDE.md](INTERVIEW-GUIDE.md)