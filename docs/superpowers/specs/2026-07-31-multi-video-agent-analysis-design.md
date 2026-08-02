# Agent 深度分析多视频支持 — 设计稿

> 关联代码探查见会话内两份 Explore 报告（agent_loop 全栈 + RAG/迁移）。本设计已锁定 4 个架构决策（均取推荐项）。

## 背景：现状 ground truth（核查后，非推测）

阅读 `src/videomind/core/agent_loop/*` 与 `src/videomind/interface/routes/agent.py`、`src/videomind/infrastructure/storage/models.py`、`src/videomind/core/rag/*`、`alembic/versions/*` 后确认：

1. **Executor 空跑**（[executor.py:62-72](src/videomind/core/agent_loop/executor.py#L62)）：提交给 LLM 的 prompt 只有 `子任务：{task.description}`，无视频内容、无 media_id、无 RAG 检索。返回的 `evidence`（含 `timestamp_ms/source/content/chunk_id`）全部由 LLM 凭 goal 文本编造。**单视频现状即是空跑**，与多视频无关。

2. **Critic 硬校验自始即坏**：`critic.py` 调 `self._verifier.verify_all(result.evidence, duration_ms=0)`，`verify_one` 判定 `0 <= timestamp_ms <= duration_ms`，`duration_ms=0` ⇒ 所有 `timestamp_ms > 0` 的证据**一律校验失败**。`passed` 要求 `llm_passed AND verified_passed`，故 Critic 硬校验长期为 False。

3. **max_rounds 钳制**：`loop.py` `MAX_ROUNDS=2` 硬编码；`agent.py` `_run_agent_loop` 用 `effective_rounds = min(max_rounds, 2)`。API 收 `max_rounds 1-5` 但运行层钳到 ≤2，**1-5 区间实际从未生效**（1 会被 min 保为 1，但 3/4/5 被砍到 2）。

4. **AgentState 无 media 上下文**（[types.py](src/videomind/core/agent_loop/types.py)）：`AgentState(goal, plan, result, critique, round, trace_id)`，全程不存 media_id。`_run_agent_loop` 拿到 media_id 仅用于更新 DB 状态，从不注入 AgentState/Planner/Executor/Critic。

5. **API/DB 单视频**：`AnalyzeRequest` 为单个 `media_id: uuid.UUID`；`AnalysisTask` 有单 `media_id` FK + `UniqueConstraint(media_id, goal_hash)` 幂等键；`AgentResult.evidence_json` 每条仅 `id/timestamp_ms/source/content/chunk_id`，**无视频来源字段**。

6. **RAG 已支持多视频**（`routes/rag.py` `/search`、`/chat`）：接收 `media_ids: list[uuid.UUID]`，对每个 video 调 `rag_pipeline.search(db, query, media_id, top_k)` 再合并按 `score` 降序。`Evidence`（`core/rag/pipeline.py`）含 `id/chunk_id/content/score/start_ms/end_ms/source_type`。

7. **可复用遗留列**：`AgentCheckpoint.video_context_ref` JSONB 列**已存在但从未写入**——正好用来承载多视频检索上下文。

8. **Evidence 双 dataclass**：
   - agent_loop 侧 `Evidence`（`types.py`）：`id/timestamp_ms/source/content/chunk_id`。
   - RAG 侧 `Evidence`（`core/rag/pipeline.py`）：`id/chunk_id/content/score/start_ms/end_ms/source_type`。
   二者字段不对齐，重构需统一。

## 目标与非目标

### 目标
- 前端 Agent 深度分析页支持多选视频（照搬 RAGChat 多选模式）。
- 后端接收 `media_ids: list[uuid.UUID]`，Executor 跨所选视频做**真实 RAG 检索**，产出**真实证据**，证据标注来源视频。
- 顺带修三个已知 bug：Executor 空跑、Critic verify_all(duration_ms=0)、max_rounds 钳制。
- LLM 不再编造证据，只能引用真实检索命中的 evidence_id 产出结论。

### 非目标
- 不引入 Celery（保持 FastAPI `BackgroundTasks`）。
- 不改 `VectorRetriever` 单 media_id Qdrant filter（多视频走 N 次合并，与 `/rag` 一致）。
- 不改 RAG 检索/重排算法本身。
- 不处理视频未索引（status != ready）的降级检索——直接拒绝（与 `/rag` 一致）。
- 不做 agent 跨多视频的"对比矩阵"专用 UI——先让结论/evidence 带来源，对比由用户读结论完成。

## 已锁定的 4 个架构决策

| # | 决策 | 取向 |
|---|------|------|
| D1 | 顺带修复范围 | **全修**（Executor 接 RAG + Critic verify bug + max_rounds 真生效） |
| D2 | Critic 校验语义 | **真实命中校验**（chunk_id 命中真实检索集 + conclusion.evidence_ids 指向真实 evidence；放弃时间戳范围校验） |
| D3 | 检索策略 | **N 次单视频查询合并**，复用 `rag_pipeline.search`，不改 VectorRetriever |
| D4 | DB schema | **primary media_id 保留 + 新增 `analysis_task_media` 关联表 + `media_ids_hash` 幂等键** |

## 端到端数据流

```
前端 AgentAnalysisPage
  └─ checkbox 多选(照搬 RAGChat selectedMediaIds + 折叠面板 + count Badge)
     └─ POST /api/agent/analyze { goal, media_ids: UUID[], user_id, max_rounds: 1|2 }
        │
API (routes/agent.py)
  ├─ 校验：所有 media 存在且 status=='ready'（否则 404/409，逐个报错）
  ├─ primary = media_ids[0]
  ├─ media_ids_hash = sha256(",".join(sorted(str(m) for m in media_ids)))
  ├─ 幂等：(media_ids_hash, goal_hash) 复用未失败任务
  ├─ 建 AnalysisTask(media_id=primary, media_ids_hash, goal, goal_hash, max_rounds)
  ├─ 建 analysis_task_media 关联行：每条 (task_id, media_id, position=index)
  └─ BackgroundTasks: _run_agent_loop(task_id, goal, media_ids, max_rounds)
        │
_run_agent_loop
  ├─ AgentState(goal, media_ids, video_meta[filename/duration])
  ├─ 不再调 loop.run；保持逐阶段调用 + checkpoint（现状 idiom）
  │
  ├─ Planner：goal + 视频元信息(数量/文件名) → SubTasks
  │     每个 SubTask 新增 search_query（检索友好查询词）
  │
  ├─ Executor（重写）：
  │     for SubTask in plan.tasks:
  │       hits = 跨 media_ids 调 rag_pipeline.search(db, subtask.search_query or description, media_id) 合并
  │       real_evidence = 命中的真实 Evidence（每条带 media 来源）
  │       把 real_evidence(content + EID + 来源视频名) 作为 context 喂 LLM
  │       LLM 产出 conclusions(引用 evid)[只引用 context 中存在的 EID] + suggestions + title
  │     AnalysisResult.evidence = 真实检索并集（带 media 来源）
  │
  └─ Critic（改）：
        校验真实命中：
          (1) result.evidence 每条 chunk_id 必须在 Executor 实际检索集内
          (2) conclusion.evidence_ids 必须 ⊆ 真实 evidence_ids
        不再做时间戳范围校验（多视频无单一 duration）
        保留 LLM 判断 passed/coverage_score/hallucination_risk
        passed = llm_passed AND 真实命中校验通过
```

## 组件设计

### 1. 前端 `AgentAnalysisPage.tsx`

**改动**：
- 移除单选 `mediaId: string`，改为 `selectedMediaIds: string[]`。
- 目标视频区由 `<select>` 改为**可折叠复选面板**，照搬 RAGChat：
  - 折叠面板 header 显示 `agentAnalysis.videoSourcesCount` Badge（`{count} 个视频源`）。
  - 展开后列出 `status==='ready'` 视频，逐行 checkbox + 文件名 + 时长。
  - "全选/清空"快捷按钮（可选，首版可省，YAGNI）。
- 提交校验：`selectedMediaIds.length >= 1`。
- `analysisApi.create` 调用改传 `media_ids: selectedMediaIds`。
- 证据卡片渲染：`evidence_json` 每条新增来源视频 badge（`e.media_title`）；时间戳改显 `start_ms/end_ms`。

**i18n 新键**（`agentAnalysis.*`，en-US/zh-CN 各补）：
- `videoSources` / `videoSourcesCount`（`{count} 个视频源` / `{count} video source(s)`，与 ragChat 对齐命名） / `selectAtLeastOne` / `collapseVideoSources` / `expandVideoSources`。
- 现有 `targetVideo`/`selectVideo` 退役（保留键不删，避免引用断裂；或一并替换，二选一在 plan 定——倾向直接替换并删除旧键）。

### 2. API 层 `routes/agent.py`

**AnalyzeRequest**（改）：
```python
class AnalyzeRequest(BaseModel):
    goal: str = Field(..., min_length=1, max_length=5000)
    media_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=20, description="目标视频列表")
    user_id: uuid.UUID = Field(...)
    max_rounds: int = Field(2, ge=1, le=2, description="最大轮数；上限 2（Critic 仅 1→2 重试）")
```
> 注：`le=2` 反映真实能力（去掉虚高的 1-5），与运行层一致。`max_length=20` 防 N 次检索发散。

**响应模型不变**：`AnalyzeResponse`、`TaskStatusResponse`、`AgentResultResponse` 结构保持。`AgentResultResponse.evidence_json` 仍是 `list[Any]`（内容里多 media 字段，schema 不必改）。

**analyze 端点**（改）：
- 校验：遍历 `req.media_ids`，逐个 `db.get(MediaFile)`；任一不存在→404，任一 `status != 'ready'`→409，信息指明哪个 media。
- `primary = req.media_ids[0]`。
- `media_ids_hash = sha256(",".join(sorted(str(m) for m in req.media_ids))).hexdigest()`。
- 幂等查询改 `where(media_ids_hash == ..., goal_hash == ..., status not in ('failed',))`。
- 新建 `AnalysisTask(media_id=primary, media_ids_hash=..., ...)` 后，`session.add` 逐条关联：见下 ORM。
- `background_tasks.add_task(_run_agent_loop, task_id=..., goal=..., media_ids=list(req.media_ids), max_rounds=req.max_rounds)`。

### 3. DB / ORM `infrastructure/storage/models.py`

**AnalysisTask**（改）：
- 保留 `media_id` FK（primary，向后兼容现有按 media_id 的查询）。
- 新增 `media_ids_hash: Mapped[str] = mapped_column(String(64), index=True)`。
- 唯一约束：drop `uq_at_media_goal`，add `UniqueConstraint("media_ids_hash", "goal_hash", name="uq_at_mediaids_goal")`。

**新增 `AnalysisTaskMedia`**（关联表，含 primary）：
```python
class AnalysisTaskMedia(Base):
    __tablename__ = "analysis_task_media"
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    task_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("analysis_task.id", ondelete="CASCADE"), index=True)
    media_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("media_file.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)   # 0 = primary
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("task_id", "media_id", name="uq_atm_task_media"),)
```

`AgentResult` / `AgentCheckpoint` 结构不变；`video_context_ref` 复用存检索上下文 summary（可选，首版写入 media_ids + 各视频检索命中数，便于可观测性）。

### 4. 迁移 `alembic/versions/2026_07_31_xxxx_<rev>_multi_video_agent.py`

按 `2026_07_26_0030_add_user_config_table.py` idiom：
- `upgrade()`：
  - `op.add_column('analysis_task', sa.Column('media_ids_hash', sa.String(64), nullable=False, server_default=''))`
  - **不**回填旧行：旧任务由旧接口（单 `media_id` 字段路径）创建，新接口已无 `media_id` 字段，路径完全分离，旧行不会被新幂等查询复用。旧行 `media_ids_hash=''` 作为空标记可接受。
  - `op.drop_constraint('uq_at_media_goal', 'analysis_task', type_='unique')`
  - `op.create_unique_constraint('uq_at_mediaids_goal', 'analysis_task', ['media_ids_hash', 'goal_hash'])`
  - `op.create_index('ix_analysis_task_media_ids_hash', 'analysis_task', ['media_ids_hash'])`
  - `op.create_table('analysis_task_media', ...)` 含 FK + 唯一约束 + 索引。
- `downgrade()`：反向。
- revision id 用 alembic `file_template` 自动（`%%(rev)s`），down_revision 指向 `b1a7d2e3f901`。

### 5. AgentState `core/agent_loop/types.py`

扩展：
```python
@dataclass
class VideoMeta:
    media_id: str
    filename: str
    duration_ms: int | None

@dataclass
class AgentState:
    goal: str
    media_ids: list[str] = field(default_factory=list)
    video_meta: list[VideoMeta] = field(default_factory=list)
    plan: AgentPlan | None = None
    result: AnalysisResult | None = None
    critique: CriticResult | None = None
    round: int = 0
    trace_id: str = ""
    retrieved_evidence_ids: set[str] = field(default_factory=set)  # Executor 填充，Critic 读取
```

**SubTask** 扩展：`search_query: str = ""`（检索友好查询词；为空则 Executor 用 `description` 退化）。

**Evidence（agent_loop 侧）统一为承载真实检索结果**（扩展字段，对齐 RAG Evidence + 媒体来源）：
```python
@dataclass
class Evidence:
    id: str                       # EID_...
    chunk_id: str
    content: str
    source_type: str = ""        # asr | ocr | mixed（来自 RAG）
    score: float = 0.0
    start_ms: int | None = None
    end_ms: int | None = None
    media_id: str = ""            # 来源视频
    media_title: str = ""         # 来源视频文件名（便于前端直接渲染）
```
> 退役旧字段 `timestamp_ms`/`source`(frame|text|...)：前端改用 `start_ms`/`source_type`/`media_title`。`AnalysisResult.evidence_json` 序列化时带新字段。

### 6. Planner `core/agent_loop/planner.py`

- system prompt 增加：告知有 N 个视频（给文件名列表），子任务 `search_query` 应为面向检索的查询词（如"商业模式 价格 斜率"而非"找出商业模式"）。
- 解析 LLM JSON 增取 `search_query`，映射进 `SubTask.search_query`。
- **不**在此层注入 RAG（Planner 只规划，Executor 才检索，职责单一）。

### 7. Executor `core/agent_loop/executor.py`（重写核心）

伪码：
```
all_evidence = {}        # eid -> Evidence（真实）
all_evidence_by_chunk = {}
all_conclusions = []
all_suggestions = []
title = None

for subtask in plan.tasks:
    query = subtask.search_query or subtask.description
    round_hits = []      # 跨视频检索本子任务
    for media_id in state.media_ids:
        res = await rag_pipeline.search(db, query, uuid.UUID(media_id), top_k=top_k_per_video)
        for ev in res["evidence"]:
            eid = ev.id
            ev_full = Evidence(id=eid, chunk_id=ev.chunk_id, content=ev.content,
                               source_type=ev.source_type, score=ev.score,
                               start_ms=ev.start_ms, end_ms=ev.end_ms,
                               media_id=media_id, media_title=meta[media_id].filename)
            all_evidence[eid] = ev_full
            round_hits.append(ev_full)

    # 拼 context 喂 LLM：[EID] (来源: 视频名) content
    context_block = format_with_sources(round_hits)
    llm_out = await llm.chat(build_executor_prompt(subtask, context_block),
                             temperature=0.1)
    # LLM 产 { title?, conclusions:[{point, evidence_ids, confidence}], suggestions:[] }
    title = title or llm_out.title
    all_conclusions += llm_out.conclusions
    all_suggestions += llm_out.suggestions

# 过滤 final：仅保留 evidence_ids 命中 all_evidence 的 conclusion（防御 LLM 幻觉引用）
final_conclusions = [c for c in all_conclusions if set(c.evidence_ids) <= set(all_evidence)]
return AnalysisResult(title=title or state.goal[:64], conclusions=final_conclusions,
                      evidence=list(all_evidence.values()), suggestions=all_suggestions)
```

executor prompt 改为：给真实 evidence context，要求**只能引用 context 中出现的 evidence_id**，产出 conclusions（每个引用 ≥1 条 evidence）+ suggestions + 可选 title。明确禁止编造新的 evidence。

`top_k_per_video` 默认 5（N 个视频×5 ≤ 100 片段，控量）。

### 8. Critic `core/agent_loop/critic.py`（改校验语义）

- 删去 `verify_all(duration_ms=0)` 调用与 `EvidenceVerifier` 的时间戳范围校验依赖（`verifier.py` 可保留但 Critic 不再用其时间戳路径）。
- 引入 `EvidenceHitSet`：Executor 在 state 暴露本轮实际检索命中的 eid 集合（写到 `state.critique` 之前的 state）；实现上把命中集存进 `AnalysisResult`（新增非序列化字段? 更简单：Critic 自己重建——但 Critic 不再检索。故 Executor 把命中集放入 `state.result` 的内部字段）。**决策**：扩展 `AgentState` 加 `retrieved_evidence_ids: set[str]`，Executor 填充；Critic 读取校验。
- 硬校验：
  - `(1) result.evidence` 每条 `id ∈ retrieved_evidence_ids`（真实）
  - `(2) conclusion.evidence_ids ⊆ retrieved_evidence_ids`
- `passed = llm_passed AND 硬校验全通过`。
- `evidence_verified` 字段改为硬校验结果。

### 9. `_run_agent_loop` / max_rounds

- **`_run_agent_loop` 路径**（当前实际执行的入口，逐阶段调用 + checkpoint）：去掉 `effective_rounds = min(max_rounds, 2)` 钳制，改为 `for round_num in range(1, max_rounds + 1)` 并在 `critique.passed` 时 break；`max_rounds=1` 即只跑 1 轮。签名从 `media_id` 改 `media_ids: list[uuid.UUID]`，构造 `AgentState(goal, media_ids=[str(m) for m in media_ids], video_meta=await _load_video_meta(...))`。
- **`AgentLoop.run` 同步**（保持两路径能力一致、去硬编码）：`run(goal)` → `run(goal, max_rounds: int = 2)`，循环 `range(1, max_rounds + 1)`；删除模块级 `MAX_ROUNDS=2` 常量的硬编码使用。`_run_agent_loop` 路径当前不强行改用 `loop.run`（保留逐阶段 idiom 以减少回归面），但 `run` 仍同步签名以反映真实能力。
- checkpoint 写 `video_context_ref`：`{"media_ids": [...], "per_video_hit_counts": {...}}`（可观测）。

### 10. `analysisApi`（frontend `lib/api.ts`）

```ts
create: (data: { goal: string; media_ids: string[]; user_id: string; max_rounds?: number }) => ...
```

## 接口契约汇总

| 端点/契约 | Before | After |
|-----------|--------|-------|
| `AnalyzeRequest.media_id` | `uuid.UUID` 单值 | 删除，改 `media_ids: list[uuid.UUID]` |
| `AnalyzeRequest.max_rounds` | `ge=1, le=5` | `ge=1, le=2` |
| 幂等键 | `(media_id, goal_hash)` | `(media_ids_hash, goal_hash)` |
| `evidence_json` 每条 | `id/timestamp_ms/source/content/chunk_id` | `id/chunk_id/content/source_type/score/start_ms/end_ms/media_id/media_title` |
| `_run_agent_loop` 入参 | `media_id` 单值 | `media_ids` 列表 |
| Executor prompt | `子任务：{desc}` | 真实 evidence context + 引用约束 |
| Critic 硬校验 | `verify_all(duration_ms=0)`（恒失败） | 真实命中校验 |
| 运行 max_rounds | `min(max_rounds, 2)` | `max_rounds`（1 或 2） |

## 错误处理与边界

- media 不存在→404，未就绪→409，**逐个报错指明 media_id**（与 `/rag` 一致：`f"media {id} not found"` / `f"media {id} 未就绪"`）。
- `media_ids` 为空→422（Pydantic `min_length=1`）。
- 视频数量过多→422（`max_length=20`）。
- Executor 检索零命中（某子任务跨全视频无命中）：该子任务 conclusions 为空，不阻断整体；若**所有子任务都零命中**→Critic 判 `evidence_verified=False`→`passed=False`→走重试或最终 `failed`。
- LLM 返回非法 JSON：单个子任务 try/except 吞掉（现状 idiom：`continue`），不阻断其它子任务。
- 幂等命中已完成/运行中任务：返回现有 task，不重新调度（保持现状语义）。

## 测试策略

### 后端 pytest
新增 `tests/core/agent_loop/test_executor_multi_video.py`：
- Executor 跨 2 视频 mock `rag_pipeline.search` 返回不同命中→`AnalysisResult.evidence` 含两视频来源，`media_id/media_title` 正确。
- LLM（mock）产出 conclusion 引用不存在的 eid→被过滤剔除。
- 单子任务零命中→不崩，conclusions 为空。

`tests/core/agent_loop/test_critic_real_evidence.py`：
- evidence 全部命中 `retrieved_evidence_ids` 且 conclusion.evidence_ids ⊆ →  硬校验通过。
- 任一 conclusion 引用不存在的 eid → 硬校验失败→`passed=False`。
- LLM 判 passed=False → `passed=False`（短路）。

`tests/interface/test_agent_analyze_multi.py`：
- `POST /api/agent/analyze` 传 `media_ids`→建 task + 关联表行数 == len(media_ids)。
- 重复同 `media_ids+goal`→复用同一 task（幂等）。
- 乱序 `media_ids` 与正序应**复用同一 task**（`media_ids_hash` 排序归一）。
- 合一个不 ready media→409 指明 id。
- 合空 list→422。

`tests/migration/test_multi_video_migration.py`（或手测）：
- `alembic upgrade head` 后 `analysis_task_media` 表存在、约束齐。
- `downgrade -1` 干净回退。

### 前端 Playwright
- 打开 Agent 分析页，多选 2 视频，填 goal，提交→任务卡片出现、轮询进入 planning/executing。
- 完成后 result 的 evidence 卡每条带来源视频 badge。
- 切中英文，来源视频相关文案随语言变。

## 风险与对策

| 风险 | 对策 |
|------|------|
| AgentLoop 重写越界诱发回归 | 不强行复用 `loop.run`，保留 `_run_agent_loop` 逐阶段 idiom；新增 `retrieved_evidence_ids` 而非大改 AgentResult 序列化 |
| LLM 仍编 EID 引用 | Executor 过滤 + Critic 硬校验双重拦截 |
| 多视频检索慢（N×Qdrant） | `max_length=20` + `top_k_per_video=5` 控量；后续可升级 terms filter |
| 旧 `evidence_json` 字段变更致前端旧渲染崩 | 同次改前端渲染；evidence_json 由 JSONB 容纳，无 schema 强约束 |
| 幂等：旧单视频行 `media_ids_hash` 回填 | 回填为 `sha256(str(media_id))`，保持可复用（单视频 == media_ids=[primary]） |

## 提交与推送约束（重要）

- **`docs/superpowers/` 产物（本 spec、后续 plan、各 task report）不推送 GitHub**。提交/推送时显式排除该目录：`git add` 精确指定业务文件，禁止 `git add -A` / `git add .` 把 `docs/superpowers` 带入。如需本地留痕，`docs/superpowers` 维持本地 tracked 现状即可，不新增进 push 范围。
- 不修改 `.gitignore`（不把 `docs/superpowers` 加入忽略——那会改变仓库配置且冲击已 tracked 文件，超出"别管"范围）。
- 前端实现遵循已有约定：**前端不自动 commit**（用户明确指令），等用户确认后再提交。

## 任务分解（高层，留 writing-plans 细化）

1. DB 迁移 + ORM（`analysis_task_media` 表 + `media_ids_hash` 列 + 约束）。
2. `types.py` 扩展（AgentState/SubTask/VideoMeta/Evidence 字段对齐）。
3. Executor 重写接 RAG + 真实 evidence。
4. Critic 改真实命中校验 + `retrieved_evidence_ids`。
5. max_rounds 去钳制 + `_run_agent_loop` 多视频签名。
6. API `AnalyzeRequest` + analyze 端点 + 关联表写入 + 幂等。
7. 后端 pytest（Executor/Critic/API/迁移）。
8. 前端 `AgentAnalysisPage` 多选 UI + `analysisApi`。
9. 前端 i18n 新键（en-US/zh-CN）。
10. Playwright 实测 + 旧 evidence 字段渲染核对。
