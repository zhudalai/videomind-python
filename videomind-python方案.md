# VideoMind — Python 方案 (Python + FastAPI)

## 面向求职方向：AI 应用开发 / 算法工程师

---

## 目录

1. [项目概述](#1-项目概述)
2. [为什么选 Python](#2-为什么选-python)
3. [四个参考项目的拆解与复用](#3-四个参考项目的拆解与复用)
4. [技术栈详解](#4-技术栈详解)
5. [架构设计](#5-架构设计)
6. [模块设计（含参考来源）](#6-模块设计含参考来源)
7. [目录结构](#7-目录结构)
8. [数据库设计](#8-数据库设计)
9. [API 设计](#9-api-设计)
10. [分阶段实施计划](#10-分阶段实施计划)
11. [面试问答准备](#11-面试问答准备)

---

## 1. 项目概述

**VideoMind** 是一个以视频/音频为核心知识源的企业级 Agentic RAG 平台。用户上传一个视频（或输入视频链接），系统自动完成：

1. **多模态知识抽取**：将视频中的语音转文字（ASR）、提取关键帧并 OCR、构建带时间轴的结构化内容
2. **混合 RAG 检索**：向量检索 + 关键词检索 + 时序检索，支持基于视频内容的精准提问
3. **Agent 智能分析**：Planner 拆解用户目标 → Executor 从证据中生成结论 → Critic 校验证据绑定
4. **可追溯的结果展示**：每个结论都带有时间戳、ASR 原文截图和关键帧，支持继续追问

**一句话定位**：能理解视频内容并给出带证据分析的 AI Agent。

---

## 2. 为什么选 Python

### 2.1 面试竞争力分析

| 维度 | Python + FastAPI | Java + Spring Boot 3 |
|------|-----------------|---------------------|
| **AI 应用岗位适配度** | ⭐⭐⭐⭐⭐ AI 应用/算法岗主流语言 | ⭐⭐ 后端语言，AI 岗不太需要 |
| **AI 生态丰富度** | ⭐⭐⭐⭐⭐ LangChain, LlamaIndex, Whisper, HuggingFace | ⭐⭐ LangChain4j 生态有限 |
| **项目开发速度** | ⭐⭐⭐⭐ FastAPI 自动 API 文档 + 快速迭代 | ⭐⭐ 配置较多，启动慢 |
| **后端岗位区分度** | ⭐⭐⭐ 也能展现后端能力但 Java 更主流 | ⭐⭐⭐⭐⭐ 后端岗的王牌 |

### 2.2 适合人群
- 投递 **AI 应用开发/算法工程师/大模型应用**岗位
- 已有一定 Python 基础，想深入 AI 应用层
- 希望快速搭建出能 demo 的 AI 产品

### 2.3 Python 生态的独特优势
- **Whisper**：本地部署 ASR，无需外部 API，精度极高
- **LangChain/LlamaIndex**：RAG 管线和 Agent 框架开箱即用
- **yt-dlp + FFmpeg**：视频处理生态成熟
- **FastAPI + SSE**：异步原生支持，流式输出天然友好
- **GPU 生态成熟**：CUDA/PyTorch 原生支持，Whisper、PaddleOCR、Sentence-Transformers 全系 GPU 加速

---

## 3. 四个参考项目的拆解与复用

### 3.1 DOVideo-AI — 核心参考：Agent 编排设计理念

> ⚠️ DOVideo-AI 用 Java 实现，Python 方案不能直接抄代码，但**架构设计完全可移植**

| 架构设计 | 移植方式 | 在 Python 中的实现 |
|---------|---------|------------------|
| **AgentLoop** (Planner→Executor→Critic) | **核心设计直接移植** | `core/agent/agent_loop.py` — 将 Java 的 Service 类改为 Python 的 AgentLoop 类 |
| **AgentState** (goal/plan/result/critique/round) | **数据结构直接移植** | `core/agent/schemas.py` — 用 Pydantic BaseModel 替代 Java Record |
| **VideoContext/VideoSegment** (ASR+OCR 时序结构) | **数据结构直接移植** | `core/video/schemas.py` — 用 Pydantic 定义相同字段 |
| **EvidenceVerification** | **逻辑直接移植** | `core/agent/evidence.py` — 相同的时间戳核验算法 |
| **Checkpoint 断点恢复** | **设计参考** | `core/storage/checkpoint.py` — MySQL/Redis 双存储 |
| **SSE 推送** | **参考实现方式** | FastAPI 原生 SSE (`StreamingResponse`) |
| **分片上传** | **参考设计** | 用 FastAPI 的 UploadFile + 分片合并逻辑 |

**结论**: DOVideo-AI 的核心价值在于 AgentLoop 的**架构设计理念**（Planner→Executor→Critic 的编排、Critic 靶向补充策略、轮数控制），这些在 Python 中完全可复用，只是把 Java 语法换成 Python。

### 3.2 Free-Video-Downloader — 核心参考：Python 生态的直接复用

| 模块 | 参考方式 | 在 VideoMind 中的位置 |
|------|---------|---------------------|
| `downloader.py` | **直接复用** | `core/downloader/ytdlp_downloader.py` — yt-dlp 封装逻辑 |
| `douyin.py` | **直接复用** | `core/downloader/douyin.py` — 抖音无水印解析 |
| `summarizer.py` | **参考并增强** | `core/agent/executor.py` — 从单一总结扩展为 Agent 分析 |
| `auth.py` | **参考** | `core/auth/jwt_auth.py` — JWT 认证 |
| `database.py` | **参考** | `core/database/` — SQLAlchemy 定义 |
| `Frontend 组件` | **参考 UI** | Vue 3 前端组件可直接复用 |

### 3.3 Ragent — 核心参考：企业级 RAG 的设计理念

> ⚠️ Ragent 是 Java 项目，Python 方案参考其**架构设计**，用 Python 生态工具（LangChain/LlamaIndex）实现

| 架构设计 | 移植方式 | 在 Python 中的实现 |
|---------|---------|------------------|
| **多通道并行检索** | **设计移植** | LlamaIndex 的 `QueryEngine` + 自定义 retriever |
| **后处理流水线 (PostProcessor)** | **设计移植** | LlamaIndex 的 `NodePostprocessor` 链 |
| **树形意图识别** | **设计移植** | 用 Pydantic + LLM 实现决策树 |
| **模型路由 + 熔断器** | **逻辑移植** | `core/llm/router.py` — 同算法不同语言 |
| **三态熔断器** | **逻辑直接移植** | `core/llm/circuit_breaker.py` — 状态机逻辑与语言无关 |
| **MCP 工具注册** | **设计移植** | `core/mcp/tool_registry.py` — 注册表模式 |
| **Ingestion Pipeline (可编排节点)** | **设计移植** | `core/ingestion/pipeline.py` — 节点编排 |
| **队列式并发限流 (Redis ZSET+Pub/Sub)** | **逻辑移植** | `core/ratelimit/queue_limiter.py` — 同算法 |
| **全链路 Trace** | **简化实现** | Python logging + OpenTelemetry |

### 3.4 VidLens — 核心参考：视频检索与治理

| 模块 | 参考方式 | 在 VideoMind 中的位置 |
|------|---------|---------------------|
| **分段 ASR 失败恢复** | **架构移植** | `core/video/asr_processor.py` — 分段+重试 |
| **BM25 + 向量混合检索** | **架构移植** | LlamaIndex 的 BM25Retriever + VectorRetriever |
| **RRF 融合排序** | **算法直接移植** | `core/rag/fusion.py` — RRF 公式不依赖语言 |
| **Redis Lua 令牌桶** | **逻辑移植** | `core/ratelimit/lua_token_bucket.py` |
| **用户级 AI 配置** | **架构移植** | `core/config/user_ai_config.py` |

---

## 4. 技术栈详解

### 4.1 后端核心

| 技术 | 版本 | 用途 | 选择理由 |
|------|------|------|---------|
| Python | 3.11+ | 主语言 | AI 生态最成熟 |
| FastAPI | 最新 | Web 框架 | 异步原生，自动 OpenAPI 文档 |
| Uvicorn | 最新 | ASGI 服务器 | 与 FastAPI 标配 |
| SQLAlchemy | 2.0+ | ORM | Python 最成熟的 ORM |
| Celery / Redis Queue | 最新 | 异步任务 | 替代 RocketMQ/Kafka |
| LangChain | 最新 | Agent/RAG 框架 | 开箱即用的 Agent + RAG 工具 |
| LlamaIndex | 最新 | 数据索引+检索 | 比 LangChain 更强的检索能力 |
| Pydantic | 2.x | 数据校验 | FastAPI 原生集成 |

### 4.2 AI 与视频处理

| 技术 | 用途 | 选择理由 |
|------|------|---------|
| **Whisper** (openai-whisper) | 本地 ASR 转写 | 开源免费，精度极高，离线可用 |
| **yt-dlp** | 多平台视频下载 | 1800+ 平台支持 |
| **FFmpeg** | 音视频处理 | 行业标准 |
| **Tesserocr / PaddleOCR** | OCR | PaddleOCR 中文更好 |
| **Sentence-Transformers** | Embedding | BGE-M3 / BAAI 系列 |
| **Qdrant Client** | 向量数据库 | DOVideo-AI 已验证 |
| **Rank-BM25** | 关键词检索 | 纯 Python，无需额外服务 |

### 4.3 前端（与 Java 方案共用）

| 技术 | 用途 |
|------|------|
| Vue 3 + Vite | 前端框架 |
| SSE (EventSource) | 实时推送 |
| Marked | Markdown 渲染 |
| Mermaid | 思维导图 |
| ECharts | 统计面板 |

### 4.4 中间件

| 组件 | 用途 | 启动方式 |
|------|------|---------|
| MySQL 8 | 业务数据 | Docker Compose |
| Redis 7 | 缓存/限流/Celery Broker | Docker Compose |
| MinIO | 视频/图片对象存储 | Docker Compose |
| Qdrant | 向量检索 | Docker Compose |

> **注意**: 相比 Java 方案少用了 RocketMQ，改用 Celery + Redis 作为异步任务队列。这是 Python 生态更自然的选型。

### 4.5 硬件配置与性能要求

#### 推荐配置

| 组件 | 最低要求 | 推荐配置（RTX 4060 8GB） | 效果 |
|------|---------|------------------------|------|
| **Whisper ASR** | CPU 模式（任何 x64） | GPU CUDA 加速 | 1h 视频 2min vs CPU 1-2h |
| **PaddleOCR** | CPU 模式即可 | GPU 可选加速 | 60-120 帧/视频 ≈ 几秒完成 |
| **Sentence-Transformers** | CPU 模式慢但可用 | GPU 加速 | 批量 Embedding 秒级 |
| **本地 LLM (Ollama)** | 不适用（需 6G+ VRAM） | Qwen2.5-7B (INT4) ≈ 6GB | 本地推理，零 API 费用 |
| **Docker 中间件合计** | 4GB RAM | 8GB RAM 剩余 | MySQL+Redis+MinIO+Qdrant |

#### RTX 4060 8GB 的实际运行策略

显存 8GB 一次只能装一个模型，但 Pipeline 是串行的，正好错峰使用：

```
Phase 1: Whisper large-v3 ASR       → 占用 ~6GB VRAM       → 完成后释放
Phase 2: PaddleOCR                  → 占用 <1GB VRAM       → 瞬间完成
Phase 3: Sentence-Transformers      → 占用 ~2GB VRAM       → 完成后释放  
Phase 4: Ollama Qwen2.5-7B 推理     → 占用 ~6GB VRAM       → 分析输出
```

**无需等待**：同一时间只有一个模型需要显存，不会 OOM。

#### 如果无独显也能开发

如果开发机没有 NVIDIA GPU，或者后期在没有 GPU 的服务器上部署：

| 场景 | 影响 | 替代方案 |
|------|------|---------|
| 功能开发调试 | **无影响** | 只写代码、调 API 响应，不需要 GPU |
| ASR 测试 | 慢但能用 | 用 Whisper `tiny` 模型纯 CPU 跑，5 分钟视频 ≈ 5-10 分钟转完 |
| Embedding | 稍慢 | Sentence-Transformers CPU 模式，千条分块几十秒 |
| **生产部署** | 建议 GPU | 实在没有 GPU 也可用 CPU 跑 `small` 模型，1h 视频 ≈ 2h ASR |

> **结论**：开发阶段有没有 GPU 都可以写代码。有 GPU（如 RTX 4060）时，本地 ASR 从小时级变为分钟级，体验大幅提升，且面试时能讲"全本地化"的故事。

---

## 5. 架构设计

### 5.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                      Vue 3 管理控制台                            │
│   知识库管理  │  任务监控  │  Agent工作台  │  Trace查看  │  配置  │
└─────────────┬───────────────────────────────────────────────────┘
              │ REST API (JSON)              │ SSE (EventStream)
              ▼                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     FastAPI API 网关                             │
│                                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────────┐  │
│  │JWT 认证  │  │限流中间件 │  │请求路由  │  │SSE 流式输出    │  │
│  │(PyJWT)   │  │(令牌桶)  │  │(FastAPI) │  │(StreamingResp.)│  │
│  └──────────┘  └──────────┘  └──────────┘  └────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
              │
    ┌─────────┼─────────┬──────────────────┐
    ▼         ▼         ▼                  ▼
┌────────┐ ┌────────┐ ┌────────┐  ┌────────────────┐
│视频/媒体│ │知识库  │ │Agent   │  │ 模型网关       │
│管理模块  │ │管理模块 │ │分析模块│  │                │
│         │ │        │ │        │  │ ┌──────────┐   │
│(FastAPI)│ │(FastAPI)│ │(LangChain)│  │DeepSeek  │   │
│         │ │        │ │+ LlamaIndex│ │OpenAI    │   │
└───┬────┘ └───┬────┘ └───┬────┘  │SiliconFlow│  │
    │          │          │       │Whisper    │   │
    ▼          ▼          ▼       └──────────┘   │
┌─────────────────────────────┐  └────────────────┘
│   Celery 异步任务队列         │
│  (视频处理/知识入库/Agent任务) │
└─────────────────────────────┘
    │          │          │
    ▼          ▼          ▼
┌────────┐ ┌────────┐ ┌────────┐
│ MySQL  │ │ Redis  │ │ MinIO  │
│(业务表) │ │(缓存)  │ │(对象存储)│
└────────┘ └────────┘ └────────┘
              │
              ▼
         ┌────────┐
         │ Qdrant │
         │(向量库) │
         └────────┘
```

### 5.2 Agent + RAG 核心流程

```
用户上传视频 + 输入分析目标: "总结这个视频的技术架构"
        │
        ▼
┌─────────────────────────────────────┐
│ Phase 1: 视频处理 (Celery 异步)       │
│                                     │
│ 1. yt-dlp 下载 (或使用已上传文件)      │
│ 2. FFmpeg 音频提取 + 60s 分片         │  [参考 DOVideo-AI]
│ 3. Whisper 并行 ASR 转写              │  [Python 独特优势]
│ 4. FFmpeg 场景检测抽帧 + PaddleOCR    │  [参考 DOVideo-AI]
│ 5. 合并为 VideoSegment 时序列表        │
│ 6. 分段摘要 + Embedding → Qdrant      │
└─────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────┐
│ Phase 2: 意图识别 + 问题重写          │
│                                     │
│ - 树形意图分类 (技术分析类/问答类/...) │  [参考 Ragent]
│ - 多轮上下文补全                      │
│ - 复合问题拆分                        │
└─────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────┐
│ Phase 3: 多通道混合检索               │
│                                     │
│ ┌──────────┐ ┌──────┐ ┌──────────┐ │
│ │ 向量通道  │ │BM25  │ │ 时序通道  │ │  [DOVideo-AI + VidLens + Ragent]
│ │ (Qdrant) │ │关键词│ │(时间范围) │ │
│ └──────────┘ └──────┘ └──────────┘ │
│        │        │         │         │
│        └────────┼─────────┘         │
│                 ▼                   │
│        ┌────────────────┐           │
│        │ RRF 融合排序    │           │  [参考 VidLens]
│        │ + CrossEncoder │           │
│        │ 重排序         │           │
│        └────────────────┘           │
└─────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────┐
│ Phase 4: AgentLoop (最多 2 轮)       │
│                                     │
│ 第1轮:                               │
│   Planner: "[识别前端栈,识别后端栈,   │  [参考 DOVideo-AI]
│             识别数据层,分析优缺点]"   │
│   Executor: 从 RAG 证据中逐任务执行   │
│   Critic: 检查目标覆盖+证据有效性     │  [使用 LangChain 的 ReflectionAgent]
│                                     │
│   └─ 不通过 → 定向补充检索 → 第2轮    │
│                                     │
│ 第2轮 (如有): 同上，输出最终结果       │
└─────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────┐
│ Phase 5: SSE 实时推送结果             │
│   - 结构化结论 (Markdown)            │
│   - 时间戳证据                       │
│   - 关键帧截图                       │
│   - 执行轨迹                         │
│   - 支持继续追问                     │
└─────────────────────────────────────┘
```

---

## 6. 模块设计（含参考来源）

### 6.1 core/ 核心目录

Python 版的核心模块，每个文件对应一个明确的职责：

```
videomind/
├── core/
│   ├── agent/              # Agent 引擎
│   │   ├── agent_loop.py        →  AgentLoop (参考 DOVideo-AI)
│   │   ├── schemas.py           →  AgentState/AnalysisResult (参考 DOVideo-AI)
│   │   ├── planner.py           →  Planner 任务拆解 (参考 DOVideo-AI)
│   │   ├── executor.py          →  Executor 结构化结论 (参考 DOVideo-AI + free-video-downloader)
│   │   ├── critic.py            →  Critic 校验 (参考 DOVideo-AI)
│   │   ├── evidence.py          →  EvidenceVerification (参考 DOVideo-AI)
│   │   ├── intent_classifier.py →  意图识别树 (参考 Ragent)
│   │   └── query_rewriter.py    →  问题重写/拆分 (参考 Ragent)
│   │
│   ├── rag/                # RAG 引擎
│   │   ├── hybrid_retriever.py  →  多通道混合检索 (DOVideo-AI + Ragent + VidLens)
│   │   ├── channels/
│   │   │   ├── vector_channel.py    →  Qdrant 向量检索
│   │   │   ├── keyword_channel.py   →  BM25 关键词检索 (参考 VidLens)
│   │   │   └── temporal_channel.py  →  时序检索 (新增)
│   │   ├── fusion.py               →  RRF 融合 + CrossEncoder 重排序 (参考 VidLens)
│   │   └── post_processor.py       →  去重/过滤/增强 (参考 Ragent)
│   │
│   ├── video/              # 视频处理
│   │   ├── downloader.py          →  yt-dlp 封装 (参考 free-video-downloader)
│   │   ├── douyin.py              →  抖音解析 (参考 free-video-downloader)
│   │   ├── audio_processor.py     →  FFmpeg 音频提取+分段 (参考 DOVideo-AI)
│   │   ├── asr_processor.py       →  Whisper ASR (Python 独特优势)
│   │   ├── ocr_processor.py       →  PaddleOCR (参考 DOVideo-AI 的 Tesseract)
│   │   ├── keyframe_extractor.py  →  场景检测抽帧 (参考 DOVideo-AI)
│   │   ├── dedup.py               →  感知哈希去重 (参考 DOVideo-AI)
│   │   └── context_builder.py     →  构建 VideoContext (参考 DOVideo-AI)
│   │
│   ├── llm/                # 模型网关
│   │   ├── router.py              →  多供应商路由 (参考 Ragent)
│   │   ├── circuit_breaker.py     →  三态熔断器 (参考 Ragent)
│   │   ├── providers/
│   │   │   ├── deepseek.py        →  DeepSeek 适配
│   │   │   ├── openai_compat.py   →  OpenAI 兼容 API
│   │   │   └── siliconflow.py     →  SiliconFlow 适配
│   │   └── retry_policy.py        →  指数退避重试 (参考 DOVideo-AI + VidLens)
│   │
│   ├── ingestion/          # 知识入库
│   │   ├── pipeline.py            →  IngestionPipeline 编排 (参考 Ragent)
│   │   └── nodes/
│   │       ├── fetcher_node.py    →  获取源 (参考 free-video-downloader)
│   │       ├── parser_node.py     →  解析 (视频→ASR, 文档→文本)
│   │       ├── chunker_node.py    →  分块 (参考 Ragent)
│   │       ├── embed_node.py      →  向量化
│   │       └── indexer_node.py    →  写入 Qdrant
│   │
│   ├── mcp/                # MCP 工具
│   │   ├── tool_registry.py       →  工具注册表 (参考 Ragent)
│   │   └── tools/                 →  具体工具实现
│   │
│   ├── storage/            # 存储
│   │   ├── checkpoint.py          →  Checkpoint 管理 (参考 DOVideo-AI)
│   │   └── models.py              →  SQLAlchemy 模型
│   │
│   ├── ratelimit/          # 限流
│   │   ├── token_bucket.py        →  令牌桶 (参考 DOVideo-AI + VidLens)
│   │   └── queue_limiter.py       →  队列限流 (参考 Ragent)
│   │
│   └── config/             # 配置
│       ├── settings.py            →  全局配置
│       └── user_ai_config.py      →  用户级 AI 配置 (参考 VidLens)
│
├── api/                   # FastAPI 路由层
│   ├── main.py                  →  FastAPI 应用入口
│   ├── auth.py                  →  登录/注册路由
│   ├── media.py                 →  视频管理路由
│   ├── analysis.py              →  Agent 分析路由
│   ├── knowledge_base.py        →  知识库管理路由
│   └── admin.py                 →  管理后台路由
│
├── worker/                # Celery 异步任务
│   ├── celery_app.py           →  Celery 配置
│   └── tasks/
│       ├── video_analysis.py    →  视频分析任务
│       ├── rag_indexing.py      →  RAG 索引建任务
│       └── agent_task.py        →  Agent 分析任务
│
├── web/                   # Vue 3 前端 (与 Java 方案共用)
│   └── src/
│       ├── views/
│       │   ├── VideoLibrary.vue
│       │   ├── AgentWorkbench.vue
│       │   └── KnowledgeBase.vue
│       └── components/
│           ├── VideoUploader.vue
│           ├── StreamOutput.vue
│           └── MindMapViewer.vue
│
├── docker-compose.yml
├── pyproject.toml           # Python 依赖
└── .env.example
```

### 6.2 关键模块的代码骨架示例

#### AgentLoop (`core/agent/agent_loop.py`)

```python
"""Agent 编排器：Planner → Executor → Critic，最多 2 轮"""

from dataclasses import dataclass
from typing import Optional

@dataclass
class AgentPlan:
    understood_goal: str
    tasks: list[str]

@dataclass  
class AgentState:
    goal: str
    plan: Optional[AgentPlan] = None
    result: Optional[AnalysisResult] = None
    critique: Optional[CriticResult] = None
    round: int = 0

class AgentLoop:
    """受控 AgentLoop：DOVideo-AI 的 Java 代码 → Python 移植"""
    
    MAX_ROUNDS = 2
    
    def __init__(self, 
                 llm_client,              # LLM 客户端 [参考 DOVideo-AI DeepSeekUtils]
                 retriever,               # 混合检索 [参考 DOVideo-AI + Ragent + VidLens]
                 checkpoint_service,       # Checkpoint [参考 DOVideo-AI]
                 evidence_verifier):       # 证据校验 [参考 DOVideo-AI]
        self.llm = llm_client
        self.retriever = retriever
        self.checkpointer = checkpoint_service
        self.verifier = evidence_verifier
    
    def run(self, context: VideoContext) -> AgentState:
        # 1. 尝试恢复 Checkpoint [参考 DOVideo-AI]
        state = self.checkpointer.load(context.media_id, context.user_goal)
        if state and self._is_complete(state):
            return state
        
        # 2. Planner 拆解任务 [参考 DOVideo-AI DeepSeekUtils.plan()]
        plan = state.plan if state else self._plan(context)
        
        # 3. 混合检索 [参考 DOVideo-AI + VidLens + Ragent]
        evidence = self.retriever.search(context, plan)
        
        # 4. AgentLoop — 最多 2 轮 [参考 DOVideo-AI AgentLoopService]
        for round_num in range(state.round + 1, self.MAX_ROUNDS + 1):
            state = self._execute_round(context, evidence, plan, round_num)
            if state.critique.passed:
                break
            if round_num < self.MAX_ROUNDS:
                # Critic 未通过 → 定向补充检索 [参考 DOVideo-AI]
                evidence = self.retriever.refine(context, state.critique)
        
        # 5. 保存结果 + Checkpoint [参考 DOVideo-AI]
        self.checkpointer.save(context.media_id, state)
        return state
    
    def _plan(self, context: VideoContext) -> AgentPlan:
        """Planner：将用户目标拆解为可执行任务 [参考 DOVideo-AI]"""
        prompt = f"""分析目标: {context.user_goal}
        视频时长: {context.duration_seconds}秒
        分段数: {len(context.segments)}
        
        请拆解为 3-5 个具体分析任务，每个任务应：
        1. 可在视频特定时间段完成
        2. 有明确的检索范围
        3. 能产出结构化结论
        
        返回 JSON: {{"tasks": [{{"name": "...", "scope": "..."}}]}}"""
        
        result = self.llm.chat_json(prompt)  # [参考 DOVideo-AI DeepSeekUtils]
        return AgentPlan(
            understood_goal=context.user_goal,
            tasks=[t["name"] for t in result["tasks"]]
        )
    
    def _execute_round(self, context, evidence, plan, round_num):
        """执行一轮分析 [参考 DOVideo-AI AgentLoopService.executeRound()]"""
        # Executor：从证据中生成结构化结论
        result = self._execute(context, evidence, plan)
        
        # Critic：校验目标覆盖 + 证据绑定 [参考 DOVideo-AI CriticService]
        critique = self._critique(context, plan, result)
        
        return AgentState(
            goal=context.user_goal,
            plan=plan,
            result=result,
            critique=critique,
            round=round_num
        )
```

#### 混合检索 (`core/rag/hybrid_retriever.py`)

```python
"""多通道混合检索：向量 + BM25 + 时序 [参考 DOVideo-AI + VidLens + Ragent]"""

from typing import Protocol

class SearchChannel(Protocol):
    """检索通道接口 [参考 Ragent SearchChannel 策略模式]"""
    def search(self, query: str, top_k: int) -> list[ScoredChunk]: ...

class VectorChannel(SearchChannel):
    """Qdrant 向量检索通道"""
    def __init__(self, qdrant_client, embedder):
        self.client = qdrant_client  
        self.embedder = embedder

class KeywordChannel(SearchChannel):
    """BM25 关键词检索通道 [参考 VidLens BM25]"""
    def __init__(self, index_path: str):
        from rank_bm25 import BM25Okapi
        self.bm25 = BM25Okapi(...)  # [参考 VidLens BM25 实现]

class TemporalChannel(SearchChannel):
    """时序检索通道 — 按时间戳范围 (视频独有)"""
    def __init__(self, segments: list[VideoSegment]):
        self.segments = segments  # [参考 DOVideo-AI VideoContext]

class HybridRetriever:
    """混合检索器：多通道并行 → RRF 融合 → CrossEncoder 重排序"""
    
    def __init__(self, channels: list[SearchChannel], fusion_alpha: float = 60):
        self.channels = channels
        self.fusion_alpha = fusion_alpha  # [参考 VidLens RRF k 参数]
    
    def search(self, query: str, top_k: int = 10) -> list[Evidence]:
        # 1. 多通道并行检索 [参考 Ragent 多通道架构]
        all_results = []
        for channel in self.channels:
            results = channel.search(query, top_k * 2)
            all_results.append(results)
        
        # 2. RRF 融合排序 [参考 VidLens RRF]
        scores = {}
        for channel_results in all_results:
            for rank, chunk in enumerate(channel_results):
                scores[chunk.id] = scores.get(chunk.id, 0) + 1 / (self.fusion_alpha + rank)
        
        # 3. 按 RRF 分数排序取 TopK
        reranked = sorted(scores.items(), key=lambda x: -x[1])[:top_k]
        
        # 4. CrossEncoder 重排序 [参考 Ragent PostProcessor]
        # ... (可选)
        
        return [self._to_evidence(chunk_id) for chunk_id, _ in reranked]
```

#### 模型网关 (`core/llm/circuit_breaker.py`)

```python
"""三态熔断器 [参考 Ragent 熔断器实现]"""

from enum import Enum
import time

class CircuitState(Enum):
    CLOSED = "closed"       # 正常
    OPEN = "open"           # 熔断
    HALF_OPEN = "half_open" # 半开(探测中)

class CircuitBreaker:
    """三态熔断器 — Ragent Java 代码 → Python 移植"""
    
    def __init__(self, failure_threshold=5, recovery_timeout=30):
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.failure_threshold = failure_threshold  # 失败次数阈值
        self.recovery_timeout = recovery_timeout     # 冷却时间(秒)
        self.last_failure_time = 0
    
    def call(self, func, *args, **kwargs):
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN  # 冷却到→半开
            else:
                raise CircuitBreakerOpenError("模型已被熔断")
        
        try:
            result = func(*args, **kwargs)
            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.CLOSED    # 探测成功→恢复
                self.failure_count = 0
            return result
        except Exception:
            self.failure_count += 1
            self.last_failure_time = time.time()
            if self.failure_count >= self.failure_threshold:
                self.state = CircuitState.OPEN      # 失败超标→熔断
            raise
```

---

## 7. 对比总结：Java vs Python

| 对比维度 | Java + Spring Boot 3 | Python + FastAPI |
|---------|---------------------|-----------------|
| **核心亮点** | AgentLoop 自研编排 + 企业级工程化 | Whisper 本地 ASR + LangChain/LlamaIndex 生态 |
| **可直接复用的代码** | DOVideo-AI (~50 Java 文件) + Ragent (~400 Java 文件) | free-video-downloader (~15 Python 文件) |
| **间接参考的代码** | VidLens Go 代码 | DOVideo-AI + Ragent + VidLens (架构移植) |
| **适合岗位** | Java 后端 / 全栈 | AI 应用 / 算法工程师 |
| **面试区分度** | ⭐⭐⭐⭐⭐ Java + AI 是稀缺组合 | ⭐⭐⭐ Python + AI 较常见 |
| **关键优势** | 展示工程化能力、设计模式、并发 | 展示 AI 生态熟悉度、快速迭代能力 |
| **关键劣势** | AI 生态需外部 API | 工程化深度不如 Java |
| **ASR 方案** | 调用外部 API (SiliconFlow) | 🏆 本地 Whisper，免费且可控 |
| **Agent 方案** | 🏆 自研 (更可控，面试能深入讲) | LangChain Agent (更方便，但面试泛泛而谈) |
| **RAG 方案** | 自行实现多路检索 (更可控) | LlamaIndex (方便但黑盒) |
| **异步任务** | RocketMQ (强一致性) | Celery + Redis (轻量) |
| **启动复杂度** | 较高 (JDK + MVN + 5个中间件 Docker) | 🏆 较低 (Python + pip + pip install) |
| **项目建成后代码量** | 约 8000-10000 行 Java | 约 5000-7000 行 Python |
| **GPU 利用率** | ❌ 基本用不上（AI 全走 API） | ✅ CUDA 原生支持，所有 AI 模块 GPU 加速 |
| **离线能力** | ❌ 必须联网（三大 AI 功能全 API） | ✅ 可选完全离线（本地 Whisper + Ollama） |
| **AI 运行成本** | 💰 按 API 调用量计费 | 🆓 本地跑基本零成本 |

### 如果你有 RTX 4060 8GB

这一配置让 Python 方案的优势进一步放大：

```
Java 方案: 4060 基本闲置，该花的 API 钱一分不少
Python 方案: Whisper 2min → 1h视频; Ollama 本地跑; 全部零API费
```

**面试故事升级**：
> "我用 Python 做了一套全本地化的视频 AI 分析平台。RTX 4060 上 Whisper large-v3 做 ASR，1 小时视频 2 分钟转完；PaddleOCR 抽帧 OCR 几秒完事；Qwen2.5-7B 本地 Agent 推理。整个链路不依赖任何外部 API，完全离线可用。"

### 选择建议

**选 Java 如果：**
- 你投递的岗位是 **Java 后端开发 / 全栈开发**
- 你的简历上已经有 Spring Boot CRUD 项目，需要 AI 项目提升区分度
- 你希望深入展示工程化能力（设计模式、并发、分层架构）
- 你不介意大部分 AI 能力依赖外部 API

**选 Python 如果：**
- 你投递的岗位是 **AI 应用开发 / 算法工程师**
- 你希望完全本地运行（Whisper ASR + PaddleOCR + 本地 LLM）
- 你想快速出一个能跑起来的 Demo
- 你对 LangChain/LlamaIndex 等 AI 框架生态更熟悉
- **你有 NVIDIA 显卡**（非必需但极大提升体验）

---

## 8. 数据库设计（15 张核心表）

Python 方案使用 SQLAlchemy，表设计与 Java 方案一致，略去了 RocketMQ 相关的表（改为 Celery 任务）：

### 用户与认证 (3 张)
| 表名 | 说明 |
|------|------|
| `user` | 用户信息 (邮箱/密码/角色) |
| `user_ai_config` | 用户级 AI 服务配置 |
| `membership` | 会员权益 |

### 媒体与知识库 (4 张)
| 表名 | 说明 |
|------|------|
| `media_file` | 视频/音频文件记录 |
| `video_segment` | 视频分段 (ASR/OCR/关键帧) |
| `knowledge_base` | 知识库 |
| `document` | 文档记录 |

### 任务 (3 张)
| 表名 | 说明 |
|------|------|
| `analysis_task` | 视频分析任务 |
| `celery_task` | Celery 任务状态记录 |
| `ingestion_task` | 入库任务实例 |

### RAG 与 Agent (5 张)
| 表名 | 说明 |
|------|------|
| `chunk` | 知识分块 (向量化的源) |
| `agent_checkpoint` | Agent 断点 |
| `agent_result` | 分析结果 |
| `rag_trace` | 检索链路追踪 |
| `session` | 多轮对话会话 |

---

## 9. API 设计

### 9.1 视频管理 API

| 方法 | 路径 | 说明 | 参考 |
|------|------|------|------|
| POST | `/api/media/upload` | 分片上传 | DOVideo-AI |
| POST | `/api/media/merge` | 合并分片 | DOVideo-AI |
| GET | `/api/media/list` | 媒体列表 | DOVideo-AI |
| POST | `/api/media/from-url` | 从链接导入视频 | Free-Video-Downloader |

### 9.2 分析 API

| 方法 | 路径 | 说明 | 参考 |
|------|------|------|------|
| POST | `/api/analysis/start` | 启动 Agent 分析 | DOVideo-AI |
| GET | `/api/analysis/stream/{taskId}` | SSE 实时接收进度 | DOVideo-AI |
| GET | `/api/analysis/result/{taskId}` | 获取最终结果 | DOVideo-AI |

### 9.3 知识库 API

| 方法 | 路径 | 说明 | 参考 |
|------|------|------|------|
| POST | `/api/kb` | 创建知识库 | Ragent |
| GET | `/api/kb` | 知识库列表 | Ragent |
| POST | `/api/kb/{kbId}/import` | 导入文档/视频 | Ragent |
| POST | `/api/kb/{kbId}/chat` | RAG 问答 | VidLens + Ragent |

---

## 10. 分阶段实施计划

### Phase 1：地基（1.5 周）

**目标**：跑通最小闭环

- [ ] FastAPI 脚手架 + Vue 3 + Docker Compose
- [ ] 用户注册登录 (JWT)
- [ ] 视频上传 + 列表展示
- [ ] 参考: `free-video-downloader/backend/main.py`, `auth.py`, `database.py`

### Phase 2：视频处理 Pipeline（1.5 周）

**目标**：上传视频 → 自动 Whisper ASR + 抽帧

- [ ] yt-dlp 多平台下载
- [ ] FFmpeg 音频提取 + 60s 分段
- [ ] Whisper ASR 转写 + 分段持久化（Python 独特优势）
- [ ] FFmpeg 场景检测抽帧 + PaddleOCR
- [ ] Celery 异步任务编排
- [ ] 参考: `DOVideo-AI` 的 VideoContext 设计

### Phase 3：RAG 检索（1.5 周）

**目标**：基于视频内容进行带引用的问答

- [ ] LlamaIndex 索引构建
- [ ] Qdrant 向量检索 + BM25 关键词检索
- [ ] 多通道混合检索 + RRF 融合
- [ ] 问题重写 + 上下文补全
- [ ] 参考: `VidLens` 的 RRF 融合, `Ragent` 的多通道设计

### Phase 4：Agent 分析能力（2 周）

**目标**：Agent 自动分析视频

- [ ] LangChain Agent 配置
- [ ] Planner→Executor→Critic 三阶段编排（移植自 DOVideo-AI）
- [ ] Critic 证据校验
- [ ] SSE 流式推送
- [ ] 参考: `DOVideo-AI/AgentLoopService.java` 架构移植

### Phase 5：企业级特性（1.5 周）

**目标**：模型治理 + 管理后台

- [ ] 模型网关（多供应商路由 + 熔断器）
- [ ] 意图识别 + MCP 工具
- [ ] 管理控制台前端

---

## 11. 面试问答准备

### 11.1 基础问题

**Q: 为什么用 Python + FastAPI 而不是 Java？**
A: Python 在 AI 生态上有天然优势——Whisper 可以本地部署 ASR 无需外部 API，LangChain 和 LlamaIndex 让 RAG 和 Agent 开箱即用。FastAPI 相比 Flask 性能更好，原生 ASGI 支持 SSE 流式输出，自动生成 OpenAPI 文档。对于 AI 应用开发来说，Python 能让我把精力放在 Agent 和 RAG 的核心逻辑上，而不是基础设施。

**Q: 为什么不直接用 LangChain Agent 而自己写 AgentLoop？**
A: LangChain Agent 的 ReAct 模式适合简单任务，但长视频分析场景下有两个问题：(1) Token 浪费——Agent 边推理边检索，很多轮实际上在做重复工作；(2) 不可控——没有目标达成标准，Agent 可能跑偏。我们参考 DOVideo-AI 的 Planner→Executor→Critic 架构，固定 2 轮上限 + Checkpoint 机制，在质量和成本之间做了工程权衡。

### 11.2 深度问题

**Q: Whisper ASR 效果怎么样？如何处理长音频？需要什么硬件？**
A: 英语近乎完美，中文需要 `large-v3` 模型才能达到实用级精度。长音频用分段策略：FFmpeg 按 60s 切片 → 每个切片独立 ASR → 合并结果。分段 ASR 比整段快约 3 倍（并行），且单切片失败只重试它一个。

**硬件影响巨大**：
- **RTX 4060 8GB**（推荐）：1 小时视频约 **2 分钟**转完（large-v3）
- **纯 CPU**：1 小时视频约 1-2 小时，可用 `tiny`/`base` 模型降速到 30-45 分钟
- 8GB 显存正好装 `large-v3`（~6GB），Pipeline 串行工作不碰撞

**Q: 项目的 GPU 利用策略？8GB 显存够用吗？**
A: 够用，关键在于错峰调度。视频处理 Pipeline 是串行的，同一时间只有一个模型需要 GPU：
1. Whisper ASR: 6GB → 完事释放
2. PaddleOCR + Embedding: 2GB → 瞬间完事释放
3. Ollama 本地推理: 6GB
8GB 单卡正好装一个模型。如果显存更小（如 6GB），可以把 Whisper 降级到 `medium`（~3GB）或用 CPU 跑 OCR。**开发阶段**有没有 GPU 都行，GPU 只影响速度不影响功能开发。

**Q: Python 的 GIL 问题怎么处理？**
A: 两层次解：(1) IO 密集型（HTTP 调用 LLM、读写磁盘、网络请求）用 asyncio + FastAPI 的异步路由，GIL 不影响；(2) CPU 密集型（ASR 转写、OCR、Embedding）用 multiprocessing 子进程池或 Celery 工作进程。具体来说，Whisher ASR 的 60s 分片用 ProcessPoolExecutor 并行，每个分片在独立进程中执行，绕开 GIL。这在面试中可以展开讲 Python 并发模型。

**Q: 这个项目的模型容错怎么做？**
A: 参考 Ragent 的三态熔断器。每个模型供应商有独立的熔断器状态：连续 5 次失败 → 熔断（快速失败）→ 30s 冷却 → 半开（放行一次探针请求）→ 成功则恢复/失败继续熔断。配合优先级降级链：用户配置的 primary 模型 → backup 模型 → 系统保底模型。一个模型挂了自动切换，业务层无感。

### 11.3 和 Java 方案的对比（面试时可能被问到）

**Q: 和 Java 版的 Agent 实现有什么区别？**
A: Java 版的所有 Agent 编排都是自研的（参考 DOVideo-AI 的 AgentLoopService），代码更可控，面试能讲到每一行。Python 版的核心循环是自研的，但内部调用了 LangChain 的工具集成和 LLM 调用封装。**Java 版面试可以讲「我是怎么写 AgentLoop 的」，Python 版面试要讲「为什么这里用 LangChain 而那里要自己写」**，这是一个权衡。

---

> **关键原则**：Python 方案的核心竞争力在于**AI 生态的深度利用**（Whisper ASR、LangChain Agent、LlamaIndex RAG）——要能证明你熟悉这些工具而不只是调用 API。面试时重点展示对 AI 工具链的理解和最佳实践，而非工程框架的深度。
