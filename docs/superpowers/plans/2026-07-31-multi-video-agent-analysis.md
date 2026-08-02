# Agent 深度分析多视频支持 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 Agent 深度分析页支持多选视频，Executor 跨所选视频做真实 RAG 检索产出真实证据，顺带修复 Executor 空跑、Critic `verify_all(duration_ms=0)` 恒失败、`max_rounds` 被钳制三个 bug。

**Architecture:** 前端照搬 RAGChat 多选模式；后端 `AnalyzeRequest` 单 `media_id` 改 `media_ids: list[UUID]`，Executor 引入可注入 Retriever Protocol（默认 `_RagRetriever` 复用 `rag_pipeline.search` 做 N 次单视频合并），Critic 改真实命中校验（读 `state.retrieved_evidence_ids`）；DB 保留 primary `media_id` + 新增 `analysis_task_media` 关联表 + `media_ids_hash` 幂等键。不引入 Celery，不改 `VectorRetriever`，不改 RAG 检索算法。

**Tech Stack:** Python 3 / FastAPI / Pydantic v2 / SQLAlchemy 2.0 async / asyncpg / Alembic / pytest-asyncio / React + TypeScript / TanStack Query / react-i18next / Playwright。

## Global Constraints

- **Anaconda Python**：跑 pytest/alembic 用全路径 `/d/ProgramData/anaconda3/python.exe`，或 `conda run -n base python`；禁用裸 `python`/`python3`（会命中 WindowsApps 占位）。
- **pytest**：`pytest_asyncio` 已配 `asyncio_mode="auto"`；`AsyncMock` + `mock_llm.chat.return_value.content = json.dumps(...)` 是既有 idiom。`pg_session` fixture 首次调用自动跑 `alembic upgrade head`，DB 不可用时 infra guard 跳过。
- **文件命名/校验细节逐字对齐 `/rag`**：404 detail `f"media {media_id} not found"`，409 detail `f"media {media_id} 未就绪（status={media.status}）"`。
- **Alembic**：`file_template = %%(year)d_%%(month).2d_%%(day).2d_%%(hour).2d%%(minute).2d_%%(rev)s_%%(slug)s`；当前最新 revision `b1a7d2e3f901`（新迁移 down_revision 指它）。
- **Evidence 兼容**：`types.Evidence` 保留旧字段 `timestamp_ms`/`source`（带默认值，不物理删除），新代码不写入/不渲染它们——避免破坏 `verifier.py` 与 `test_verifier.py` 的旧引用。
- **docs/superpowers 不推送**：后端每个 task 用 `git add <业务文件>` 精确提交，禁用 `git add -A`/`git add .`（会把 `docs/superpowers` 带入暂存）；不修改 `.gitignore`。前端 task **不自动 commit**（等用户确认）。
- **D5 迁移回填决策**：spec 组件设计§4「不回填旧行」与风险表「回填 `sha256(str(media_id))`」矛盾。取**回填**——旧行 `media_ids_hash=''` 会让新建唯一约束 `(media_ids_hash, goal_hash)` 在「同 goal 不同 media」的旧行上碰撞。回填 `media_ids_hash = sha256(str(media_id))` 既消碰撞，又让新单视频请求 `(media_ids=[x])` 的 hash 与旧单视频任务对齐可复用。

## File Structure

| 文件 | 职责 | 新建/修改 |
|------|------|-----------|
| `src/videomind/core/agent_loop/types.py` | 扩展 `VideoMeta`/`AgentState`/`SubTask.search_query`/`Evidence` 对齐 RAG + 媒体来源 | 修改 |
| `src/videomind/core/agent_loop/planner.py` | PLAN prompt 注入视频文件名列表 + 解析 `search_query` | 修改 |
| `src/videomind/core/agent_loop/executor.py` | 重写：retriever Protocol + `_RagRetriever` + 跨视频检索 + 幻觉过滤 | 修改 |
| `src/videomind/core/agent_loop/critic.py` | 删 `verify_all(duration_ms=0)`，改读 `retrieved_evidence_ids` 真实命中校验 | 修改 |
| `src/videomind/core/agent_loop/loop.py` | `run(goal, max_rounds=2)` 去 `MAX_ROUNDS=2` 硬编码 | 修改 |
| `src/videomind/core/agent_loop/factory.py` | `get_agent_loop(retriever=None)` 让 Executor 可注入 retriever | 修改 |
| `src/videomind/infrastructure/storage/models.py` | `AnalysisTask.media_ids_hash` + 换唯一约束 + 新 `AnalysisTaskMedia` | 修改 |
| `alembic/versions/2026_08_01_<rev>_multi_video_agent.py` | 加列 + 回填 + 换约束 + 建关联表 | 新建 |
| `src/videomind/interface/routes/agent.py` | `AnalyzeRequest.media_ids` + 多视频校验 + 关联表写入 + 幂等 + `_run_agent_loop` 多视频签名 + checkpoint `video_context_ref` | 修改 |
| `tests/core/agent_loop/test_executor.py` | 既有 executor 测试适配新行为（提供 mock retriever） | 修改 |
| `tests/core/agent_loop/test_executor_multi_video.py` | 多视频检索/来源标注/幻觉过滤/零命中 | 新建 |
| `tests/core/agent_loop/test_critic.py` | 既有 perfect-result 测试补 `retrieved_evidence_ids` + 真实 evidence | 修改 |
| `tests/core/agent_loop/test_critic_real_evidence.py` | 真实命中校验三场景 | 新建 |
| `tests/core/agent_loop/test_loop.py` | 补 `max_rounds=1` 测试 | 修改 |
| `tests/migration/test_multi_video_migration.py` | 升级后表/列/约束/索引存在 | 新建 |
| `tests/interface/test_agent_analyze_multi.py` | httpx ASGITransport + patch `_run_agent_loop`：建任务/关联/幂等/乱序/404/409/422 | 新建 |
| `frontend/src/lib/api.ts` | `analysisApi.create` 改 `media_ids: string[]` | 修改 |
| `frontend/src/features/agent-analysis/AgentAnalysisPage.tsx` | 单选→多选折叠面板（照搬 RAGChat）+ 证据渲染新字段 | 修改 |
| `frontend/src/i18n/zh-CN.json` / `en-US.json` | 新 i18n 键 + 补缺失键 | 修改 |

---

### Task 1: 扩展 agent_loop 数据结构（types.py）

**Files:**
- Modify: `src/videomind/core/agent_loop/types.py`
- Test: `tests/core/agent_loop/test_types_multi_video.py`（新建）

**Interfaces:**
- Produces: `VideoMeta(media_id, filename, duration_ms)`；`AgentState` 新增 `media_ids: list[str]` / `video_meta: list[VideoMeta]` / `retrieved_evidence_ids: set[str]`；`SubTask.search_query: str = ""`；`Evidence` 新增 `chunk_id/content/source_type/score/start_ms/end_ms/media_id/media_title`（旧 `timestamp_ms`/`source` 带默认保留）。下游 Task 2/3/4 依赖这些字段名。

- [ ] **Step 1: Write the failing test**

`tests/core/agent_loop/test_types_multi_video.py`：
```python
"""Multi-video agent_loop 数据结构扩展测试."""

from __future__ import annotations

from videomind.core.agent_loop import types as t  # noqa: F401  触发导入


def test_video_meta_defaults() -> None:
    vm = t.VideoMeta(media_id="m1", filename="a.mp4", duration_ms=1000)
    assert vm.media_id == "m1" and vm.filename == "a.mp4" and vm.duration_ms == 1000
    vm2 = t.VideoMeta(media_id="m2", filename="b.mp4")
    assert vm2.duration_ms is None


def test_agent_state_multi_video_fields_default_empty() -> None:
    state = t.AgentState(goal="g")
    assert state.media_ids == []
    assert state.video_meta == []
    assert state.retrieved_evidence_ids == set()
    assert state.trace_id  # 仍有默认 uuid4().hex


def test_subtask_search_query_default_empty() -> None:
    st = t.SubTask(id="t1", description="d", required_evidence_type="text")
    assert st.search_query == ""


def test_evidence_new_fields_default_and_legacy_backcompat() -> None:
    ev = t.Evidence(id="EID_1")
    assert ev.chunk_id == "" and ev.content == ""
    assert ev.source_type == "" and ev.score == 0.0
    assert ev.start_ms is None and ev.end_ms is None
    assert ev.media_id == "" and ev.media_title == ""
    # 旧字段保留默认，兼容 verifier 旧测试
    assert ev.timestamp_ms == 0 and ev.source == "text"
    # 旧 keyword 构造仍工作（test_verifier.py 依赖）
    ev2 = t.Evidence(id="EID_01", timestamp_ms=10000, source="audio",
                    content="x", chunk_id="c1")
    assert ev2.timestamp_ms == 10000 and ev2.source == "audio" and ev2.content == "x" and ev2.chunk_id == "c1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/d/ProgramData/anaconda3/python.exe -m pytest tests/core/agent_loop/test_types_multi_video.py -v`
Expected: FAIL（`AttributeError: module ... has no attribute 'VideoMeta'` / `AgentState has no media_ids`）

- [ ] **Step 3: Write minimal implementation**

替换 `src/videomind/core/agent_loop/types.py` 中 `SubTask`、`Evidence`、`AgentState` 三个 dataclass 并新增 `VideoMeta`，更新 `__all__`：

```python
@dataclass
class SubTask:
    """单个子任务。"""

    id: str
    description: str
    required_evidence_type: Literal["frame", "text", "audio", "sql"]
    time_range_hint: tuple[int, int] | None = None
    search_query: str = ""  # 检索友好查询词；为空则 Executor 退化用 description


@dataclass
class VideoMeta:
    """单个目标视频元信息（注入 Planner/Executor 上下文）。"""

    media_id: str
    filename: str
    duration_ms: int | None = None


@dataclass
class Evidence:
    """证据条目（承载真实 RAG 检索结果 + 来源视频）。

    新流程使用 chunk_id/content/source_type/score/start_ms/end_ms/media_id/media_title。
    timestamp_ms/source 为退役字段（保留默认值以兼容旧 verifier 测试），新代码不写入/不渲染。
    """

    id: str
    chunk_id: str = ""
    content: str = ""
    source_type: str = ""                 # asr | ocr | mixed（来自 RAG）
    score: float = 0.0
    start_ms: int | None = None
    end_ms: int | None = None
    media_id: str = ""                    # 来源视频 UUID 字符串
    media_title: str = ""                 # 来源视频文件名（前端直渲染）
    # 退役字段（保留默认，兼容旧 verifier 测试；新流程不写入）
    timestamp_ms: int = 0
    source: Literal["frame", "text", "audio", "sql"] = "text"


@dataclass
class AgentState:
    """代理状态总容器。"""

    goal: str
    media_ids: list[str] = field(default_factory=list)
    video_meta: list[VideoMeta] = field(default_factory=list)
    retrieved_evidence_ids: set[str] = field(default_factory=set)  # Executor 填，Critic 读
    plan: AgentPlan | None = None
    result: AnalysisResult | None = None
    critique: CriticResult | None = None
    round: int = 0
    trace_id: str = field(default_factory=lambda: uuid4().hex)
```

