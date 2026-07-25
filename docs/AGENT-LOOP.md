# VideoMind AgentLoop 设计

> Planner → Executor → Critic 闭环（≤2 轮）+ 证据强校验 + Checkpoint 断点恢复
> 核心参考：DOVideo-AI `AgentLoopService.java` + `EvidenceVerificationService.java` + `AgentCheckpointService.java`

---

## 1. AgentLoop 总览

```
用户目标 (Goal)
      │
      ▼
┌─────────────────────────────────────────────────────────────────┐
│                        AgentLoop                                 │
│                                                                  │
│  Round 0: 初始化                                                  │
│  ├─ 加载 VideoContext (segments, full_transcript, evidence_frames)│
│  └─ 创建 AgentState(goal, plan=None, result=None, critique=None, round=0) │
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Round 1..MAX_ROUNDS (默认 2)                               │  │
│  │                                                            │  │
│  │  ┌─────────────┐                                           │  │
│  │  │  PLANNER    │  目标 → 1-5 个可执行子任务                  │  │
│  │  │             │  仅依赖 ASR/OCR/时间戳，禁止主观推测        │  │
│  │  └──────┬──────┘                                           │  │
│  │         │                                                    │  │
│  │         ▼                                                    │  │
│  │  ┌─────────────┐                                           │  │
│  │  │  EXECUTOR   │  逐项执行 → 结构化结果                     │  │
│  │  │             │  {title, conclusions[], evidence[],       │  │
│  │  │             │   suggestions[]}                          │  │
│  │  └──────┬──────┘                                           │  │
│  │         │                                                    │  │
│  │         ▼                                                    │  │
│  │  ┌─────────────┐                                           │  │
│  │  │  CRITIC     │  目标覆盖/结构完整/时间戳证据/无幻觉       │  │
│  │  │             │  passed? → 输出最终结果                    │  │
│  │  │             │  failed → feedback + requiredTimestamps   │  │
│  │  └──────┬──────┘                                           │  │
│  │         │                                                    │  │
│  │         ├─▶ passed → BREAK                                   │  │
│  │         │                                                    │  │
│  │         └─▶ failed → 证据强校验 → 定向补证据 → 修正 Plan → NEXT ROUND │
│  │                                                            │  │
│  │  Checkpoint: 每轮持久化 AgentState (PostgreSQL + Redis)      │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
      │
      ▼
最终结果：AnalysisResult + Evidence(可溯源到毫秒级时间戳)
```

---

## 2. 核心数据结构

```python
@dataclass
class AgentState:
    goal: str
    plan: AgentPlan | None = None
    result: AnalysisResult | None = None
    critique: CriticResult | None = None
    round: int = 0
    video_context: VideoContext | None = None
    trace_id: str = field(default_factory=lambda: uuid4().hex[:32])

@dataclass
class AgentPlan:
    tasks: list[SubTask]  # 1-5 个
    reasoning: str        # 规划理由

@dataclass
class SubTask:
    id: str               # "task_1", "task_2"...
    description: str      # 具体执行描述
    required_evidence_type: Literal["asr", "ocr", "frame", "mixed"]
    time_range_hint: tuple[int, int] | None  # (start_ms, end_ms) 可选提示

@dataclass
class AnalysisResult:
    title: str
    conclusions: list[Conclusion]
    evidence: list[Evidence]
    suggestions: list[str]  # 后续追问建议

@dataclass
class Conclusion:
    point: str                    # 核心观点
    evidence_ids: list[str]       # 引用的 Evidence ID
    confidence: float             # 0-1

@dataclass
class Evidence:
    id: str                       # ChunkEvidenceID: EID_{chunk_id[:8]}_{idx:02d}(统一格式,见 INTENT-ROUTING.md make_evidence_id)
    timestamp_ms: int             # 视频时间戳（毫秒）
    source: Literal["asr", "ocr", "frame"]
    content: str                  # 原文片段（≤500字符）
    chunk_id: UUID                # 关联 chunk 表

@dataclass
class CriticResult:
    passed: bool
    feedback: str                 # 给 Executor 的修正建议
    required_timestamps: list[int]  # 必须补证据的时间戳（毫秒）
    coverage_score: float         # 目标覆盖度 0-1
    structure_ok: bool            # 结构是否完整
    evidence_verified: bool       # 证据是否全部核验通过
    hallucination_risk: float     # 幻觉风险 0-1
```

