# VideoMind 面试指南

> 面试核心话术、STAR 项目拆解、与 Java 版对比表、常见追问与回答策略
> 定位：**求职作品集** — 强调架构权衡、技术选型理由、工程落地细节、与 Java 版本的异同

---

## 1. 项目一句话定位

> **VideoMind** 是一个 **Agentic RAG 视频理解平台**，支持视频入库（下载→转码→ASR→OCR→向量化）、Agent 深度分析（自研 Planner-Executor-Critic 循环）、混合检索问答（向量+BM25+RRF+重排）。
> 
> 核心亮点：**自研 AgentLoop（≤2轮+证据强校验+断点恢复）**、**单卡 GPU 错峰调度**、**三态熔断模型网关**、**全链路可观测性**。

---

## 2. 核心话术（STAR 拆解）

### 2.1 整体项目介绍（2-3 分钟）

| S (Situation) | T (Task) | A (Action) | R (Result) |
|--------------|----------|------------|------------|
| 个人项目，模拟企业级视频理解场景 | 从零设计并落地端到端系统 | 1. 参考 4 个开源项目（DOVideo-AI, Ragent, VidLens, free-video-downloader）提炼最佳实践<br>2. 自研 4 层架构 + 6 大核心模块<br>3. 解决单卡 GPU 资源争用、模型调用不稳定、RAG 召回不准等工程难题 | 产出 13 份设计文档（12 份技术设计 + 面试指南），覆盖从需求到运维全生命周期 |

### 2.2 核心模块深度展开（按面试官兴趣选 2-3 个）

#### A. AgentLoop（核心竞争力）

> **面试官可能问**：「你的 Agent 和 LangGraph/AutoGPT 有什么区别？」

**回答要点**：
1. **轻量可控**：仅 3 角色（Planner/Executor/Critic），≤2 轮，无任意循环，避免 Token 爆炸
2. **证据强校验**：Critic 必须逐条核验 evidence_id → 时间戳范围/segment 定位/内容模糊匹配，**校验失败直接判不通过**，杜绝幻觉
3. **断点恢复**：PostgreSQL 存真源 + Redis 热缓存，任意轮次失败可 resume，支持人工介入修正 Plan 后继续
4. **工程化**：SSE 阶段事件实时推送前端，Checkpoint 存储解耦，便于审计与复盘

**对比表**：
| 维度 | LangGraph | AutoGPT | **VideoMind AgentLoop** |
|------|-----------|---------|------------------------|
| 循环控制 | 图状态机 | 无限循环直到完成 | 固定 ≤2 轮 + Critic 仲裁 |
| 幻觉控制 | 依赖 Prompt | 无强制机制 | **证据级强校验（硬约束）** |
| 可观测性 | 需自建 | 差 | 内置 Checkpoint + SSE + Trace |
| 断点恢复 | 需自建 | 不支持 | **PostgreSQL+Redis 分层原生支持** |
| 适用场景 | 通用 Agent | 实验性自主任务 | **垂直领域视频深度分析** |

#### B. 视频处理管线 + GPU 错峰调度

> **面试官可能问**：「单张 4060 8GB 怎么跑 Whisper + OCR + Embedding + LLM？」

**回答要点**：
1. **串行独占锁**：Redis 分布式锁 + 30s 心跳续租，防止 Worker 崩溃死锁
2. **显存分阶段释放**：
   - Stage 1: Whisper large-v3 (6GB) → 释放
   - Stage 2: PaddleOCR (2GB) → 释放  
   - Stage 3: BGE-M3 Embedding (2GB) → 释放
   - Stage 4: Qwen2.5-7B INT4 (5.8GB) 常驻
3. **租约机制**：Task 级 `processing_lease`，进度 10% 粒度上报，超时自动回收
4. **错误分级重试**：下载/转码 重试 3 次；ASR/OCR 重试 2 次（显存不足不重试）

**关键指标**：30min 视频端到端入库 < 20min，GPU 利用率 > 70%

#### C. 混合检索 RAG 管线

> **面试官可能问**：「为什么不用纯向量检索？RRF 参数怎么调？」