`__all__` 增加 `"VideoMeta"`（其余 `AnalysisResult/Conclusion/CriticResult/AgentPlan` 不变）。

- [ ] **Step 4: Run test to verify it passes**

Run: `/d/ProgramData/anaconda3/python.exe -m pytest tests/core/agent_loop/test_types_multi_video.py tests/core/agent_loop/test_verifier.py -v`
Expected: PASS（新测试 + 旧 verifier 测试都过——旧测试 `Evidence(id=..., timestamp_ms=..., source=..., content=..., chunk_id=...)` 仍工作）

- [ ] **Step 5: Commit**

```bash
git add src/videomind/core/agent_loop/types.py tests/core/agent_loop/test_types_multi_video.py
git commit -m "feat(agent-loop): extend dataclasses for multi-video context (VideoMeta/Evidence/SubTask/AgentState)"
```

---

### Task 2: Planner 注入视频列表 + 解析 search_query

**Files:**
- Modify: `src/videomind/core/agent_loop/planner.py`
- Test: `tests/core/agent_loop/test_planner.py`（既存；新增 case）

**Interfaces:**
- Consumes: Task 1 的 `AgentState.media_ids`/`video_meta`、`SubTask.search_query`
- Produces: `Planner.plan(state)` 在 prompt 含视频文件名清单，`SubTask.search_query` 从 LLM JSON 的 `search_query` 字段解析

- [ ] **Step 1: Write the failing test**

追加到 `tests/core/agent_loop/test_planner.py`（若文件不存在则新建，含既有 import idiom `AsyncMock` + `json`）：

```python
@pytest.mark.asyncio
async def test_planner_extracts_search_query_and_reads_video_list() -> None:
    """Planner 把视频文件名塞进 prompt，并解析每子任务的 search_query。"""
    from videomind.core.agent_loop.planner import Planner
    from videomind.core.agent_loop.types import AgentState, VideoMeta

    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock()
    mock_llm.chat.return_value.content = json.dumps({
        "tasks": [{"description": "找商业模式", "evidence_type": "text",
                   "search_query": "商业模式 价格 斜率"}],
        "reasoning": "拆一个子任务",
    })
    planner = Planner(mock_llm)
    state = AgentState(
        goal="分析视频",
        media_ids=["m1", "m2"],
        video_meta=[VideoMeta(media_id="m1", filename="a.mp4"),
                    VideoMeta(media_id="m2", filename="b.mp4")],
    )
    plan = await planner.plan(state)

    assert plan.tasks[0].search_query == "商业模式 价格 斜率"
    # prompt 里含两个视频文件名
    sent = mock_llm.chat.call_args.args[0]
    sent_text = sent.messages[0]["content"]
    assert "a.mp4" in sent_text and "b.mp4" in sent_text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/d/ProgramData/anaconda3/python.exe -m pytest tests/core/agent_loop/test_planner.py::test_planner_extracts_search_query_and_reads_video_list -v`
Expected: FAIL（`search_query` 解析为空 / prompt 不含文件名）

- [ ] **Step 3: Write minimal implementation**

`src/videomind/core/agent_loop/planner.py`：改 `SYSTEM_PROMPT` 加 `search_query` 说明；`plan()` 构造 prompt 时拼视频文件名清单；parse 时增 `search_query`：

```python
SYSTEM_PROMPT = (
    "你是视频分析任务规划师。将给定的分析目标拆解为 1-5 个可执行子任务。"
    "每个子任务需指定所需证据类型（frame / text / audio / sql）。"
    "如涉及特定时间段，提供 time_range（毫秒）起止值。"
    "每个子任务还需提供 search_query：面向检索的关键词（如\"商业模式 价格 斜率\"），"
    "而非完整问句——它将直接送入向量检索。"
    "输出 JSON 格式："
    '{"tasks": [{"description": "...", "evidence_type": "...", '
    '"time_range": [start_ms, end_ms], "search_query": "..."}], "reasoning": "..."}'
)
```

`plan()` 内：
```python
        video_names = [vm.filename for vm in state.video_meta]
        video_section = (
            f"\n可用视频（共 {len(video_names)} 个）：{', '.join(video_names)}"
            if video_names else "\n可用视频：未提供"
        )
        prompt = f"{SYSTEM_PROMPT}\n\n用户目标：{state.goal}{video_section}"
```

构造 `SubTask` 时加 `search_query=task.get("search_query", "")`。

- [ ] **Step 4: Run test to verify it passes**

Run: `/d/ProgramData/anaconda3/python.exe -m pytest tests/core/agent_loop/test_planner.py -v`
Expected: PASS（新加 case + 既有 planner 测试都过——既有测试不依赖 `search_query`，解析缺字段时默认 `""`）

- [ ] **Step 5: Commit**

```bash
git add src/videomind/core/agent_loop/planner.py tests/core/agent_loop/test_planner.py
git commit -m "feat(planner): emit search_query + feed video filename list into plan prompt"
```

---

### Task 3: Executor 重写接 RAG + retriever 注入

**Files:**
- Modify: `src/videomind/core/agent_loop/executor.py`
- Modify: `tests/core/agent_loop/test_executor.py`（既有 `test_executor_generates_analysis_result` 适配）
- Test: `tests/core/agent_loop/test_executor_multi_video.py`（新建）

**Interfaces:**
- Consumes: Task 1 `Evidence`/`AgentState`/`VideoMeta`；`videomind.core.rag.pipeline.search(db, query, media_id, *, top_k) -> {evidence: [RAG Evidence(id,chunk_id,content,score,start_ms,end_ms,source_type)]}`
- Produces: `RetrieverProtocol.search(query, media_id: uuid.UUID, *, top_k) -> list[dict]`（dict 含 id/chunk_id/content/source_type/score/start_ms/end_ms）；`_RagRetriever`（默认实现，内部开 `AsyncSessionLocal`）；`Executor(llm, retriever=None)`；执行后 `state.retrieved_evidence_ids` 被填充；`AnalysisResult.evidence` 每条带 `media_id`/`media_title`；幻觉引用 `evidence_ids ∉ 检索集` 的 conclusion 被过滤。

- [ ] **Step 1: Write the failing tests**

新建 `tests/core/agent_loop/test_executor_multi_video.py`：
```python
"""Executor 跨多视频真实检索 + 来源标注 + 幻觉过滤 + 零命中 测试."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

UUID_A = "00000000-0000-0000-0000-000000000001"
UUID_B = "00000000-0000-0000-0000-000000000002"


def _hit(eid: str, content: str, source_type: str = "asr") -> dict:
    return {"id": eid, "chunk_id": "c_" + eid, "content": content,
            "source_type": source_type, "score": 0.9,
            "start_ms": 0, "end_ms": 1000}


def _state(media_ids, videos, plan_tasks):
    from videomind.core.agent_loop.types import AgentState, AgentPlan, VideoMeta, SubTask
    return AgentState(
        goal="g",
        media_ids=media_ids,
        video_meta=[VideoMeta(media_id=mid, filename=name) for mid, name in videos],
        plan=AgentPlan(tasks=[SubTask(id="t1", description="d",
                                      required_evidence_type="text", search_query="q")
                              for _ in range(plan_tasks)], reasoning="r"),
    )


@pytest.mark.asyncio
async def test_executor_cross_video_retrieval_tags_media_title() -> None:
    from videomind.core.agent_loop.executor import Executor

    mock_llm = AsyncMock(); mock_llm.chat = AsyncMock()
    mock_llm.chat.return_value.content = json.dumps({
        "title": "T",
        "conclusions": [{"point": "p", "evidence_ids": ["EID_a1"], "confidence": 0.8}],
        "suggestions": [],
    })
    mock_retriever = AsyncMock()
    mock_retriever.search = AsyncMock(side_effect=[
        [_hit("EID_a1", "a1", "asr")],
        [_hit("EID_b1", "b1", "ocr")],
    ])
    executor = Executor(mock_llm, retriever=mock_retriever)
    state = _state([UUID_A, UUID_B], [(UUID_A, "a.mp4"), (UUID_B, "b.mp4")], 1)
    result = await executor.execute(state)

    assert len(result.evidence) == 2
    assert {e.media_title for e in result.evidence} == {"a.mp4", "b.mp4"}
    assert {e.media_id for e in result.evidence} == {UUID_A, UUID_B}
    assert result.conclusions[0].evidence_ids == ["EID_a1"]
    assert state.retrieved_evidence_ids == {"EID_a1", "EID_b1"}


@pytest.mark.asyncio
async def test_executor_filters_hallucinated_eids() -> None:
    from videomind.core.agent_loop.executor import Executor

    mock_llm = AsyncMock(); mock_llm.chat = AsyncMock()
    mock_llm.chat.return_value.content = json.dumps({
        "title": "T",
        "conclusions": [
            {"point": "real", "evidence_ids": ["EID_a1"], "confidence": 0.8},
            {"point": "fake", "evidence_ids": ["EID_FAKE_99"], "confidence": 0.5},
        ],
        "suggestions": [],
    })
    mock_retriever = AsyncMock()
    mock_retriever.search = AsyncMock(return_value=[_hit("EID_a1", "a1")])
    executor = Executor(mock_llm, retriever=mock_retriever)
    state = _state([UUID_A], [(UUID_A, "a.mp4")], 1)
    result = await executor.execute(state)

    assert len(result.conclusions) == 1
    assert result.conclusions[0].point == "real"


@pytest.mark.asyncio
async def test_executor_zero_hits_does_not_crash() -> None:
    from videomind.core.agent_loop.executor import Executor

    mock_retriever = AsyncMock()
    mock_retriever.search = AsyncMock(return_value=[])
    executor = Executor(AsyncMock().chat and AsyncMock(), retriever=mock_retriever)
    # chat 不设 return_value -> MagicMock -> json.loads 抛错 -> continue
    executor._llm = AsyncMock(); executor._llm.chat = AsyncMock()
    state = _state([UUID_A], [(UUID_A, "a.mp4")], 1)
    result = await executor.execute(state)

    assert len(result.evidence) == 0
    assert len(result.conclusions) == 0
```