---

## 3. 三角色实现

### 3.1 Planner（规划器）

```python
class Planner:
    SYSTEM_PROMPT = """
    你是视频分析任务规划师。给定用户目标和视频上下文（已分段的 ASR+OCR），
    将目标拆解为 1-5 个具体可执行的子任务。
    
    规则：
    1. 每个子任务必须基于视频证据（ASR/OCR/关键帧）可验证
    2. 禁止主观推测、外部知识、未来预测
    3. 子任务粒度：单一事实核查、观点提取、时间轴定位、对比/归纳
    4. 输出 JSON：{"tasks": [...], "reasoning": "..."}
    
    视频上下文摘要：{context_summary}
    用户目标：{goal}
    """
    
    def __init__(self, llm: LLMClient):
        self.llm = llm
    
    async def plan(self, state: AgentState) -> AgentPlan:
        context_summary = self._summarize_context(state.video_context)
        
        response = await self.llm.chat(
            messages=[{"role": "user", "content": self.SYSTEM_PROMPT.format(
                context_summary=context_summary,
                goal=state.goal
            )}],
            temperature=0.2,
            response_format={"type": "json_object"}
        )
        
        data = json.loads(response.content)
        tasks = [
            SubTask(
                id=f"task_{i+1}",
                description=t["description"],
                required_evidence_type=t.get("evidence_type", "mixed"),
                time_range_hint=tuple(t["time_range"]) if t.get("time_range") else None
            )
            for i, t in enumerate(data["tasks"][:5])  # 最多 5 个
        ]
        
        return AgentPlan(tasks=tasks, reasoning=data["reasoning"])
    
    def _summarize_context(self, ctx: VideoContext) -> str:
        """生成给 Planner 看的上下文摘要（Token 友好）"""
        return f"""
    视频时长：{ms_to_hms(ctx.duration_ms)}
    总段数：{len(ctx.segments)} (每段 60s)
    关键帧数：{sum(len(s.evidence_frames) for s in ctx.segments)}
    全文前 2000 字符：{ctx.full_transcript[:2000]}...
    典型段落示例：
    {chr(10).join(f"  [{ms_to_ts(s.start_ms)}-{ms_to_ts(s.end_ms)}] {s.transcript[:100]}..." for s in ctx.segments[:3])}
    """
```

---

### 3.2 Executor（执行器）

