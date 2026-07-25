# VideoMind — AI 应用工程师求职作品集

> **一句话定位**：面向 AI 应用/算法岗的企业级 **Agentic RAG 视频理解平台**——用户上传视频/链接 → 本地 ASR+OCR → 混合检索 → 自研 AgentLoop 多轮分析 → 带时间戳证据的结构化结论。全链路零 API 费用，单卡 8GB 显存串行错峰跑完。

---

## 📁 文档索引（按阅读顺序）

| 文档 | 核心内容 | 关联模块 |
|------|----------|----------|
| [ARCHITECTURE.md](docs/ARCHITECTURE.md) | 总架构：分层、数据流、技术栈选型理由、与 Java 版对比、GPU 调度策略 | 全局 |
| [DATA-MODEL.md](docs/DATA-MODEL.md) | 18 张 SQLAlchemy 表定义、ER 图、索引策略、迁移方案 | `core/storage/` |
| [VIDEO-PIPELINE.md](docs/VIDEO-PIPELINE.md) | 视频处理管线：下载→转码→分段 ASR→关键帧 OCR→VideoSegment 合并 | `core/video/` |
| [RAG-RETRIEVAL.md](docs/RAG-RETRIEVAL.md) | 混合检索：向量(Qdrant)+BM25→RRF→CrossEncoder重排→ContextExpander→引用溯源 | `core/rag/` |
| [AGENT-LOOP.md](docs/AGENT-LOOP.md) | 自研 AgentLoop：Planner→Executor→Critic 2轮、证据强校验、Checkpoint 断点恢复 | `core/agent/` |
| [MODEL-GATEWAY.md](docs/MODEL-GATEWAY.md) | 模型网关：三态熔断、优先级路由、首包探测、Token 计费、多供应商抽象 | `core/llm/` |
| [TASK-ORCHESTRATION.md](docs/TASK-ORCHESTRATION.md) | 任务编排：Celery+Redis、状态机、幂等、重试预算、SSE 阶段推送 | `core/task/` |
| [INTENT-ROUTING.md](docs/INTENT-ROUTING.md) | 意图识别树、查询改写拆分、多通道检索编排、MCP 工具调用集成 | `core/intent/` |
| [FRONTEND.md](docs/FRONTEND.md) | Vue3+Vite+SSE 工作台：视频库、Agent 工作台、流式 Markdown、证据卡片、键盘快捷键 | `frontend/` |
| [SECURITY.md](docs/SECURITY.md) | JWT 鉴权、API Key AES-GCM 加密、速率限流、审计日志、CORS/CSRF | `core/auth/` |
| [OBSERVABILITY.md](docs/OBSERVABILITY.md) | 结构化日志、Prometheus 指标、Grafana 看板、全链路 Trace、离线评测框架 | `core/observability/` |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Docker Compose 一键起、环境变量、GPU 直通、MinIO/Qdrant/Ollama 本地化部署 | 运维 |
| [INTERVIEW-GUIDE.md](docs/INTERVIEW-GUIDE.md) | 面试核心话术、STAR 项目拆解、与 Java 版对比表、常见追问与回答策略 | 求职准备 |

---

## 🎯 技术栈速览

| 层级 | 选型 | 关键理由 |
|------|------|----------|
| **Web 框架** | FastAPI + Uvicorn | 异步原生、自动 OpenAPI、SSE 原生 |
| **异步任务** | Celery + Redis | Python 生态自然替代 RocketMQ/Kafka |
| **ASR** | **Whisper (openai-whisper)** | 本地离线、高精度、GPU 加速、零 API 费用 |
| **视频下载** | yt-dlp | 1800+ 平台支持（首版仅启用 yt-dlp 通用下载；抖音无 Cookie 解析/字幕提取 ⏳ 延后实现 Phase 2+） |
| **OCR** | PaddleOCR | 中文识别优于 Tesseract |
| **向量检索** | Qdrant | 纯 Rust、单节点高性能、Payload 过滤 |
| **关键词检索** | Rank-BM25 | 纯 Python，无额外服务 |
| **RAG 编排** | LlamaIndex | 检索能力强于 LangChain |
| **Agent 编排** | 自研 AgentLoop + LangChain 工具集成 | 核心循环自研，边界清晰可控 |
| **向量模型** | BGE-M3 / BAAI 系列 | Sentence-Transformers 封装 |
| **本地 LLM** | Ollama (Qwen2.5-7B INT4) | 6GB VRAM 可跑，零 API 费用 |
| **前端** | Vue 3 + Vite + SSE | 与 Java 版共用前端设计 |