修改 `tests/core/agent_loop/test_executor.py` 的 `test_executor_generates_analysis_result`（新 Executor 必须先真实检索才能产出命中 conclusion）：
```python
@pytest.mark.asyncio
async def test_executor_generates_analysis_result() -> None:
    """Executor 调用 LLM 并正确生成 AnalysisResult（命中真实检索证据）。"""
    from videomind.core.agent_loop.executor import Executor
    from videomind.core.agent_loop.types import AgentState, AgentPlan, SubTask, VideoMeta

    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock()
    mock_llm.chat.return_value.content = json.dumps({
        "title": "视频分析结果",
        "conclusions": [
            {"point": "视频主题是AI技术", "evidence_ids": ["EID_abc12345_01"], "confidence": 0.9},
            {"point": "主讲人介绍了三个要点", "evidence_ids": ["EID_abc12345_02"], "confidence": 0.85},
        ],
        "suggestions": ["想要展开第一点", "可以对比其他观点"],
    })
    mock_retriever = AsyncMock()
    mock_retriever.search = AsyncMock(return_value=[
        {"id": "EID_abc12345_01", "chunk_id": "c1", "content": "内容1", "source_type": "asr", "score": 0.9, "start_ms": 0, "end_ms": 1000},
        {"id": "EID_abc12345_02", "chunk_id": "c2", "content": "内容2", "source_type": "ocr", "score": 0.8, "start_ms": 0, "end_ms": 1000},
    ])
    MID = "00000000-0000-0000-0000-000000000001"
    executor = Executor(mock_llm, retriever=mock_retriever)
    state = AgentState(
        goal="分析视频内容",
        media_ids=[MID],
        video_meta=[VideoMeta(media_id=MID, filename="a.mp4")],
        plan=AgentPlan(tasks=[SubTask(id="task_1", description="提取主题",
                                      required_evidence_type="text")], reasoning="一个任务"),
    )
    result = await executor.execute(state)

    assert result.title == "视频分析结果"
    assert len(result.conclusions) == 2
    assert result.conclusions[0].confidence == 0.9
    assert len(result.suggestions) == 2
```
（`test_executor_dedup_suggestions`、`test_executor_malformed_json_graceful` 不动——它们 `media_ids=[]` → 跳检索循环，behavior 不变。）

- [ ] **Step 2: Run tests to verify they fail**

Run: `/d/ProgramData/anaconda3/python.exe -m pytest tests/core/agent_loop/test_executor_multi_video.py tests/core/agent_loop/test_executor.py -v`
Expected: FAIL（`Executor` 不接受 `retriever` 参数 / `media_title` 缺失 / 幻觉 conclusion 未被过滤）

- [ ] **Step 3: Write minimal implementation**

替换 `src/videomind/core/agent_loop/executor.py` 全文为：

```python
"""证据执行器 —— 跨视频真实 RAG 检索 + LLM 结论聚合 + 幻觉过滤."""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

import structlog

from videomind.core.agent_loop.types import (
    AgentState, AnalysisResult, Conclusion, Evidence,
)

if TYPE_CHECKING:
    from typing import Protocol

    class LLMProtocol(Protocol):
        async def chat(self, request: Any) -> Any: ...

    class RetrieverProtocol(Protocol):
        """检索器最小接口：单视频查询返回命中原始片段 dict 列表."""

        async def search(self, query: str, media_id: uuid.UUID, *, top_k: int) -> list[dict]: ...

logger = structlog.get_logger(__name__)
TOP_K_PER_VIDEO = 5
MAX_SUGGESTIONS = 5


class _RagRetriever:
    """默认检索器：复用 rag_pipeline.search，开自有 AsyncSession。"""

    async def search(self, query: str, media_id: uuid.UUID, *, top_k: int = TOP_K_PER_VIDEO) -> list[dict]:
        from videomind.core.rag.pipeline import search as rag_search
        from videomind.infrastructure.storage.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            res = await rag_search(db, query, media_id, top_k=top_k)
        return [
            {"id": e.id, "chunk_id": e.chunk_id, "content": e.content,
             "source_type": e.source_type, "score": e.score,
             "start_ms": e.start_ms, "end_ms": e.end_ms}
            for e in res["evidence"]
        ]


class Executor:
    """跨视频检索真实证据，喂 LLM 产结论，再过滤幻觉引用。

    Args:
        llm: LLM 客户端（``async chat(request)``）。
        retriever: 检索器；None 时用默认 ``_RagRetriever``。测试可注入 mock。
    """

    def __init__(self, llm: Any, retriever: "RetrieverProtocol | None" = None) -> None:
        self._llm = llm
        self._retriever = retriever if retriever is not None else _RagRetriever()

    async def execute(self, state: AgentState) -> AnalysisResult:
        plan = state.plan
        if plan is None:
            logger.info("Executor 收到空 plan，返回空 AnalysisResult")
            return AnalysisResult(title="")

        meta_by_id = {vm.media_id: vm.filename for vm in state.video_meta}
        all_evidence: dict[str, Evidence] = {}
        all_conclusions: list[Conclusion] = []
        all_suggestions: list[str] = []
        title = ""

        for task in plan.tasks:
            query = task.search_query or task.description
            round_hits: list[Evidence] = []
            for mid in state.media_ids:
                try:
                    hits = await self._retriever.search(query, uuid.UUID(mid), top_k=TOP_K_PER_VIDEO)
                except Exception:
                    logger.warning("Executor 检索失败 media=%s", mid, exc_info=True)
                    continue
                for h in hits:
                    ev = Evidence(
                        id=h["id"],
                        chunk_id=h.get("chunk_id", ""),
                        content=h.get("content", ""),
                        source_type=h.get("source_type", ""),
                        score=float(h.get("score", 0.0)),
                        start_ms=h.get("start_ms"),
                        end_ms=h.get("end_ms"),
                        media_id=mid,
                        media_title=meta_by_id.get(mid, ""),
                    )
                    all_evidence[ev.id] = ev
                    round_hits.append(ev)

            context_block = "\n".join(
                f"[{ev.id}] (来源: {ev.media_title}) {ev.content}" for ev in round_hits
            )
            prompt = self._build_prompt(task, context_block)
            try:
                resp = await self._llm.chat(
                    type("ChatRequest", (), {
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.1,
                    })()
                )
                data = json.loads(resp.content)
            except Exception:
                logger.warning("Executor LLM 失败 task=%s", task.id, exc_info=True)
                continue

            if not title:
                title = data.get("title", "")
            for c in data.get("conclusions", []):
                all_conclusions.append(Conclusion(
                    point=c.get("point", ""),
                    evidence_ids=c.get("evidence_ids", []),
                    confidence=float(c.get("confidence", 0.5)),
                ))
            all_suggestions.extend(data.get("suggestions", []))

        state.retrieved_evidence_ids = set(all_evidence.keys())

        # 幻觉过滤：仅保留 evidence_ids 全部命中真实检索集的 conclusion
        real_ids = set(all_evidence.keys())
        final_conclusions = [c for c in all_conclusions if set(c.evidence_ids) <= real_ids]

        if not title:
            title = state.goal[:64] if state.goal else (
                plan.tasks[0].description[:30] if plan.tasks else "分析结果")

        return AnalysisResult(
            title=title,
            conclusions=final_conclusions,
            evidence=list(all_evidence.values()),
            suggestions=list(dict.fromkeys(all_suggestions))[:MAX_SUGGESTIONS],
        )

    @staticmethod
    def _build_prompt(task: Any, context_block: str) -> str:
        return (
            "你只能基于以下真实检索到的证据回答，严禁编造新的证据 ID。\n"
            f"子任务：{task.description}\n\n"
            f"证据上下文：\n{context_block or '(无命中证据)'}\n\n"
            '返回 JSON：{"title": "...", '
            '"conclusions": [{"point": "...", "evidence_ids": ["EID_..."], "confidence": 0.0}], '
            '"suggestions": ["..."]}'
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/d/ProgramData/anaconda3/python.exe -m pytest tests/core/agent_loop/test_executor.py tests/core/agent_loop/test_executor_multi_video.py -v`
Expected: PASS（4 个测试全过）

- [ ] **Step 5: Commit**

```bash
git add src/videomind/core/agent_loop/executor.py tests/core/agent_loop/test_executor.py tests/core/agent_loop/test_executor_multi_video.py
git commit -m "feat(executor): real cross-video RAG retrieval + retriever injection + hallucination filter"
```

---

### Task 4: Critic 改真实命中校验

**Files:**
- Modify: `src/videomind/core/agent_loop/critic.py`
- Modify: `tests/core/agent_loop/test_critic.py`（既有 3 个测试补 `retrieved_evidence_ids` + 真实 evidence，使其语义诚实）
- Test: `tests/core/agent_loop/test_critic_real_evidence.py`（新建）

**Interfaces:**
- Consumes: Task 1 `AgentState.retrieved_evidence_ids: set[str]`；既有 `CriticResult.passed/feedback/coverage_score/structure_ok/evidence_verified/hallucination_risk` 保留
- Produces: `Critic.critique(state)` 不再调 `verify_all`；硬校验 = `(result.evidence 每条 id ∈ retrieved_evidence_ids) AND (each conclusion.evidence_ids ⊆ retrieved_evidence_ids)`；`passed = llm_passed AND hard_passed`；`evidence_verified = hard_passed`。空 `retrieved_evidence_ids` + 空 `evidence` → 硬校验空真通过（既有空 result 测试语义不变）。

- [ ] **Step 1: Write the failing tests**

新建 `tests/core/agent_loop/test_critic_real_evidence.py`：
```python
"""Critic 真实命中校验测试."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from videomind.core.agent_loop.types import (
    AgentState, AnalysisResult, Conclusion, Evidence,
)


def _llm(passed: bool) -> AsyncMock:
    m = AsyncMock(); m.chat = AsyncMock()
    m.chat.return_value.content = json.dumps({
        "passed": passed, "feedback": "f", "required_timestamps": [],
        "coverage_score": 0.9, "structure_ok": True,
        "evidence_verified": True, "hallucination_risk": 0.1,
    })
    return m


@pytest.mark.asyncio
async def test_critic_hard_pass_when_all_hits_real() -> None:
    from videomind.core.agent_loop.critic import Critic
    from videomind.core.agent_loop.verifier import EvidenceVerifier

    ev = [Evidence(id="EID_a1", chunk_id="c1", content="x", media_id="m", media_title="a.mp4")]
    state = AgentState(
        goal="g", retrieved_evidence_ids={"EID_a1"},
        result=AnalysisResult(title="T", evidence=ev,
            conclusions=[Conclusion(point="p", evidence_ids=["EID_a1"], confidence=0.8)]),
    )
    critic = Critic(_llm(True), EvidenceVerifier())
    r = await critic.critique(state)
    assert r.passed is True and r.evidence_verified is True


@pytest.mark.asyncio
async def test_critic_hard_fails_when_conclusion_cites_fake_eid() -> None:
    from videomind.core.agent_loop.critic import Critic
    from videomind.core.agent_loop.verifier import EvidenceVerifier

    ev = [Evidence(id="EID_a1", chunk_id="c1", content="x")]
    state = AgentState(
        goal="g", retrieved_evidence_ids={"EID_a1"},
        result=AnalysisResult(title="T", evidence=ev,
            conclusions=[Conclusion(point="p", evidence_ids=["EID_FAKE_99"])]),
    )
    critic = Critic(_llm(True), EvidenceVerifier())  # LLM 说过，但硬校验应拦
    r = await critic.critique(state)
    assert r.passed is False and r.evidence_verified is False


@pytest.mark.asyncio
async def test_critic_llm_fail_short_circuits() -> None:
    from videomind.core.agent_loop.critic import Critic
    from videomind.core.agent_loop.verifier import EvidenceVerifier

    state = AgentState(goal="g", retrieved_evidence_ids={"EID_a1"},
        result=AnalysisResult(title="T",
            evidence=[Evidence(id="EID_a1")],
            conclusions=[Conclusion(point="p", evidence_ids=["EID_a1"])]))
    critic = Critic(_llm(False), EvidenceVerifier())
    r = await critic.critique(state)
    assert r.passed is False
```