```python
class Executor:
    SYSTEM_PROMPT = """
    你是视频证据分析师。给定子任务和相关证据片段，生成结构化结论。
    
    规则：
    1. 仅使用提供的证据，不可编造
    2. 每个结论必须绑定证据 ID 和时间戳
    3. 证据 ID 格式：EID_{chunk_id[:8]}_{idx:02d}（统一格式，见 INTENT-ROUTING.md make_evidence_id）
    4. 输出 JSON：{"title": "...", "conclusions": [...], "suggestions": [...]}
    
    子任务：{task}
    相关证据：{evidence}
    """
    
    def __init__(self, llm: LLMClient, retriever: HybridRetriever):
        self.llm = llm
        self.retriever = retriever
    
    async def execute(self, state: AgentState) -> AnalysisResult:
        plan = state.plan
        all_conclusions = []
        all_evidence = []
        all_suggestions = []
        
        for task in plan.tasks:
            # 1. 检索该任务相关证据
            evidence_chunks = await self._retrieve_for_task(task, state.video_context)
            
            # 2. 构建证据上下文
            evidence_text = self._format_evidence(evidence_chunks)
            
            # 3. LLM 生成结论
            response = await self.llm.chat(
                messages=[{"role": "user", "content": self.SYSTEM_PROMPT.format(
                    task=task.description,
                    evidence=evidence_text
                )}],
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            
            data = json.loads(response.content)
            
            # 4. 解析结论并绑定证据
            for conc in data.get("conclusions", []):
                conclusion = Conclusion(
                    point=conc["point"],
                    evidence_ids=conc["evidence_ids"],
                    confidence=conc.get("confidence", 0.8)
                )
                all_conclusions.append(conclusion)
                
                # 补充 Evidence 详情
                for eid in conc["evidence_ids"]:
                    if eid not in [e.id for e in all_evidence]:
                        ev = self._find_evidence(eid, evidence_chunks)
                        if ev:
                            all_evidence.append(ev)
            
            all_suggestions.extend(data.get("suggestions", []))
        
        # 去重建议
        unique_suggestions = list(dict.fromkeys(all_suggestions))[:5]
        
        return AnalysisResult(
            title=data.get("title", f"分析：{plan.tasks[0].description[:30]}"),
            conclusions=all_conclusions,
            evidence=all_evidence,
            suggestions=unique_suggestions
        )
    
    async def _retrieve_for_task(self, task: SubTask, ctx: VideoContext) -> list[SearchResult]:
        """针对子任务检索证据：优先时间范围提示，否则语义检索"""
        if task.time_range_hint:
            start, end = task.time_range_hint
            return await self.retriever.retrieve_by_time_range(ctx.media_id, start, end)
        else:
            return await self.retriever.retrieve(
                query=task.description,
                media_id=ctx.media_id,
                top_k=10
            )
```

---

### 3.3 Critic（评论家/校验器）

```python
class Critic:
    SYSTEM_PROMPT = """
    你是严格的质量审查员。检查分析结果是否满足用户目标、结构完整、证据可核验。
    
    评估维度：
    1. 目标覆盖度：是否回答了用户目标的所有关键点
    2. 结构完整性：title/conclusions/evidence/suggestions 是否齐全
    3. 证据绑定：每个 conclusion 是否有 evidence_ids，每个 evidence 是否有 timestamp_ms+source+content
    4. 证据核验：时间戳是否在视频范围内，内容是否真实存在于 ASR/OCR 中
    5. 无幻觉：结论是否超出证据范围
    
    输出 JSON：
    {
      "passed": true/false,
      "feedback": "给 Executor 的具体修正建议",
      "required_timestamps": [123456, 234567],  // 必须补证据的时间点
      "coverage_score": 0.9,
      "structure_ok": true,
      "evidence_verified": true/false,
      "hallucination_risk": 0.1
    }
    
    用户目标：{goal}
    分析结果：{result_json}
    视频时长：{duration_ms}ms
    """
    
    def __init__(self, llm: LLMClient, verifier: EvidenceVerifier):
        self.llm = llm
        self.verifier = verifier
    
    async def critique(self, state: AgentState) -> CriticResult:
        result = state.result
        result_json = json.dumps({
            "title": result.title,
            "conclusions": [
                {"point": c.point, "evidence_ids": c.evidence_ids, "confidence": c.confidence}
                for c in result.conclusions
            ],
            "evidence": [
                {"id": e.id, "timestamp_ms": e.timestamp_ms, "source": e.source, "content": e.content[:200]}
                for e in result.evidence
            ],
            "suggestions": result.suggestions
        }, ensure_ascii=False)
        
        # 1. LLM 评估
        response = await self.llm.chat(
            messages=[{"role": "user", "content": self.SYSTEM_PROMPT.format(
                goal=state.goal,
                result_json=result_json,
                duration_ms=state.video_context.duration_ms
            )}],
            temperature=0.0,
            response_format={"type": "json_object"}
        )
        
        data = json.loads(response.content)
        
        # 2. 硬性证据核验（EvidenceVerifier）
        verified, failed_evidence = await self.verifier.verify_all(
            result.evidence,
            state.video_context
        )
        
        # 3. 合并结果：证据核验失败强制 passed=false
        passed = data["passed"] and verified
        required_timestamps = list(set(data.get("required_timestamps", []) + 
                                       [e.timestamp_ms for e in failed_evidence]))
        
        return CriticResult(
            passed=passed,
            feedback=data["feedback"],
            required_timestamps=required_timestamps,
            coverage_score=data["coverage_score"],
            structure_ok=data["structure_ok"],
            evidence_verified=verified,
            hallucination_risk=data["hallucination_risk"]
        )
```

