# VideoMind — AI 应用工程师求职作品集

> **一句话定位**：面向 AI 应用/算法岗的企业级 **Agentic RAG 视频理解平台**——用户上传视频/链接 → ASR+OCR 双路径解析 → 混合检索 → 自研 AgentLoop 多轮分析 → 带时间戳证据的结构化结论。ASR/OCR/Embedding 均为 `local`/`api` 双路径，可全本地离线跑，也可 API 主力+本地兜底；单卡 8GB 显存串行错峰跑完。

---

## 📁 文档索引（按阅读顺序）

| 文档 | 核心内容 | 关联模块 |
|------|----------|----------|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | 总架构：分层、数据流、技术栈选型理由、与 Java 版对比、GPU 调度策略 | 全局 |
| [DATA-MODEL.md](docs/DATA-MODEL.md) | 24 张 SQLAlchemy 表定义、ER 图、索引策略、迁移方案 | `infrastructure/storage/` |
| [VIDEO-PIPELINE.md](docs/VIDEO-PIPELINE.md) | 视频处理管线：下载→转码→分段 ASR→关键帧 OCR→VideoSegment 合并 | `core/video_pipeline/` |
| [RAG-RETRIEVAL.md](docs/RAG-RETRIEVAL.md) | 混合检索：向量(Qdrant)+BM25→RRF→CrossEncoder 重排→ContextExpander→引用溯源 | `core/rag/` |
| [AGENT-LOOP.md](docs/AGENT-LOOP.md) | 自研 AgentLoop：Planner→Executor→Critic 2 轮、证据强校验、Checkpoint 断点恢复 | `core/agent_loop/` |
| [MODEL-GATEWAY.md](docs/MODEL-GATEWAY.md) | 模型网关：三态熔断、优先级路由、首包探测、Token 计费、多供应商抽象 | `core/model_gateway/` |
| [TASK-ORCHESTRATION.md](docs/TASK-ORCHESTRATION.md) | 任务编排：Celery+Redis、状态机、幂等、重试预算、SSE 阶段推送 | `application/task_orchestration/` |
| [INTENT-ROUTING.md](docs/INTENT-ROUTING.md) | 意图识别树、查询改写拆分、多通道检索编排 | `core/intent/` |
| [FRONTEND.md](docs/FRONTEND.md) | 前端工作台：路由、TanStack Query 状态层、axios API 层、SSE 管线进度、9 个页面、测试与构建（**已按实际 React 实现重写**） | `frontend/` |
| [SECURITY.md](docs/SECURITY.md) | JWT 鉴权、API Key AES-GCM 加密、速率限流、审计日志、CORS/CSRF | `interface/` + `infrastructure/` |
| [OBSERVABILITY.md](docs/OBSERVABILITY.md) | 结构化日志、Prometheus 指标、Grafana 看板、全链路 Trace、离线评测框架 | `observability/` |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Docker Compose 一键起、环境变量、GPU 直通、MinIO/Qdrant/Ollama 本地化部署 | 运维 |
| [DECISIONS.md](docs/DECISIONS.md) | **设计↔实现的偏离记录**：哪些设计已落地、哪些主动取舍未实现及原因 | 全局（读代码前必看） |
| [INTERVIEW-GUIDE.md](docs/INTERVIEW-GUIDE.md) | 面试核心话术、STAR 项目拆解、与 Java 版对比表、常见追问与回答策略 | 求职准备 |

> **阅读顺序建议**：先读 DECISIONS.md 建立「设计 ≠ 实现」的预期，再读其余文档。`docs/` 下的设计文档刻画的是**理想形态**，部分章节（如 MCP 工具调用、GraphSearch）明确未实现，DECISIONS.md 有逐条对照。

---

## 🎯 技术栈速览

| 层级 | 选型 | 关键理由 |
|------|------|----------|
| **Web 框架** | FastAPI + Uvicorn | 异步原生、自动 OpenAPI、SSE 原生 |
| **异步任务** | Celery + Redis | Python 生态自然替代 RocketMQ/Kafka；双队列（gpu `-c 1` / cpu `-c 4`） |
| **ASR** | **faster-whisper**（本地）+ **Groq API**（主力） | `ASR_PROVIDER` 切换；本地 CTranslate2 加速，API 侧 `whisper-large-v3-turbo` |
| **视频下载** | yt-dlp | 1800+ 平台支持（首版仅启用 yt-dlp 通用下载；抖音无 Cookie 解析/字幕提取 ⏳ 延后 Phase 2+） |
| **OCR** | **PaddleOCR**（本地）+ **ocr.space**（主力） | `OCR_PROVIDER` 切换；中文识别优于 Tesseract |
| **向量检索** | Qdrant | 纯 Rust、单节点高性能、Payload 过滤 |
| **关键词检索** | Rank-BM25 + jieba | 纯 Python，无额外服务；jieba 分詞修 CJK 检索退化 |
| **RAG 编排** | **自研**（无 LlamaIndex / LangChain） | 检索链路自持：`HybridRetriever` → `rrf_fuse` → Reranker → `ContextExpander` |
| **Agent 编排** | **自研 AgentLoop**（无 LangChain） | 核心循环自研，边界清晰可控 |
| **向量模型** | BGE-M3 / BAAI 系列 | Sentence-Transformers 封装；重排用 `BAAI/bge-reranker-v2-m3` |
| **重排** | OpenRouter `/rerank` 端点 + 本地 BGE 双路径 | `rerank_backend.py` 按可用性选择后端 |
| **本地 LLM** | Ollama (Qwen2.5-7B INT4) | ⏳ **仅预留**：`embed.py` API 路径为 TODO 占位，未接通 |
| **前端** | **React 18 + TypeScript + Vite + SSE** | 状态层为 **TanStack Query**（服务端状态）+ `useState`；Tailwind + Radix；无状态库（zustand 依赖存在但零引用） |