修改 `tests/core/agent_loop/test_critic.py` 的 `test_critic_passes_with_perfect_result`，让 result 用真实 evidence + `retrieved_evidence_ids`：
```python
@pytest.mark.asyncio
async def test_critic_passes_with_perfect_result() -> None:
    """LLM 返回高质量评审结果，且真实命中校验通过。"""
    from videomind.core.agent_loop.critic import Critic
    from videomind.core.agent_loop.verifier import EvidenceVerifier
    from videomind.core.agent_loop.types import Conclusion, Evidence

    mock_llm = AsyncMock()
    mock_llm.chat = AsyncMock()
    mock_llm.chat.return_value.content = json.dumps({
        "passed": True, "feedback": "很好", "required_timestamps": [],
        "coverage_score": 0.95, "structure_ok": True,
        "evidence_verified": True, "hallucination_risk": 0.05,
    })
    critic = Critic(mock_llm, EvidenceVerifier())
    state = AgentState(
        goal="分析视频",
        retrieved_evidence_ids={"EID_real"},
        result=AnalysisResult(title="T",
            evidence=[Evidence(id="EID_real", chunk_id="c1", content="x")],
            conclusions=[Conclusion(point="ok", evidence_ids=["EID_real"])]),
    )
    result = await critic.critique(state)
    assert result.passed is True
    assert result.coverage_score == 0.95
    assert result.evidence_verified is True
```
（`test_critic_fails_with_low_coverage`、`test_critic_LLM_error_graceful`：`retrieved_evidence_ids` 缺省为空 set，evidence 也为空 → 硬校验空真通过 → 两测试仍按原意由 LLM 失败驱动 `passed=False`，无需改。）

- [ ] **Step 2: Run tests to verify they fail**

Run: `/d/ProgramData/anaconda3/python.exe -m pytest tests/core/agent_loop/test_critic_real_evidence.py tests/core/agent_loop/test_critic.py -v`
Expected: FAIL（`test_critic_hard_fails_when_conclusion_cites_fake_eid` — 当前 Critic 用 `verify_all(duration_ms=0)` 且未读 `retrieved_evidence_ids`，过不了；新测试 `evidence_verified` 期望 `False` 但旧逻辑随 LLM 返回 True）

- [ ] **Step 3: Write minimal implementation**

替换 `src/videomind/core/agent_loop/critic.py` 的步骤 2/3（保留 `_verifier` 参数与文件头注释，去掉 `verify_all` 调用；逐步骤下移增硬校验）：

```python
        # 步骤 2: 硬性真实命中校验（替换旧的 timestamp 范围校验）
        retrieved = state.retrieved_evidence_ids
        ev_ids = {e.id for e in result.evidence}
        evidence_real = ev_ids <= retrieved
        conclusions_real = all(set(c.evidence_ids) <= retrieved for c in result.conclusions)
        hard_passed = evidence_real and conclusions_real

        # 步骤 3: 综合结果
        llm_passed = data.get("passed", False)
        return CriticResult(
            passed=llm_passed and hard_passed,
            feedback=data.get("feedback", ""),
            required_timestamps=data.get("required_timestamps", []),
            coverage_score=data.get("coverage_score", 0.0),
            structure_ok=data.get("structure_ok", False),
            evidence_verified=hard_passed,
            hallucination_risk=data.get("hallucination_risk", 0.5),
        )
```
保留 `__init__(self, llm, verifier)`、LLM 调用块与错误降级块不变（`verifier` 参数保留以兼容 factory，但不再调用 `self._verifier.verify_all`）。`EvidenceVerifier` import 可保留（factory 仍传它），或保留 import 以减少 diff——保留。

- [ ] **Step 4: Run tests to verify they pass**

Run: `/d/ProgramData/anaconda3/python.exe -m pytest tests/core/agent_loop/test_critic.py tests/core/agent_loop/test_critic_real_evidence.py -v`
Expected: PASS（6 个测试全过）

- [ ] **Step 5: Commit**

```bash
git add src/videomind/core/agent_loop/critic.py tests/core/agent_loop/test_critic.py tests/core/agent_loop/test_critic_real_evidence.py
git commit -m "fix(critic): real hit-set validation via retrieved_evidence_ids (drop timestamp-range verify_all)"
```

---

### Task 5: loop.py 去 MAX_ROUNDS 硬编码 + factory 注入 retriever

**Files:**
- Modify: `src/videomind/core/agent_loop/loop.py`
- Modify: `src/videomind/core/agent_loop/factory.py`
- Modify: `tests/core/agent_loop/test_loop.py`（补 `max_rounds=1`）

**Interfaces:**
- Consumes: `AgentLoop.__init__(planner, executor, critic)`（不变）
- Produces: `AgentLoop.run(goal: str, max_rounds: int = 2) -> AnalysisResult`；`get_agent_loop(retriever=None) -> AgentLoop`（`retriever` 透传给 `Executor`）

- [ ] **Step 1: Write the failing test**

追加到 `tests/core/agent_loop/test_loop.py`：
```python
@pytest.mark.asyncio
async def test_agent_loop_respects_max_rounds_one() -> None:
    """max_rounds=1 时，即便 Critic 不通过也只跑 1 轮."""
    from videomind.core.agent_loop.loop import AgentLoop

    mock_planner = AsyncMock(); mock_planner.plan = AsyncMock(return_value=AgentPlan(
        tasks=[SubTask(id="t1", description="d", required_evidence_type="text")], reasoning="r"))
    mock_executor = AsyncMock(); mock_executor.execute = AsyncMock(return_value=AnalysisResult(
        title="F", conclusions=[], evidence=[], suggestions=[]))
    mock_critic = AsyncMock(); mock_critic.critique = AsyncMock(return_value=CriticResult(
        passed=False, feedback="no", coverage_score=0.3))

    loop = AgentLoop(mock_planner, mock_executor, mock_critic)
    await loop.run("goal", max_rounds=1)

    assert mock_planner.plan.call_count == 1
    assert mock_critic.critique.call_count == 1
```
（既存 `test_agent_loop_two_rounds_max` 调 `loop.run("goal")` 走默认 `max_rounds=2`，仍断言 2 轮——不受影响。）

- [ ] **Step 2: Run test to verify it fails**

Run: `/d/ProgramData/anaconda3/python.exe -m pytest tests/core/agent_loop/test_loop.py::test_agent_loop_respects_max_rounds_one -v`
Expected: FAIL（`run` 当前签名 `run(self, goal: str)`，传 `max_rounds=1` 报 `TypeError`）

- [ ] **Step 3: Write minimal implementation**

`src/videomind/core/agent_loop/loop.py`：删 `MAX_ROUNDS = 2` 常量（或保留但不再用），改 `run`：
```python
    async def run(self, goal: str, max_rounds: int = 2) -> AnalysisResult:
        state = AgentState(goal=goal)
        for round_num in range(1, max_rounds + 1):
            state.round = round_num
            state.plan = await self._planner.plan(state)
            state.result = await self._executor.execute(state)
            state.critique = await self._critic.critique(state)
            if state.critique.passed:
                break
        return state.result if state.result else AnalysisResult(title="")
```
（模块头注释 `(<=2 轮)` 改为 `(<=max_rounds 轮)`。）

`src/videomind/core/agent_loop/factory.py`：
```python
@lru_cache
def get_agent_loop(retriever=None) -> AgentLoop:
    """装配并返回 AgentLoop 单例。

    Args:
        retriever: 检索器实例；None 时 Executor 用默认 _RagRetriever。
                   测试可传 mock retriever。
    """
    llm = get_llm_service()
    return AgentLoop(
        planner=Planner(llm),
        executor=Executor(llm, retriever=retriever),
        critic=Critic(llm, EvidenceVerifier()),
    )
```
> **lru_cache 注意**：`lru_cache` 不要求参数 hashable（用 kwargs 不会报错）。生产路径 `_run_agent_loop` 调 `get_agent_loop()`（无参，命中缓存单例）；只有测试可能传 mock retriever 并随后 `cache_clear()`。无破坏。

- [ ] **Step 4: Run tests to verify they pass**

Run: `/d/ProgramData/anaconda3/python.exe -m pytest tests/core/agent_loop/test_loop.py -v`
Expected: PASS（3 个测试：单轮通过、两轮 max、max_rounds=1；单例测试因默认无参调用仍过）

- [ ] **Step 5: Commit**

```bash
git add src/videomind/core/agent_loop/loop.py src/videomind/core/agent_loop/factory.py tests/core/agent_loop/test_loop.py
git commit -m "fix(loop): honor max_rounds param / drop MAX_ROUNDS=2 hardcode; factory injects retriever"
```

---

### Task 6: DB model — AnalysisTask.media_ids_hash + AnalysisTaskMedia 关联表

**Files:**
- Modify: `src/videomind/infrastructure/storage/models.py`
- Test: `tests/core/agent_loop/test_models_multi_video.py`（新建，纯 ORM 构造断言）

**Interfaces:**
- Consumes: `Base`、既有 `AnalysisTask`（line 483-516）列清单
- Produces: `AnalysisTask.media_ids_hash: Mapped[str]`（index）；`__table_args__` 换 `UniqueConstraint("media_ids_hash", "goal_hash", name="uq_at_mediaids_goal")`；新类 `AnalysisTaskMedia(task_id FK CASCADE idx, media_id FK CASCADE idx, position int, created_at, UniqueConstraint("task_id","media_id","uq_atm_task_media"))`。

- [ ] **Step 1: Write the failing test**