---

### 3.4 EvidenceVerifier（证据强校验器）

```python
class EvidenceVerifier:
    """在原始 ASR/OCR 中核验证据的时间戳和内容真实性"""
    
    TOLERANCE_MS = 5000  # 允许 ±5s 容差
    CONTENT_SIMILARITY_THRESHOLD = 0.7  # 字符级相似度阈值
    
    async def verify_all(self, evidence: list[Evidence], ctx: VideoContext) -> tuple[bool, list[Evidence]]:
        failed = []
        for ev in evidence:
            ok = await self._verify_one(ev, ctx)
            if not ok:
                failed.append(ev)
        return len(failed) == 0, failed
    
    async def _verify_one(self, ev: Evidence, ctx: VideoContext) -> bool:
        # 1. 时间戳范围检查
        if not (0 <= ev.timestamp_ms <= ctx.duration_ms):
            return False
        
        # 2. 定位到对应 segment
        segment = self._find_segment(ctx, ev.timestamp_ms)
        if not segment:
            return False
        
        # 3. 根据 source 类型核验内容
        if ev.source == "asr":
            return self._verify_asr(ev, segment)
        elif ev.source == "ocr":
            return self._verify_ocr(ev, segment)
        elif ev.source == "frame":
            return self._verify_frame(ev, segment)
        return False
    
    def _verify_asr(self, ev: Evidence, segment: VideoSegment) -> bool:
        # 在 segment.transcript 中查找 ev.content 子串（模糊匹配）
        return self._fuzzy_contains(segment.transcript, ev.content)
    
    def _verify_ocr(self, ev: Evidence, segment: VideoSegment) -> bool:
        # 在 segment.ocr_texts 列表中查找
        for ocr in segment.ocr_texts:
            if self._fuzzy_contains(ocr, ev.content):
                return True
        return False
    
    def _verify_frame(self, ev: Evidence, segment: VideoSegment) -> bool:
        # 在 evidence_frames 中按 timestamp_ms 精确匹配
        for frame in segment.evidence_frames:
            if abs(frame.timestamp_ms - ev.timestamp_ms) <= self.TOLERANCE_MS:
                if frame.ocr_text and self._fuzzy_contains(frame.ocr_text, ev.content):
                    return True
        return False
    
    def _fuzzy_contains(self, haystack: str, needle: str) -> bool:
        """字符级编辑距离相似度"""
        if needle in haystack:
            return True
        # 允许部分匹配：needle 的核心词在 haystack 中
        needle_words = set(jieba.lcut(needle))
        haystack_words = set(jieba.lcut(haystack))
        overlap = len(needle_words & haystack_words) / max(len(needle_words), 1)
        return overlap >= self.CONTENT_SIMILARITY_THRESHOLD
    
    def _find_segment(self, ctx: VideoContext, timestamp_ms: int) -> VideoSegment | None:
        for seg in ctx.segments:
            if seg.start_ms <= timestamp_ms < seg.end_ms:
                return seg
        # 边界情况：最后一帧
        if timestamp_ms == ctx.duration_ms and ctx.segments:
            return ctx.segments[-1]
        return None
```

---

## 4. AgentLoop 主循环