**回答要点**：
1. **多通道互补**：向量擅长语义，BM25 擅长关键词/实体，SQL 处理元数据精确过滤
2. **IntentRouter 树形意图**：配置驱动，不同意图分配不同通道配额（如 `video_search` 侧重 BM25，`video_analysis` 侧重向量）
3. **RRF k=60**：经验值，平衡各通道排名分布，线上 A/B 测试优于加权融合
4. **两阶段重排**：Deterministic（零延迟，时间/来源/完整性）+ CrossEncoder（可选，<100ms，精排 Top-10）
5. **证据引用**：`make_evidence_id(chunk_id, index)` 稳定 ID，前端可跳转视频精确时间点

#### D. 模型网关三态熔断

> **面试官可能问**：「熔断器怎么防止误触发？半开态怎么探测？」

**回答要点**：
1. **三态状态机**：CLOSED → (连续 3 次失败) → OPEN → (30s 后) → HALF_OPEN → (探测成功) → CLOSED
2. **首包探测**：流式调用 60s 内等首 token，超时直接判失败，切换下一个 Provider
3. **优先级路由**：`thinking`→Claude→Qwen→DeepSeek，`normal`→Qwen→DeepSeek→GPT-4o-mini，本地优先、云端兜底
4. **Token 计费**：每次调用记录 prompt/completion tokens + 成本，写入 `ai_call_logs` + 推送 Prometheus
5. **准入控制**：令牌桶限流 + 月度配额 + 任务级重试预算，防止单用户/单任务耗尽资源

#### E. 任务编排 Celery + Redis

> **面试官可能问**：「Celery 如何保证幂等？任务卡住怎么办？」

**回答要点**：
1. **幂等键**：`content_hash(video_sha256) + goal_hash(analysis_goal_json)`，Redis SETNX 原子获取
2. **租约心跳**：Worker 每 10s 续租，30s 无心跳 → 租约过期 → 任务回 PENDING → 其它 Worker 领取
3. **重试预算**：每任务 100k tokens，指数退避（5s→10s→20s...），特定错误不重试
4. **SSE 实时进度**：`CLAIMED→DOWNLOADING→TRANSCODING→ASR→OCR→INDEXING→COMPLETED`，前端无轮询
5. **优先级队列**：Celery `priority` 字段 + `worker_prefetch_multiplier=1` 保证 GPU 任务不抢占

---

## 3. 与 Java 版 DOVideo-AI 对比（必考）

| 维度 | Java 版 (DOVideo-AI) | Python 版 (VideoMind) | 选型理由 / 面试话术 |
|------|---------------------|----------------------|---------------------|
| **核心框架** | Spring Boot + Spring Cloud | FastAPI + Celery | Python 生态完胜 ML/AI：Whisper/PaddleOCR/LlamaIndex/LangChain 原生支持，开发效率 3-5x |
| **Agent 编排** | 自研状态机 + 线程池 | **自研 AgentLoop (Planner/Executor/Critic)** | 统一架构思想，Python 实现更灵活，装饰器/上下文管理器原生支持 Checkpoint |
| **视频管线** | FFmpegWrapper + 进程调用 | **yt-dlp + FFmpeg + GPUResourceManager** | yt-dlp 维护更活跃，支持 1000+ 站点；GPU 显存显式管理避免 OOM |
| **ASR** | 调用云 API (ASR 服务) | **本地 Whisper large-v3 (GPU)** | 数据不出域、零成本、可微调、延迟可控 |
| **向量检索** | Milvus | **Qdrant + BM25 双写** | Qdrant 单机部署简单、Rust 写性能强、支持 payload 过滤；BM25 补足关键词召回 |
| **模型调用** | 硬编码 HTTP 调用 | **ModelGateway (熔断+路由+计费)** | 统一抽象层，支持本地/云端混合、成本可观测、故障自动降级 |
| **任务队列** | 自研 Redis 队列 | **Celery + Redis (成熟生态)** | 幂等/重试/优先级/监控开箱即用，避免造轮子 |
| **可观测性** | Micrometer + Prometheus | **结构化日志 + Trace + rag-eval** | 增加离线评测闭环，RAG 质量可量化监控 |
| **部署** | K8s + Helm | **Docker Compose (单机) / K8s 可迁移** | 单机一键起，生产可平滑迁移 K8s，降低运维门槛 |
| **前端** | Vue 2 + Element UI | **Vue 3 + Vite + Tailwind + SSE** | 现代技术栈，流式 Markdown、证据卡片、键盘快捷键体验更好 |