---

## 🔗 核心模块映射表（文档↔代码）

| 文档 | 对应代码路径 | 核心类/函数 |
|------|-------------|------------|
| VIDEO-PIPELINE.md | `core/video_pipeline/` | `Downloader`, `Transcoder`, `ASREngine`, `OCREngine`, `EmbeddingBackend`, `Indexer` |
| RAG-RETRIEVAL.md | `core/rag/` | `HybridRetriever`, `rrf_fuse`, `rerank_backend`(`OpenRouterRerankBackend`/`LocalBGERerankBackend`), `ContextExpander`, `semantic_cache_lookup` |
| AGENT-LOOP.md | `core/agent_loop/` | `AgentLoop`, `Planner`, `Executor`, `Critic`, `EvidenceVerifier` |
| MODEL-GATEWAY.md | `core/model_gateway/` | `RoutingLLMService`, `ModelHealthStore`, `TokenAccounting` |
| TASK-ORCHESTRATION.md | `application/task_orchestration/` | `tasks`(含 `on_failure` 终态落库), `GPUResourceManager`, `broadcast_progress`, `agent_runner` |
| INTENT-ROUTING.md | `core/intent/` | `IntentRouter`, `QueryRewriter`, `RuleRewriter` |
| DATA-MODEL.md | `infrastructure/storage/models.py` | 24 个 SQLAlchemy 模型 |
| FRONTEND.md | `frontend/src/` | `main.tsx`, `app/`(路由), `features/`, `components/`, `store/` |

**顶层目录职责**（`src/videomind/`）：

| 目录 | 职责 |
|------|------|
| `core/` | 领域核心：`video_pipeline` / `rag` / `agent_loop` / `model_gateway` / `intent` + `errors.py`（错误分级） |
| `application/` | 用例编排：`task_orchestration`（Celery 任务、GPU 调度、SSE 广播） |
| `infrastructure/` | 外部适配：`storage`(ORM) / `vector`(Qdrant) / `cache`(Redis) / `media`(FFmpeg) |
| `interface/` | HTTP 层：FastAPI app + `routes/`（video/rag/agent/user/health/sse） |
| `observability/` | 日志 / 指标 / Trace / 中间件 |

---

## ⚡ 快速导航指引

- **读代码前** → [DECISIONS.md](docs/DECISIONS.md)（设计↔实现偏离）
- **前端规范** → [FRONTEND.md](docs/FRONTEND.md)
- **安全相关** → [SECURITY.md](docs/SECURITY.md)
- **部署运维** → [DEPLOYMENT.md](docs/DEPLOYMENT.md)
- **面试准备** → [INTERVIEW-GUIDE.md](docs/INTERVIEW-GUIDE.md)
- **总架构** → [ARCHITECTURE.md](docs/ARCHITECTURE.md)

---

## 📋 实施里程碑（参考方案.md 约 8 周）

| Phase | 周期 | 目标 | 关键产出文档 |
|-------|------|------|-------------|
| **1. 基础设施** | 1.5 周 | FastAPI+React+Docker+JWT+上传列表 | ARCHITECTURE、DATA-MODEL、DEPLOYMENT、FRONTEND、SECURITY |
| **2. 视频管线** | 1.5 周 | yt-dlp+faster-whisper+PaddleOCR+Celery | VIDEO-PIPELINE、TASK-ORCHESTRATION、OBSERVABILITY |
| **3. RAG 检索** | 1.5 周 | Qdrant+BM25+RRF 融合+CrossEncoder 重排 | RAG-RETRIEVAL、DATA-MODEL(chunk 表) |
| **4. Agent 分析** | 2 周 | AgentLoop+Planner/Executor/Critic+SSE | AGENT-LOOP、MODEL-GATEWAY、INTENT-ROUTING |
| **5. 企业级特性** | 1.5 周 | 模型网关+熔断+意图识别+语义缓存+管理后台 | MODEL-GATEWAY、INTENT-ROUTING、OBSERVABILITY、DECISIONS |

---

## 🧭 设计原则

1. **可讲清边界**：哪里用框架、哪里自研，面试时能画出架构图并解释每层选型理由
2. **双路径可用性**：ASR/OCR/Embedding 均支持 `local`/`api` 切换——API 主力保证质量与速度，本地路径保证断网可用、成本可控
3. **工程化优先**：幂等、重试、熔断、观测、评测、Checkpoint——从 Day 1 落地
4. **单卡 8GB 可跑全链路**：串行错峰 GPU 调度，Whisper→OCR/Embedding 依次释放显存
5. **证据可溯源**：每个结论必须绑定 `timestampMs + source(ASR|OCR) + 原文片段`，Critic 强校验

---

## ⚠️ 已知文档/实现不一致（改动前先确认）

| 项 | 文档旧说法 | 实际实现 |
|---|---|---|
| ASR | openai-whisper | **faster-whisper** + Groq API 双路径 |
| RAG 编排 | LlamaIndex | **自研**（无 LlamaIndex 依赖） |
| Agent 编排 | 自研 + LangChain 工具集成 | **纯自研**（无 LangChain 依赖） |
| MCP 工具调用 | INTENT-ROUTING §8 设计了 `MCPToolRegistry` | **未实现**（DECISIONS.md 有记录） |
| 本地 LLM | Ollama Qwen2.5-7B 可跑 | **仅预留**，`embed.py` 为 TODO 占位 |

---

> **顶层方案参考**：`videomind-python方案.md`（保留作为高层总方案，本目录为其细化展开）