```python
class AgentLoop:
    MAX_ROUNDS = 2
    
    def __init__(
        self,
        planner: Planner,
        executor: Executor,
        critic: Critic,
        checkpoint_store: CheckpointStore,
        sse_broadcaster: SSEBroadcaster
    ):
        self.planner = planner
        self.executor = executor
        self.critic = critic
        self.checkpoint = checkpoint_store
        self.sse = sse_broadcaster
    
    async def run(self, goal: str, video_context: VideoContext, trace_id: str) -> AnalysisResult:
        state = AgentState(
            goal=goal,
            video_context=video_context,
            trace_id=trace_id
        )
        
        # 发送初始事件
        await self.sse.send(trace_id, TaskEvent(stage="planning", message="开始规划..."))
        
        for round_num in range(1, self.MAX_ROUNDS + 1):
            state.round = round_num
            
            # === PLANNER ===
            await self.sse.send(trace_id, TaskEvent(stage="planning", message=f"第 {round_num} 轮规划"))
            plan = await self.planner.plan(state)
            state.plan = plan
            
            # Checkpoint: 规划完成
            await self.checkpoint.save(Checkpoint(
                task_id=UUID(trace_id),  # = analysis_task.id（FK）；trace_id 为其 hex 形式
                trace_id=trace_id,  # 32-hex，日志/Trace 串联
                round=round_num,
                phase="planning",
                agent_state=state
            ))
            
            # === EXECUTOR ===
            await self.sse.send(trace_id, TaskEvent(stage="executing", message=f"第 {round_num} 轮执行"))
            result = await self.executor.execute(state)
            state.result = result
            
            # Checkpoint: 执行完成
            await self.checkpoint.save(Checkpoint(
                task_id=UUID(trace_id),  # = analysis_task.id（FK）；trace_id 为其 hex 形式
                trace_id=trace_id,  # 32-hex，日志/Trace 串联
                round=round_num,
                phase="executing",
                agent_state=state
            ))
            
            # === CRITIC ===
            await self.sse.send(trace_id, TaskEvent(stage="critic_check", message=f"第 {round_num} 轮评审"))
            critique = await self.critic.critique(state)
            state.critique = critique
            
            # Checkpoint: 评审完成
            await self.checkpoint.save(Checkpoint(
                task_id=UUID(trace_id),  # = analysis_task.id（FK）；trace_id 为其 hex 形式
                trace_id=trace_id,  # 32-hex，日志/Trace 串联
                round=round_num,
                phase="critic_check",
                agent_state=state,
                critique=critique
            ))
            
            if critique.passed:
                await self.sse.send(trace_id, TaskEvent(stage="completed", message="分析通过"))
                break
            
            # 未通过：准备下一轮
            await self.sse.send(trace_id, TaskEvent(
                stage="retry",
                message=f"第 {round_num} 轮未通过，准备补证据: {critique.feedback}"
            ))
            
            # 将 required_timestamps 注入下一轮 Planner 上下文
            state = self._prepare_next_round(state, critique)
        
        else:
            # 超过最大轮数
            await self.sse.send(trace_id, TaskEvent(
                stage="failed",
                message=f"达到最大轮数 {self.MAX_ROUNDS}，返回当前最佳结果"
            ))
        
        # 最终结果 Checkpoint
        await self.checkpoint.save(Checkpoint(
            task_id=UUID(trace_id),  # = analysis_task.id（FK）；trace_id 为其 hex 形式
            trace_id=trace_id,  # 32-hex，日志/Trace 串联
            round=state.round,
            phase="completed",
            agent_state=state,
            result=state.result
        ))
        
        return state.result
    
    def _prepare_next_round(self, state: AgentState, critique: CriticResult) -> AgentState:
        """构建下一轮状态：保留已验证结论，标记需补证据"""
        # 这里简化：直接返回当前 state，Planner 会读取 critique.required_timestamps
        # 实际可实现：过滤掉未验证的结论，构建 "revision_context"
        return state
```

---

## 5. Checkpoint 断点恢复

**存储分层**（PostgreSQL 真源 + Redis 热缓存）：