---

## 🔗 核心模块映射表（文档↔代码）

| 文档 | 对应代码路径 | 核心类/函数 |
|------|-------------|------------|
| VIDEO-PIPELINE.md | `core/video/` | `Downloader`, `ASRProcessor`, `OCRProcessor`, `VideoContextBuilder` |
| RAG-RETRIEVAL.md | `core/rag/` | `HybridRetriever`, `RRFusion`, `CrossEncoderReranker`, `ContextExpander` |
| AGENT-LOOP.md | `core/agent/` | `AgentLoop`, `Planner`, `Executor`, `Critic`, `EvidenceVerifier`, `CheckpointStore` |
| MODEL-GATEWAY.md | `core/llm/` | `RoutingLLMService`, `ModelHealthStore`, `LlmFirstPacketProbe`, `TokenAccounting` |
| TASK-ORCHESTRATION.md | `core/task/` | `TaskEngine`, `TaskStateMachine`, `IdempotencyKey`, `RetryScheduler`, `SSEBroadcaster` |
| INTENT-ROUTING.md | `core/intent/` | `IntentTree`, `QueryRewriter`, `MultiChannelRetrieval`, `MCPToolRegistry` |
| DATA-MODEL.md | `core/storage/models.py` | 18 个 SQLAlchemy 模型 |
| FRONTEND.md | `frontend/src/` | `App.vue`, `VideoLibrary`(路由), `AnalysisWorkbench`(路由), `StreamingMarkdown`, `EvidenceCard`, `TaskMonitorStore` |

---

## ⚡ 快速导航指引

- **前端规范** → [FRONTEND.md](docs/FRONTEND.md)
- **安全相关** → [SECURITY.md](docs/SECURITY.md)
- **部署运维** → [DEPLOYMENT.md](docs/DEPLOYMENT.md)
- **面试准备** → [INTERVIEW-GUIDE.md](docs/INTERVIEW-GUIDE.md)
- **总架构** → [ARCHITECTURE.md](docs/ARCHITECTURE.md)

---

## 📋 实施里程碑（参考方案.md 约 8 周）

| Phase | 周期 | 目标 | 关键产出文档 |
|-------|------|------|-------------|
| **1. 基础设施** | 1.5 周 | FastAPI+Vue+Docker+JWT+上传列表 | ARCHITECTURE、DATA-MODEL、DEPLOYMENT、FRONTEND、SECURITY |
| **2. 视频管线** | 1.5 周 | yt-dlp+Whisper+PaddleOCR+Celery | VIDEO-PIPELINE、TASK-ORCHESTRATION、OBSERVABILITY |
| **3. RAG 检索** | 1.5 周 | LlamaIndex+Qdrant+BM25+RRF融合 | RAG-RETRIEVAL、DATA-MODEL(chunk表) |
| **4. Agent 分析** | 2 周 | AgentLoop+Planner/Executor/Critic+SSE | AGENT-LOOP、MODEL-GATEWAY、INTENT-ROUTING |
| **5. 企业级特性** | 1.5 周 | 模型网关+熔断+意图识别+MCP+管理后台 | MODEL-GATEWAY、INTENT-ROUTING、OBSERVABILITY |

---

## 🧭 设计原则

1. **可讲清边界**：哪里用框架、哪里自研，面试时能画出架构图并解释每层选型理由
2. **零外部强依赖**：ASR/LLM/Embedding 全本地跑，断网可用，成本可控
3. **工程化优先**：幂等、重试、熔断、观测、评测、Checkpoint——从 Day 1 落地
4. **单卡 8GB 可跑全链路**：串行错峰 GPU 调度，Whisper→OCR/Embedding→Ollama 依次释放显存
5. **证据可溯源**：每个结论必须绑定 `timestampMs + source(ASR|OCR) + 原文片段`，Critic 强校验

---

> **顶层方案参考**：`videomind-python方案.md`（保留作为高层总方案，本目录为其细化展开）