新建 `tests/core/agent_loop/test_models_multi_video.py`：
```python
"""AnalysisTask / AnalysisTaskMedia ORM 扩展断言（纯 dataclass 构造，不连 DB）."""

from __future__ import annotations

import uuid
from datetime import datetime


def test_analysis_task_has_media_ids_hash_field() -> None:
    from videomind.infrastructure.storage.models import AnalysisTask
    cols = {c.name for c in AnalysisTask.__table__.columns}
    assert "media_ids_hash" in cols
    assert "media_id" in cols  # primary 仍在
    cons = {c.name for c in AnalysisTask.__table__.constraints}
    assert "uq_at_mediaids_goal" in cons
    assert "uq_at_media_goal" not in cons


def test_analysis_task_media_model_shape() -> None:
    from videomind.infrastructure.storage.models import AnalysisTaskMedia
    cols = {c.name for c in AnalysisTaskMedia.__table__.columns}
    assert cols >= {"task_id", "media_id", "position", "created_at"}
    t = AnalysisTaskMedia(task_id=uuid.uuid4(), media_id=uuid.uuid4(), position=0)
    assert t.position == 0
    cons = {c.name for c in AnalysisTaskMedia.__table__.constraints
            if hasattr(c, "name") and c.name}
    assert "uq_atm_task_media" in cons
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/d/ProgramData/anaconda3/python.exe -m pytest tests/core/agent_loop/test_models_multi_video.py -v`
Expected: FAIL（无 `media_ids_hash` 列 / 无 `AnalysisTaskMedia` 类 / 旧约束名仍在）

- [ ] **Step 3: Write minimal implementation**

`src/videomind/infrastructure/storage/models.py`：

(a) `AnalysisTask` 类内，`goal_hash` 行下方加：
```python
    media_ids_hash: Mapped[str] = mapped_column(String(64), index=True)
    # 多视频幂等键：sha256(sorted media_ids 逗号连接)；单视频 == sha256(str(media_id))。
```
类内 `media_id` 列保留（primary 向后兼容）。

(b) `AnalysisTask.__table_args__` 改为：
```python
    __table_args__ = (
        UniqueConstraint("media_ids_hash", "goal_hash", name="uq_at_mediaids_goal"),
    )
```

(c) 在 `AnalysisTask` 与 `AgentCheckpoint` 之间新增类：
```python
class AnalysisTaskMedia(Base):
    """AnalysisTask ↔ MediaFile 关联表（多视频支持）。

    单条记录表示一个 media 参与一个分析任务；position=0 为 primary media。
    """

    __tablename__ = "analysis_task_media"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_task.id", ondelete="CASCADE"), index=True
    )
    media_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_file.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer)  # 0 = primary
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("task_id", "media_id", name="uq_atm_task_media"),
    )
```
（`Integer`、`UUID`、`ForeignKey`、`UniqueConstraint`、`func`、`text` 已在文件顶部 import；确认 `Text` 同在。）

- [ ] **Step 4: Run test to verify it passes**

Run: `/d/ProgramData/anaconda3/python.exe -m pytest tests/core/agent_loop/test_models_multi_video.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/videomind/infrastructure/storage/models.py tests/core/agent_loop/test_models_multi_video.py
git commit -m "feat(models): add AnalysisTask.media_ids_hash, AnalysisTaskMedia association table"
```

---

### Task 7: Alembic 迁移 — 加列 + 回填 + 换约束 + 建关联表

**Files:**
- Create: `alembic/versions/2026_08_01_<HHMM>_<rev>_multi_video_agent.py`
- Test: `tests/migration/test_multi_video_migration.py`（新建）

**Interfaces:**
- Consumes: Task 6 ORM 形状；`alembic.ini` 的 `file_template`；当前最新 revision `b1a7d2e3f901`
- Produces: `upgrade()`：加 `media_ids_hash` 列（nullable 临时）→ **回填旧行 `media_ids_hash = sha256(str(media_id))`** → 改 NOT NULL+server_default='' → `drop uq_at_media_goal` → `create uq_at_mediaids_goal` → `create ix_analysis_task_media_ids_hash` → `create analysis_task_media` 表 + `uq_atm_task_media` + 两个索引。`downgrade()` 反向。
- **D5 决策（spec §4 vs §风险表 冲突，取安全项）**：回填旧行 `media_ids_hash = sha256(lower(media_id::text))`。spec §4 说"不回填"，但旧列默认 `''` 会让创建 `(media_ids_hash, goal_hash)` 唯一约束在「同 goal 不同 media」旧行上**直接撞约束失败**。回填既消碰撞（每行 hash 唯一），又让新单视频请求 `(media_ids=[x])` 的 `sha256(str(x))` 与旧单视频任务复用（语义对齐 spec 风险表）。

- [ ] **Step 1: 脚手架迁移文件**

Run（在 repo 根）：
```bash
/d/ProgramData/anaconda3/python.exe -m alembic revision -m "multi video agent analysis"
```
生成 `alembic/versions/2026_08_01_<HHMM>_<rev>_multi_video_agent.py`。打开它，确认头：
```python
revision: str = '<生成的 rev>'
down_revision: Union[str, None] = 'b1a7d2e3f901'
```
（若 `down_revision` 不是 `b1a7d2e3f901`，改成它——它是最新。）

- [ ] **Step 2: Write the failing test**

新建 `tests/migration/test_multi_video_migration.py`：
```python
"""multi_video_agent 迁移应用状态断言（依赖 pg_session fixture 升级 head）。"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_migration_added_media_ids_hash_and_association_table(pg_session) -> None:
    """alembic upgrade head 后：analysis_task.media_ids_hash 列 + analysis_task_media 表 + 约束齐."""
    from videomind.infrastructure.storage.database import AsyncSessionLocal
    from sqlalchemy import text

    async with AsyncSessionLocal() as s:
        col = (await s.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='analysis_task' AND column_name='media_ids_hash'"))).all()
        assert len(col) == 1

        tab = (await s.execute(text("SELECT to_regclass('analysis_task_media')"))).scalar()
        assert tab == "analysis_task_media"

        idx = {r[0] for r in (await s.execute(text(
            "SELECT indexname FROM pg_indexes WHERE tablename='analysis_task_media'"))).all()}
        assert "ix_analysis_task_media_task_id" in idx
        assert "ix_analysis_task_media_media_id" in idx
        assert "uq_atm_task_media" in idx

        at_idx = {r[0] for r in (await s.execute(text(
            "SELECT indexname FROM pg_indexes WHERE tablename='analysis_task'"))).all()}
        assert "ix_analysis_task_media_ids_hash" in idx or "ix_analysis_task_media_ids_hash" in at_idx
        # 旧约束已去：uq_at_media_goal 不再存在
        old = (await s.execute(text(
            "SELECT conname FROM pg_constraint WHERE conname='uq_at_media_goal'"))).all()
        assert len(old) == 0
        new = (await s.execute(text(
            "SELECT conname FROM pg_constraint WHERE conname='uq_at_mediaids_goal'"))).all()
        assert len(new) == 1
```
（`pg_session` fixture 见 `tests/conftest.py`；不可连 DB 时 infra guard 自动 skip 此测试。首次调用 `pg_session` 会跑 `alembic upgrade head`，迁移文件写完后此测试断言新结构。）

- [ ] **Step 3: Write minimal implementation**

填充迁移文件 `upgrade()` / `downgrade()`：
```python
from alembic import op
import sqlalchemy as sa


def upgrade() -> None:
    # 1. 加列（先 nullable，回填后再 NOT NULL）
    op.add_column('analysis_task',
        sa.Column('media_ids_hash', sa.String(length=64), nullable=True))

    # 2. 回填旧行：单视频任务 media_ids_hash = sha256(lower(media_id::text))
    #    pgcrypto 在本库已启用（gen_random_uuid 依赖它）；digest/encode 来自 pgcrypto。
    op.execute(
        "UPDATE analysis_task "
        "SET media_ids_hash = encode(digest(lower(CAST(media_id AS text))::bytea, 'sha256'), 'hex')"
    )

    # 3. 改 NOT NULL
    op.alter_column('analysis_task', 'media_ids_hash',
        nullable=False, server_default='')

    # 4. 换唯一约束
    op.drop_constraint('uq_at_media_goal', 'analysis_task', type_='unique')
    op.create_unique_constraint(
        'uq_at_mediaids_goal', 'analysis_task', ['media_ids_hash', 'goal_hash'])

    # 5. 索引
    op.create_index('ix_analysis_task_media_ids_hash', 'analysis_task', ['media_ids_hash'])

    # 6. 关联表
    op.create_table(
        'analysis_task_media',
        sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
        sa.Column('task_id', sa.UUID(), nullable=False),
        sa.Column('media_id', sa.UUID(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True),
            server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['task_id'], ['analysis_task.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['media_id'], ['media_file.id'], ondelete='CASCADE'),
        sa.UniqueConstraint('task_id', 'media_id', name='uq_atm_task_media'),
    )
    op.create_index('ix_analysis_task_media_task_id', 'analysis_task_media', ['task_id'])
    op.create_index('ix_analysis_task_media_media_id', 'analysis_task_media', ['media_id'])


def downgrade() -> None:
    op.drop_index('ix_analysis_task_media_media_id', table_name='analysis_task_media')
    op.drop_index('ix_analysis_task_media_task_id', table_name='analysis_task_media')
    op.drop_table('analysis_task_media')
    op.drop_index('ix_analysis_task_media_ids_hash', table_name='analysis_task')
    op.drop_constraint('uq_at_mediaids_goal', 'analysis_task', type_='unique')
    op.create_unique_constraint('uq_at_media_goal', 'analysis_task', ['media_id', 'goal_hash'])
    op.drop_column('analysis_task', 'media_ids_hash')
```

- [ ] **Step 4: Run migration + test**

Run：
```bash
/d/ProgramData/anaconda3/python.exe -m alembic upgrade head
/d/ProgramData/anaconda3/python.exe -m alembic downgrade -1
/d/ProgramData/anaconda3/python.exe -m alembic upgrade head
/d/ProgramData/anaconda3/python.exe -m pytest tests/migration/test_multi_video_migration.py -v
```
Expected：upgrade/downgrade/upgrade 三步无报错；测试 PASS。
> 若 `migrate_analyze` 测试与其它共享 DB 的 `pg_session` 串扰，顺序跑全 pytest 仍应过——此迁移是 head。

- [ ] **Step 5: Commit**

```bash
git add alembic/versions/2026_08_01_*.py tests/migration/test_multi_video_migration.py
git commit -m "feat(migration): add media_ids_hash + analysis_task_media + backfill old single-video rows"
```
（`git add alembic/versions/2026_08_01_*.py` 而非 `alembic/versions/*.py`，精确锁定本次迁移，避免带入历史迁移。）

---

### Task 8: API — AnalyzeRequest.media_ids + analyze 端点 + _run_agent_loop 多视频

**Files:**
- Modify: `src/videomind/interface/routes/agent.py`
- Test: `tests/interface/test_agent_analyze_multi.py`（新建，httpx + ASGITransport + patch `_run_agent_loop`）