```python
class CheckpointStore:
    def __init__(self, pg_repo: CheckpointRepo, redis: Redis):
        self.pg = pg_repo
        self.redis = redis
    
    async def save(self, cp: Checkpoint):
        # 1. PostgreSQL 持久化（真源）
        await self.pg.upsert(cp)
        
        # 2. Redis 热缓存（最近 10 轮）
        key = f"checkpoint:{cp.task_id}:{cp.round}:{cp.phase}"
        await self.redis.setex(key, 3600, cp.model_dump_json())
        
        # 3. 维护任务级索引
        await self.redis.lpush(f"checkpoint:index:{cp.task_id}", key)
        await self.redis.ltrim(f"checkpoint:index:{cp.task_id}", 0, 19)  # 保留 20 条
    
    async def load_latest(self, task_id: str) -> Checkpoint | None:
        # 优先 Redis
        index = await self.redis.lrange(f"checkpoint:index:{task_id}", 0, 0)
        if index:
            data = await self.redis.get(index[0])
            if data:
                return Checkpoint.model_validate_json(data)
        
        # 回落 PostgreSQL
        return await self.pg.get_latest(task_id)
    
    async def load_round(self, task_id: str, round: int, phase: str) -> Checkpoint | None:
        key = f"checkpoint:{task_id}:{round}:{phase}"
        data = await self.redis.get(key)
        if data:
            return Checkpoint.model_validate_json(data)
        return await self.pg.get(task_id, round, phase)
```

**恢复入口**：
```python
async def resume_analysis(task_id: str) -> AnalysisResult:
    # 1. 加载最新 Checkpoint
    cp = await checkpoint_store.load_latest(task_id)
    if not cp:
        raise ResumeError("无可用检查点")
    
    # 2. 重建 AgentState
    state = cp.agent_state
    
    # 3. 从中断阶段继续
    if cp.phase == "planning":
        # 规划已完成，直接进入 Executor
        return await agent_loop._run_from_executor(state)
    elif cp.phase == "executing":
        # 执行中断，重跑 Executor
        return await agent_loop._run_from_executor(state)
    elif cp.phase == "critic_check":
        # 评审完成，根据结果决定
        if cp.critique.passed:
            return state.result
        else:
            return await agent_loop._run_from_critic(state, cp.critique)
    elif cp.phase == "completed":
        return state.result
```

---

## 6. 追问复用

```python
async def follow_up(media_id: UUID, previous_trace_id: str, new_goal: str) -> AnalysisResult:
    # 1. 加载原 VideoContext（无需重新 ASR/OCR）
    video_ctx = await video_context_repo.get(media_id)
    
    # 2. 可选：加载上轮 AgentState 作为上下文
    prev_cp = await checkpoint_store.load_latest(previous_trace_id)
    prev_conclusions = prev_cp.agent_state.result.conclusions if prev_cp else []
    
    # 3. 构建新目标（包含前情提要）
    enhanced_goal = f"""
    前轮分析结论摘要：
    {chr(10).join(f"- {c.point}" for c in prev_conclusions)}
    
    新目标：{new_goal}
    """
    
    # 4. 新 trace_id 跑新一轮 AgentLoop
    new_trace_id = uuid4().hex[:32]
    return await agent_loop.run(enhanced_goal, video_ctx, new_trace_id)
```

---

## 7. 监控指标

| 指标名 | 类型 | 标签 | 用途 |
|--------|------|------|------|
| `vm_agent_loop_rounds_total` | Histogram | trace_id | 轮数分布 |
| `vm_agent_loop_duration_seconds` | Histogram | trace_id, phase | 各阶段耗时 |
| `vm_critic_passed_total` | Counter | trace_id, passed | 通过/不通过计数 |
| `vm_evidence_verification_rate` | Gauge | trace_id | 证据核验通过率 |
| `vm_checkpoint_save_duration_seconds` | Histogram | phase | Checkpoint 写入耗时 |
| `vm_follow_up_reuse_rate` | Counter | trace_id | 追问复用 Context 比例 |

---

> **关联文档**：[ARCHITECTURE.md](ARCHITECTURE.md) · [RAG-RETRIEVAL.md](RAG-RETRIEVAL.md) · [VIDEO-PIPELINE.md](VIDEO-PIPELINE.md) · [TASK-ORCHESTRATION.md](TASK-ORCHESTRATION.md) · [MODEL-GATEWAY.md](MODEL-GATEWAY.md)