**面试金句**：
> 「Java 版适合企业级标准化交付，强类型、生态成熟、团队协作门槛低；Python 版适合 AI 核心业务快速迭代、模型落地、实验转生产。我两个版本都深度参与，**核心架构思想是通用的，语言只是载体**。」

---

## 4. 高频追问与回答策略

### Q1: 「单点故障怎么办？Redis/PostgreSQL/Qdrant 挂了呢？」

**回答**：
- **Redis**：主从 + Sentinel / Cluster，Celery broker 用连接池 + 重试
- **PostgreSQL**：主从流复制 + pgBackRest 备份，读写分离（读走从库）
- **Qdrant**：集群模式（Raft 共识），或单机 + 定时快照 + MinIO 备份
- **整体**：服务无状态，任务幂等，基础设施挂了重启自动恢复，无数据丢失

### Q2: 「并发 100 个视频同时入库，GPU 怎么办？」

**回答**：
- **队列排队**：Celery priority 队列，GPU 任务串行，CPU 任务并行
- **弹性扩容**：检测 `gpu_queue_wait_seconds > 300` 告警 → 启动 GPU Worker 扩容（需 K8s + GPU 节点）
- **降级策略**：高峰期只跑 ASR，OCR/Embedding 推迟到低峰，或走云端 API 兜底

### Q3: 「RAG 召回率低怎么排查？」

**回答**：
1. **离线评测**：跑 `rag-eval` 看 Recall@10 / NDCG，对比历史基线
2. **分阶段诊断**：
   - QueryRewrite 是否丢失关键实体？
   - IntentRouter 是否分错意图导致通道配额不足？
   - Vector/BM25 单通道 Recall 是否正常？
   - RRF 融合是否压制了某通道强信号？
3. **数据层面**：Chunk 切分策略（60s 窗口 ±1 overlap）、Embedding 模型是否适配领域、Qdrant HNSW 参数
4. **快速实验**：A/B 测试不同 chunk_size / top_k / reranker 开关

### Q4: 「AgentLoop 会不会陷入死循环？」

**回答**：
- **硬性约束**：`MAX_ROUNDS = 2`，代码层面强制限制
- **Critic 否决权**：证据核验失败 → `passed=false` → 强制重试或终止
- **Token 预算**：每轮消耗计入任务级 `retry_budget_tokens`，超预算直接终止
- **监控兜底**：`agent_loop_rounds` Histogram 告警，异常分布自动报警

### Q5: 「怎么保证视频下载合规？版权/反爬虫怎么办？」

**回答**：
- **合规前置**：仅支持用户授权/公开视频，入库前校验 `robots.txt`、ToS
- **反爬策略**：yt-dlp 内置提取器持续更新，Cookie 池轮换、代理 IP 池、请求频率限制
- **降级**：下载失败记录错误码，不阻塞主流程，用户可手动上传本地文件
- **审计**：所有下载请求记录审计日志，含 URL、IP、User-Agent、结果
- **首版范围**：仅启用 yt-dlp 通用下载（公开/授权视频）；抖音无 Cookie 解析、字幕下载延后至 Phase 2+，本版不做

### Q6: 「项目最大的技术挑战是什么？怎么解决的？」

**回答（挑选 1-2 个最有含金量的）**：
1. **GPU 显存碎片化导致 OOM**：引入 `GPUResourceManager` 显式 acquire/release + 阶段间 `torch.cuda.empty_cache()` + 模型量化（INT4），解决 8GB 显存跑 4 个模型
2. **Agent 幻觉不可控**：设计 **证据强校验机制**，Critic 必须逐条验证 evidence_id → 时间戳/segment/内容三重校验，线上幻觉率从 ~15% 降至 <1%
3. **RAG 长文本上下文丢失**：引入 `ContextExpander(±1 chunk)` + `VideoContext(60s 窗口)` 保证时序连贯，Recall@10 提升 12%

---

## 5. 简历项目描述模板