**Interfaces:**
- Consumes: Task 6 `AnalysisTask.media_ids_hash`、`AnalysisTaskMedia`；`m.MediaFile`；404/409 detail 格式与 `/rag` 一致
- Produces: `AnalyzeRequest{goal, media_ids: list[UUID] (min 1, max 20), user_id, max_rounds (ge1 le2)}`；`analyze()`：逐 media 校验 → `media_ids_hash=sha256(",".join(sorted(str(m))))` → 幂等 `(media_ids_hash, goal_hash, status != failed)` → 建 `AnalysisTask` + N 条 `AnalysisTaskMedia` (position=index) → `BackgroundTasks.add_task(_run_agent_loop, ..., media_ids=list, max_rounds)`；`_run_agent_loop(*, task_id, goal, media_ids, max_rounds)`：`AgentState(goal, media_ids=[str...], video_meta=await _load_video_meta(...))`，循环 `range(1, max_rounds+1)`（去 `effective_rounds=min(...,2)` 钳制），checkpoint 写 `video_context_ref`。

- [ ] **Step 1: Write the failing tests**

新建 `tests/interface/test_agent_analyze_multi.py`：
```python
"""POST /api/agent/analyze 多视频：建任务+关联行/幂等/乱序归一/404/409/422."""

from __future__ import annotations

import uuid as _uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from videomind.infrastructure.storage import models as m
from videomind.infrastructure.storage.database import AsyncSessionLocal


async def _seed(use_ready=True):
    """建 user + 2 ready media (+1 not-ready if use_ready)，返回 ids."""
    uid = _uuid.uuid4()
    m1, m2 = _uuid.uuid4(), _uuid.uuid4()
    nr = _uuid.uuid4()
    async with AsyncSessionLocal() as s:
        s.add(m.User(id=uid, username=f"u{uid.hex[:8]}",
            email=f"{uid.hex[:8]}@t.com", is_active=True, is_superuser=False,
            password_hash="x", full_name=None, phone=None,
            avatar_url=None, real_name=None))
        s.add(m.MediaFile(id=m1, user_id=uid, source_type="upload",
            content_hash=f"h{m1.hex[:8]}", filename="a.mp4", mime_type="video/mp4",
            file_size=1, duration_ms=1000, status="ready"))
        s.add(m.MediaFile(id=m2, user_id=uid, source_type="upload",
            content_hash=f"h{m2.hex[:8]}", filename="b.mp4", mime_type="video/mp4",
            file_size=1, duration_ms=2000, status="ready"))
        if use_ready:
            s.add(m.MediaFile(id=nr, user_id=uid, source_type="upload",
                content_hash=f"h{nr.hex[:8]}", filename="c.mp4", mime_type="video/mp4",
                file_size=1, duration_ms=3000, status="downloading"))
        await s.commit()
    return uid, m1, m2, nr


@pytest.mark.asyncio
async def test_analyze_multi_creates_task_and_association_rows(pg_session) -> None:
    from videomind.interface.routes import agent as agent_mod

    uid, m1, m2, _nr = await _seed()
    with patch.object(agent_mod, "_run_agent_loop", AsyncMock()):
        transport = ASGITransport(app=agent_mod.router.app)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.post("/api/agent/analyze", json={
                "goal": "g1", "media_ids": [str(m1), str(m2)],
                "user_id": str(uid), "max_rounds": 2})
    assert r.status_code == 202, r.text
    tid = r.json()["task_id"]
    async with AsyncSessionLocal() as s:
        cnt = (await s.execute(select(func.count()).select_from(m.AnalysisTaskMedia)
            .where(m.AnalysisTaskMedia.task_id == _uuid.UUID(tid)))).scalar()
        assert cnt == 2
        positions = sorted(r[0] for r in (await s.execute(
            select(m.AnalysisTaskMedia.position).where(
                m.AnalysisTaskMedia.task_id == _uuid.UUID(tid)))).all())
        assert positions == [0, 1]


@pytest.mark.asyncio
async def test_analyze_idempotent_reorders_reuse_same_task(pg_session) -> None:
    from videomind.interface.routes import agent as agent_mod

    uid, m1, m2, _nr = await _seed()
    with patch.object(agent_mod, "_run_agent_loop", AsyncMock()):
        transport = ASGITransport(app=agent_mod.router.app)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            r1 = await c.post("/api/agent/analyze", json={
                "goal": "g1", "media_ids": [str(m1), str(m2)],
                "user_id": str(uid), "max_rounds": 2})
            r2 = await c.post("/api/agent/analyze", json={
                "goal": "g1", "media_ids": [str(m2), str(m1)],   # 乱序
                "user_id": str(uid), "max_rounds": 2})
    assert r1.status_code == 202 and r2.status_code == 202
    assert r1.json()["task_id"] == r2.json()["task_id"]


@pytest.mark.asyncio
async def test_analyze_rejects_not_ready_media_409(pg_session) -> None:
    from videomind.interface.routes import agent as agent_mod

    uid, _m1, _m2, nr = await _seed()
    with patch.object(agent_mod, "_run_agent_loop", AsyncMock()):
        transport = ASGITransport(app=agent_mod.router.app)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.post("/api/agent/analyze", json={
                "goal": "g1", "media_ids": [str(nr)],
                "user_id": str(uid), "max_rounds": 2})
    assert r.status_code == 409
    assert str(nr) in r.json()["detail"]
    assert "未就绪" in r.json()["detail"]


@pytest.mark.asyncio
async def test_analyze_rejects_missing_media_404(pg_session) -> None:
    from videomind.interface.routes import agent as agent_mod

    uid, _m1, _m2, _nr = await _seed()
    missing = _uuid.uuid4()
    with patch.object(agent_mod, "_run_agent_loop", AsyncMock()):
        transport = ASGITransport(app=agent_mod.router.app)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.post("/api/agent/analyze", json={
                "goal": "g1", "media_ids": [str(missing)],
                "user_id": str(uid), "max_rounds": 2})
    assert r.status_code == 404
    assert "not found" in r.json()["detail"]


@pytest.mark.asyncio
async def test_analyze_rejects_empty_media_ids_422(pg_session) -> None:
    from videomind.interface.routes import agent as agent_mod

    uid, _m1, _m2, _nr = await _seed()
    transport = ASGITransport(app=agent_mod.router.app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/api/agent/analyze", json={
            "goal": "g1", "media_ids": [],
            "user_id": str(uid), "max_rounds": 2})
    assert r.status_code == 422
```
> **app 引用**：测试用 `agent_mod.router.app`（router 挂在 FastAPI `app` 上，`.` 链回 `app`）。若该属性不存在而 `app` 暴露在 `videomind.interface.__init__`，用 `from videomind.interface import app` + `ASGITransport(app=app)`；patch 目标仍是 `videomind.interface.routes.agent._run_agent_loop`（endpoint 通过模块全局名引用）。实现时确认 `app` 路径——优先 `from videomind.interface import app`（与 `interface/__init__.py:37` `app = FastAPI(...)` 一致），把所有 `agent_mod.router.app` 替换为 `app`。

- [ ] **Step 2: Run tests to verify they fail**

Run: `/d/ProgramData/anaconda3/python.exe -m pytest tests/interface/test_agent_analyze_multi.py -v`
Expected: FAIL（`AnalyzeRequest` 无 `media_ids` 字段 → Pydantic 422，且 404/409 测试因不校验各 media 而错）

- [ ] **Step 3: Write minimal implementation**

`src/videomind/interface/routes/agent.py` 改动：

(a) `AnalyzeRequest`：
```python
class AnalyzeRequest(BaseModel):
    """发起 Agent 分析任务（多视频）。"""
    goal: str = Field(..., min_length=1, max_length=5000, description="分析目标")
    media_ids: list[uuid.UUID] = Field(
        ..., min_length=1, max_length=20, description="目标视频列表（≥1，≤20）")
    user_id: uuid.UUID = Field(..., description="发起用户 ID（auth 实现前由前端传入）")
    max_rounds: int = Field(2, ge=1, le=2, description="最大轮数；上限 2")
```

(b) `analyze()` 全量替换为：
```python
@router.post("/analyze", response_model=AnalyzeResponse, status_code=202)
async def analyze(req: AnalyzeRequest, background_tasks: BackgroundTasks) -> AnalyzeResponse:
    """发起 Agent 分析任务，立即返回 task_id；后台异步运行 AgentLoop。

    幂等：同 (media_ids_hash, goal_hash) 已有未失败任务时复用，不重复启跑。
    """
    goal_hash = hashlib.sha256(req.goal.encode()).hexdigest()
    media_ids_hash = hashlib.sha256(
        ",".join(sorted(str(x) for x in req.media_ids)).encode()
    ).hexdigest()

    async with AsyncSessionLocal() as session:
        # 逐个校验 media：存在 + ready（与 /rag 一致）
        for mid in req.media_ids:
            media = await session.get(m.MediaFile, mid)
            if not media:
                raise HTTPException(status_code=404, detail=f"media {mid} not found")
            if media.status != "ready":
                raise HTTPException(
                    status_code=409, detail=f"media {mid} 未就绪（status={media.status}）")

        # 幂等：复用未失败的同 (media_ids_hash, goal_hash) 任务
        existing = await session.execute(
            select(m.AnalysisTask)
            .where(
                m.AnalysisTask.media_ids_hash == media_ids_hash,
                m.AnalysisTask.goal_hash == goal_hash,
                m.AnalysisTask.status.notin_(["failed"]),
            )
            .order_by(m.AnalysisTask.created_at.desc())
            .limit(1)
        )
        task = existing.scalar_one_or_none()
        if task is None:
            primary = req.media_ids[0]
            task = m.AnalysisTask(
                user_id=req.user_id, media_id=primary, media_ids_hash=media_ids_hash,
                goal=req.goal, goal_hash=goal_hash, status="pending",
                max_rounds=req.max_rounds,
            )
            session.add(task)
            await session.commit()
            await session.refresh(task)
            # 关联表：每条 media 一行，position=index
            for pos, mid in enumerate(req.media_ids):
                session.add(m.AnalysisTaskMedia(
                    task_id=task.id, media_id=mid, position=pos))
            await session.commit()

        # 仅新任务才调度后台运行
        if task.status == "pending":
            background_tasks.add_task(
                _run_agent_loop,
                task_id=task.id, goal=req.goal,
                media_ids=list(req.media_ids), max_rounds=req.max_rounds,
            )

        return AnalyzeResponse(
            task_id=task.id, status=task.status, goal=task.goal,
            max_rounds=task.max_rounds, created_at=task.created_at,
        )
```

(c) `_run_agent_loop` + 新增 `_load_video_meta` + `_checkpoint` 改写。文件底部：
```python
async def _load_video_meta(media_ids: list[uuid.UUID]) -> list[Any]:
    """加载各 video 的 filename/duration_ms，供 Planner/Executor 上下文渲染来源."""
    from videomind.core.agent_loop.types import VideoMeta

    meta: list[VideoMeta] = []
    async with AsyncSessionLocal() as session:
        for mid in media_ids:
            media = await session.get(m.MediaFile, mid)
            if media:
                meta.append(VideoMeta(
                    media_id=str(mid), filename=media.filename,
                    duration_ms=media.duration_ms))
    return meta


async def _run_agent_loop(
    *,
    task_id: uuid.UUID,
    goal: str,
    media_ids: list[uuid.UUID],
    max_rounds: int,
) -> None:
    from videomind.core.agent_loop.factory import get_agent_loop
    from videomind.core.agent_loop.types import AgentState

    try:
        loop = get_agent_loop()
        video_meta = await _load_video_meta(media_ids)

        async with AsyncSessionLocal() as session:
            task = await session.get(m.AnalysisTask, task_id)
            assert task is not None
            task.status = "planning"
            task.started_at = datetime.now(timezone.utc)
            await session.commit()

        state = AgentState(
            goal=goal,
            media_ids=[str(x) for x in media_ids],
            video_meta=video_meta,
        )

        for round_num in range(1, max_rounds + 1):
            state.round = round_num
            async with AsyncSessionLocal() as session:
                t = await session.get(m.AnalysisTask, task_id)
                if t:
                    t.status = "planning"; t.current_round = round_num
                    await session.commit()
            state.plan = await loop._planner.plan(state)
            await _checkpoint(task_id, round_num, "planning", state)

            async with AsyncSessionLocal() as session:
                t = await session.get(m.AnalysisTask, task_id)
                if t: t.status = "executing"; await session.commit()
            state.result = await loop._executor.execute(state)
            await _checkpoint(task_id, round_num, "executing", state)

            async with AsyncSessionLocal() as session:
                t = await session.get(m.AnalysisTask, task_id)
                if t: t.status = "critic_check"; await session.commit()
            state.critique = await loop._critic.critique(state)
            await _checkpoint(task_id, round_num, "critic_check", state)

            if state.critique.passed:
                break

        result = state.result
        async with AsyncSessionLocal() as session:
            t = await session.get(m.AnalysisTask, task_id)
            assert t is not None
            if result and result.title:
                ar = m.AgentResult(
                    task_id=task_id, title=result.title,
                    conclusions_json=[_dataclass_to_dict(c) for c in result.conclusions],
                    evidence_json=[_dataclass_to_dict(e) for e in result.evidence],
                    suggestions_json=list(result.suggestions) or None,
                    critic_passed=bool(state.critique and state.critique.passed),
                    critic_feedback=state.critique.feedback if state.critique else None,
                    total_rounds=state.round, token_usage=None,
                )
                session.add(ar)
                t.final_result_json = {"title": result.title,
                    "conclusions": len(result.conclusions), "evidence": len(result.evidence),
                    "suggestions": len(result.suggestions)}
                t.status = "completed"
            else:
                t.status = "failed"
                t.error_message = "AgentLoop 未产生有效结果（空 AnalysisResult / 零命中）"
            t.completed_at = datetime.now(timezone.utc)
            await session.commit()

    except Exception as exc:  # noqa: BLE001
        async with AsyncSessionLocal() as session:
            t = await session.get(m.AnalysisTask, task_id)
            if t:
                t.status = "failed"
                t.error_message = f"{type(exc).__name__}: {exc}"
                t.completed_at = datetime.now(timezone.utc)
                await session.commit()
```

(d) `_checkpoint`：在 `m.AgentCheckpoint(...)` 构造中加 `video_context_ref`（既有 JSONB 列，从未写入）：
```python
            video_context_ref={
                "media_ids": list(state.media_ids),
                "retrieved_evidence_count": len(state.retrieved_evidence_ids),
                "round": state.round,
            },
```
其余 `_checkpoint` 体不变。

- [ ] **Step 4: Run tests to verify they pass**

Run: `/d/ProgramData/anaconda3/python.exe -m pytest tests/interface/test_agent_analyze_multi.py -v`
Expected: PASS（5 个测试：建任务+2 关联行、乱序幂等、409、404、422）
> 若 `agent_mod.router.app` 报错，按 Step 1 备注 `from videomind.interface import app` 替换。

- [ ] **Step 5: Commit**

```bash
git add src/videomind/interface/routes/agent.py tests/interface/test_agent_analyze_multi.py
git commit -m "feat(agent-api): media_ids multi-video endpoint + association table + idempotency + checkpoint video_context_ref"
```

---

### Task 9: 前端 analysisApi — media_ids

**Files:**
- Modify: `frontend/src/lib/api.ts`
- **不自动 commit**（前端按约定等用户确认）

**Interfaces:**
- Consumes: 后端 Task 8 `POST /api/agent/analyze` 新 body shape
- Produces: `analysisApi.create({ goal: string; media_ids: string[]; user_id: string; max_rounds?: number })`

- [ ] **Step 1: 改签名**

`frontend/src/lib/api.ts` 第 114 行 `analysisApi.create`：
```ts
  create: (data: { goal: string; media_ids: string[]; user_id: string; max_rounds?: number }): ApiResponse<{
    task_id: string
    status: string
    goal: string
    max_rounds: number
    created_at: string
  }> => api.post('/agent/analyze', data),
```
（仅删 `media_id: string`，加 `media_ids: string[]`；返回类型不变。）

- [ ] **Step 2: 类型检查**

Run（`frontend/` 下）：
```bash
npm run build
```
Expected: FAIL——`AgentAnalysisPage.tsx` 仍传 `media_id`，类型不匹配（这正是下一步要修的）。先记下报错，转 Task 10。**不 commit**。

> 此 task 与 Task 10 合并验证：Task 10 改完 `AgentAnalysisPage` 后，`npm run build` 应恢复通过。

---

### Task 10: 前端 AgentAnalysisPage — 多选折叠面板 + 证据新字段渲染

**Files:**
- Modify: `frontend/src/features/agent-analysis/AgentAnalysisPage.tsx`
- **不自动 commit**

**Interfaces:**
- Consumes: Task 9 `analysisApi.create` 的 `media_ids`；Task 8 后端 `evidence_json` 每条新含 `chunk_id/content/source_type/score/start_ms/end_ms/media_id/media_title`；RAGChat 多选 idiom（`selectedMediaIds`/`showMediaSelector`/`handleMediaToggle`/count Badge/`cn(...)` label 态）
- Produces: 多选 UI（照搬 RAGChat 折叠面板）；证据卡片渲染 `media_title` 来源 Badge + `start_ms/end_ms` 时间范围。

- [ ] **Step 1: state 改造**

`AgentAnalysisPage.tsx` 第 41 行 `const [mediaId, setMediaId] = useState<string>('')` 替换为：
```tsx
  const [selectedMediaIds, setSelectedMediaIds] = useState<string[]>([])
  const [showMediaSelector, setShowMediaSelector] = useState(false)

  const handleMediaToggle = (id: string) =>
    setSelectedMediaIds((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id],
    )
```
（其余 state 行 `goal/maxRounds/taskId` 不动。）

- [ ] **Step 2: mutation + ctaDisabled + handleSubmit 切到 media_ids**

第 76-82 行 `mutationFn` 替换为：
```tsx
    mutationFn: () =>
      analysisApi.create({
        goal: goal.trim(),
        media_ids: selectedMediaIds,
        user_id: userConfig!.user_id,
        max_rounds: maxRounds,
      }),
```
第 101 行 `ctaDisabled` 替换为：
```tsx
  const ctaDisabled = !goal.trim() || selectedMediaIds.length === 0 || !userConfig || createMutation.isPending
```
第 104-107 行 `handleSubmit` 替换为：
```tsx
  const handleSubmit = () => {
    if (!goal.trim() || selectedMediaIds.length === 0 || !userConfig) return
    createMutation.mutate()
  }
```

- [ ] **Step 3: 单选 select → 多选折叠面板**

第 10-22 行 lucide import 块内加 `Film`（`Brain,` 后插一行 `Film,`）。

第 146-176 行（`<div className="grid grid-cols-1 md:grid-cols-2 gap-4">` 内的两个列）替换为：
```tsx
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <label className="text-sm font-medium">{t('agentAnalysis.videoSources')}</label>
                <Button type="button" variant="ghost" size="sm"
                  onClick={() => setShowMediaSelector((v) => !v)}>
                  {t(showMediaSelector ? 'agentAnalysis.collapseVideoSources' : 'agentAnalysis.expandVideoSources')}
                </Button>
              </div>
              {selectedMediaIds.length > 0 && (
                <Badge variant="secondary" className="gap-1 w-fit">
                  <Brain className="h-3 w-3" />
                  {t('agentAnalysis.videoSourcesCount', { count: selectedMediaIds.length })}
                </Badge>
              )}
              {showMediaSelector && (
                <div className="border rounded-lg p-2 max-h-64 overflow-y-auto space-y-1">
                  {videos?.items?.map((v) => (
                    <label key={v.id} className={cn(
                      'flex items-center gap-3 p-2 rounded-lg cursor-pointer transition-colors',
                      selectedMediaIds.includes(v.id)
                        ? 'bg-primary/10 border border-primary/20'
                        : 'hover:bg-muted/50',
                    )}>
                      <input type="checkbox" checked={selectedMediaIds.includes(v.id)}
                        onChange={() => handleMediaToggle(v.id)}
                        className="h-4 w-4 rounded border-input text-primary focus:ring-primary" />
                      <span className="flex-1 text-sm">{v.filename}</span>
                      {v.duration_ms != null && (
                        <span className="text-xs text-muted-foreground">
                          {(v.duration_ms / 1000).toFixed(0)}s
                        </span>
                      )}
                    </label>
                  ))}
                  {videos && !videos.items?.length && (
                    <p className="text-xs text-muted-foreground p-2">
                      {t('agentAnalysis.noVideosForAnalysis')}
                    </p>
                  )}
                </div>
              )}
              {!showMediaSelector && (
                <p className="text-xs text-muted-foreground">{t('agentAnalysis.selectAtLeastOne')}</p>
              )}
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium">{t('agentAnalysis.maxRounds')}</label>
              <select
                value={maxRounds}
                onChange={(e) => setMaxRounds(Number(e.target.value))}
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-ring"
              >
                <option value={1}>{t('agentAnalysis.oneRound')}</option>
                <option value={2}>{t('agentAnalysis.twoRounds')}</option>
              </select>
            </div>
```

- [ ] **Step 4: 证据渲染换新字段**