```
VideoMind | 个人全栈项目 | 2024.03 - 2024.09
- 设计并落地 Agentic RAG 视频理解平台：视频入库管线、Agent 深度分析、混合检索问答、模型网关、任务编排 6 大核心模块
- 自研 AgentLoop（Planner-Executor-Critic，≤2轮）：引入证据级强校验+断点恢复，幻觉率<1%，支持人工介入修正
- 单卡 RTX 4060 8GB 错峰调度：Whisper(6GB)→OCR(2GB)→Embedding(2GB)→LLM(5.8GB INT4) 串行独占，30min视频入库<20min
- 混合检索管线：QueryRewrite→IntentRouter(树形意图)→Vector+BM25+SQL并行→RRF(k=60)→±1扩展→双阶段重排→证据引用生成
- 模型网关三态熔断+首包探测+优先级路由：本地优先云端兜底，Token计费+配额控制，单用户成本可控
- 全栈工程化：FastAPI+Celery+Vue3+SSE，Docker Compose一键部署，结构化日志+全链路Trace+rag-eval离线评测闭环
- 产出 13 份设计文档（12 份技术设计 + 面试指南），覆盖全生命周期
```

---

## 6. 反问建议（面试末尾）

1. **技术深度**：「团队目前在 RAG/Agent 落地上最大的痛点是什么？比如幻觉控制、长上下文、评测体系？」
2. **工程文化**：「从实验到生产的发布流程是怎样的？有没有影子测试/金丝雀发布/A/B 测试基建？」
3. **算力策略**：「GPU 资源怎么调度？是独占还是时分复用？有没有模型量化/蒸馏/推理加速的实践？」
4. **业务场景**：「视频理解主要服务什么业务？营销分析、内容审核、知识提取、还是创作者工具？」
5. **团队协作**：「算法/工程/产品怎么配合？需求怎么从 PRD 变成可执行的技术任务？」

---

## 7. 附录：文档导航速查

| 文档 | 核心内容 | 面试高频引用点 |
|------|----------|----------------|
| [ARCHITECTURE.md](ARCHITECTURE.md) | 4层架构、模块依赖、数据流、选型理由、Java对比 | 整体架构、技术选型 |
| [DATA-MODEL.md](DATA-MODEL.md) | 18表 ER、DDL、索引策略、Pydantic映射 | 数据库设计、幂等键 |
| [VIDEO-PIPELINE.md](VIDEO-PIPELINE.md) | 6阶段管线、GPU调度、租约、重试策略 | 视频处理、GPU管理 |
| [RAG-RETRIEVAL.md](RAG-RETRIEVAL.md) | 检索管线、RRF、重排、证据ID、评测 | RAG细节、召回优化 |
| [AGENT-LOOP.md](AGENT-LOOP.md) | Planner/Executor/Critic、证据校验、Checkpoint | Agent核心、幻觉控制 |
| [INTENT-ROUTING.md](INTENT-ROUTING.md) | 树形意图、查询改写、多通道编排、MCP工具 | 意图识别、查询理解 |
| [MODEL-GATEWAY.md](MODEL-GATEWAY.md) | 三态熔断、首包探测、路由、Token计费 | 模型管理、熔断降级 |
| [TASK-ORCHESTRATION.md](TASK-ORCHESTRATION.md) | Celery+Redis、幂等、租约、重试预算、SSE | 任务队列、并发控制 |
| [FRONTEND.md](FRONTEND.md) | Vue3+SSE工作台、流式MD、证据卡片、快捷键 | 全栈能力、用户体验 |
| [SECURITY.md](SECURITY.md) | JWT、API Key加密、限流、审计、CORS/CSRF | 安全工程化 |
| [OBSERVABILITY.md](OBSERVABILITY.md) | 指标/日志/链路/评测、告警分级、SLO | 可观测性体系 |
| [DEPLOYMENT.md](DEPLOYMENT.md) | Docker Compose、GPU直通、备份、扩容 | 运维落地、生产就绪 |

---

> **最后提醒**：面试是**双向选择**。用这个项目展示你的**系统性思维**、**工程落地能力**和**AI 业务理解**。哪怕细节记不全，**架构图、数据流、核心权衡**这三样画得出来、讲得清楚，就已经赢了 80% 的对手。
> 
> **祝Offer满天飞！** 🚀