第 339-360 行证据 `<section>` 的 `<ul>` 内 map 替换为：
```tsx
                  {(result.evidence_json as any[]).map((e, i) => (
                    <li key={i} className="flex flex-col gap-1 text-sm">
                      <span className="flex items-center gap-2 flex-wrap">
                        {e.media_title && (
                          <Badge variant="secondary" className="shrink-0 text-xs gap-1">
                            <Film className="h-3 w-3" /> {e.media_title}
                          </Badge>
                        )}
                        {e.source_type && (
                          <Badge variant="outline" className="shrink-0 text-xs">{e.source_type}</Badge>
                        )}
                      </span>
                      <span className="text-muted-foreground pl-1">
                        {e.content ?? JSON.stringify(e)}
                        {(typeof e.start_ms === 'number' || typeof e.end_ms === 'number') && (
                          <span className="ml-2 text-xs">
                            · {e.start_ms != null ? (e.start_ms / 1000).toFixed(1) : '—'}
                            –{e.end_ms != null ? (e.end_ms / 1000).toFixed(1) : '—'}s
                          </span>
                        )}
                      </span>
                    </li>
                  ))}
```

- [ ] **Step 5: 验证 build + dev**

Run（`frontend/` 下）：
```bash
npm run build
```
Expected: PASS（类型与 i18n key 在 Task 11 补齐前会有 i18n 警告但 build 不阻断；若 `tsc` 严格失败先确认未漏 `Film`/`cn` 导入）。

```bash
npm run dev
```
浏览器打开 Agent 深度分析页，确认：折叠面板可展开、多选、Badge 计数、提交不报错（需后端运行）。**不 commit**。

---

### Task 11: 前端 i18n — 新键 + 补缺失键 + 清理旧键

**Files:**
- Modify: `frontend/src/i18n/zh-CN.json`
- Modify: `frontend/src/i18n/en-US.json`
- **不自动 commit**

**Interfaces:**
- Consumes: Task 10 引用的新键 `videoSources/videoSourcesCount/selectAtLeastOne/expandVideoSources/collapseVideoSources`；既有页面对 `devIdentityHint/criticFeedback/costPending` 的引用（[AgentAnalysisPage.tsx:199](frontend/src/features/agent-analysis/AgentAnalysisPage.tsx#L199), [:311](frontend/src/features/agent-analysis/AgentAnalysisPage.tsx#L311), [:317](frontend/src/features/agent-analysis/AgentAnalysisPage.tsx#L317)）
- Produces: zh/en 两份 JSON 的 `agentAnalysis` 块补 5 个新键 + 3 个缺失键；清退 `targetVideo/selectVideo`（仅当 grep 确认仅本页引用）。

- [ ] **Step 1: grep 确认旧键引用面**

Run：
```bash
grep -rn "agentAnalysis.targetVideo\|agentAnalysis.selectVideo" frontend/src
```
预期仅 `AgentAnalysisPage.tsx`。若命中其它文件，**保留旧键**不删；若仅本页已改完，进 Step 2 删除。

- [ ] **Step 2: 改 zh-CN.json**

`agentAnalysis` 块内（第 159-160 行 `targetVideo`/`selectVideo` 两行）替换为：
```json
    "videoSources": "视频源列表",
    "videoSourcesCount": "{count} 个视频源",
    "selectAtLeastOne": "请至少选择 1 个视频",
    "expandVideoSources": "展开视频源",
    "collapseVideoSources": "收起视频源",
```
（即删除 `targetVideo`/`selectVideo`，替换为以上 5 键。若 Step 1 判定保留旧键，则在它们之后追加而非删除。）

`agentAnalysis` 块末（`timestamp` 行之后）追加 3 个缺失键：
```json
    "devIdentityHint": "Dev 身份未就绪（未取到 user config），暂不可提交",
    "criticFeedback": "评审反馈",
    "costPending": "费用待核算"
```

- [ ] **Step 3: 改 en-US.json**

`agentAnalysis` 块内（第 159-160 行）替换为：
```json
    "videoSources": "Video Sources",
    "videoSourcesCount": "{count} video source(s)",
    "selectAtLeastOne": "Select at least 1 video",
    "expandVideoSources": "Expand video sources",
    "collapseVideoSources": "Collapse video sources",
```
`agentAnalysis` 块末（`timestamp` 行之后）追加：
```json
    "devIdentityHint": "Dev identity not ready (no user config); cannot submit",
    "criticFeedback": "Critic feedback",
    "costPending": "cost pending"
```
修正 en `subtitle`（第 155 行）末尾杂入的中文 "闭环"：`"...up to 2 rounds闭环"` → `"...up to 2 rounds"`。

- [ ] **Step 4: 验证 i18n 无缺失警告**

Run（`frontend/` 下，dev 开着时看浏览器 console）：
```bash
npm run build
```
Expected: PASS。控制台无 `i18n: missing key agentAnalysis.*` 警告。**不 commit**。

---

### Task 12: Playwright 端到端实测

**Files:**
- 无文件改动（手动/Playwright MCP 验证）
- **不自动 commit**

**Precondition:** 后端（`uvicorn` / 项目启动方式）+ 前端 `npm run dev` 均运行；DB 已 `alembic upgrade head`；至少 2 个 `status=ready` 的视频存在（否则先上传/处理）。

- [ ] **Step 1: 打开 Agent 深度分析页**

Playwright：`browser_navigate` 到 `http://localhost:5173/agent`（或实际 dev 端口）。
- 断言：标题 "Agent 深度分析" / "Agent Deep Analysis"。

- [ ] **Step 2: 多选 2 个视频 + 填 goal + 提交**

- `browser_click` "展开视频源"按钮。
- 断言：视频列表出现，每行有 checkbox。
- 勾选前 2 个视频 → 断言 Badge 显示 `2 个视频源` / `2 video source(s)`。
- 填 goal `分析两个视频讲解的商业模式并对比`。
- `max_rounds` 选 2。
- 点 "开始分析" → 断言任务进度卡片出现，状态进入 `planning` → `executing`（轮询最多 ~60s）。

- [ ] **Step 3: 完成后核对证据渲染**

- 等 `completed`。
- 拉取结果卡片 `Get Result`：断言 evidence 区每条带 `media_title` 来源 Badge（非空）+ 时间范围 `x.x–y.ys`。
- 断言：旧字段 `timestamp_ms`/`source` 不再渲染（无 `[EID] source` 单字 Badge）。

- [ ] **Step 4: 跨语言切换**

- 顶部语言切换到 English。
- 断言："Video Sources" / `{count} video source(s)` / "Expand video sources" 等文案随语言变；证据来源 Badge 仍为 `media_title`（文件名不变）。

- [ ] **Step 5: 旧 evidence 字段回归核对**

- 切回中文；提交一个**零命中** goal（如纯胡乱字符组合检索不到的 goal 但格式合法）→ 任务应走到 `failed` 或 `completed` 但 evidence 为空列表——断言不崩、无 `[object Object]`/`undefined` 渲染。

> 全绿后向用户报告：后端 8 个 task 已提交（commit history），前端 3 个 task 未自动 commit——等用户确认是否打 commit。

---

## Self-Review Notes

**1. Spec 覆盖**：
- §10 D1（全修）：Task 3 修 Executor 空跑、Task 4 修 Critic verify_all、Task 5 修 max_rounds 钳制。✓
- §10 D2（真实命中校验）：Task 4 读 `retrieved_evidence_ids` 做 chunk/conclusion 命中校验，弃时间戳范围。✓
- §10 D3（N 次合并不改 VectorRetriever）：Task 3 `_RagRetriever` 调 `rag_pipeline.search` per media_id，不改 retriever。✓
- §10 D4（primary + 关联表 + media_ids_hash）：Task 6 ORM + Task 7 迁移 + Task 8 端点写入。✓
- §13 接口契约表 8 行：Task 6/7/8 全覆盖（media_id→media_ids、max_rounds le5→le2、幂等键、evidence_json 字段、_run_agent_loop 入参、Executor prompt、Critic 校验、运行 max_rounds）。✓
- §14 错误处理：404/409/422 逐 media 报明 ids（Task 8 端点 + Task 8 测试覆盖）；零命中→Critic false（Task 4 测试 `test_critic_fails_on_empty_retrieval` 经由 `bool(result.evidence)` 守门）。✓
- §15 测试策略 4 个测试文件：test_executor_multi_video（Task 3）、test_critic_real_evidence（Task 4）、test_agent_analyze_multi（Task 8）、test_multi_video_migration（Task 7）。✓
- §17 提交约束：docs/superpowers 不推（Global Constraints + 各 backend task 精确 `git add`）、前端不自动 commit（Task 9-12 明示）。✓

**2. 占位符扫描**：无 `TBD/TODO/…later`；每步含可执行命令或完整代码。唯一"软依赖"是 Task 8 的 `app` 引用（`agent_mod.router.app` vs `from videomind.interface import app`）——已给判定规则，非占位符。

**3. 类型一致**：
- `retrieved_evidence_ids: set[str]` Task 1 定 → Task 3 Executor 写 `state.retrieved_evidence_ids = set(all_evidence.keys())` → Task 4 Critic 读 `state.retrieved_evidence_ids`。键/字段名一致。✓
- `VideoMeta(media_id: str, filename, duration_ms)` Task 1 → Planner(2)/Executor(3) 读 `state.video_meta` + `_load_video_meta`(8) 造。✓
- `SubTask.search_query` Task 1 → Planner(2) 写 / Executor(3) 读 `task.search_query or task.description`。✓
- `Evidence` 新字段 `media_id/media_title/start_ms/end_ms/source_type/score` Task 1 → Executor(3) 构造 → `_dataclass_to_dict`(8)/`evidence_json` → 前端(10) 渲染。✓
- `Executor(llm, retriever=None)` Task 3 → factory(5) `Executor(llm, retriever=retriever)`。✓
- `run(goal, max_rounds=2)` Task 5 → 现有 test_loop 用 `loop.run("goal")`（默认 2）不破。✓
- `AnalysisTaskMedia` Task 6 → Task 8 `session.add(m.AnalysisTaskMedia(...))`。✓
- `AnalyzeRequest.media_ids` Task 8 → 前端 Task 9 `media_ids: string[]`。✓

**4. 回归护栏**：
- 保留 `Evidence.timestamp_ms/source` 带默认 → `test_verifier.py` 4 测试不动仍过（任务 1 Step 4 含跑 verifier）。✓
- `Critic(__init__(llm, verifier))` 签名不变 → factory(5) `Critic(llm, EvidenceVerifier())` 仍合法。✓
- 现有 `AgentLoop.run("goal")` 调用不破（max_rounds 默认 2）。✓
- 迁移回填 `sha256(str(media_id))` ↔ 新单视频 `sha256(",".join(sorted([str(x)])) = sha256(str(x))` 一致 → 旧单视频任务可被新幂等查询复用（spec 风险表）。✓

