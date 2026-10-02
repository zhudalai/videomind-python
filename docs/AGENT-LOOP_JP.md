# VideoMind AgentLoop 設計

> Planner → Executor → Critic のクローズドループ（≤2 ラウンド）+ エビデンスの厳格検証 + チェックポイントによる中断・復帰
> 主要参考：DOVideo-AI `AgentLoopService.java` + `EvidenceVerificationService.java` + `AgentCheckpointService.java`

---

## 1. AgentLoop の全体像

```
ユーザーの目標 (Goal)
      │
      ▼
┌─────────────────────────────────────────────────────────────────┐
│                        AgentLoop                                 │
│                                                                  │
│  Round 0: 初期化                                                  │
│  ├─ VideoContext (segments, full_transcript, evidence_frames) をロード│
│  └─ AgentState(goal, plan=None, result=None, critique=None, round=0) を生成│
│                                                                  │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Round 1..MAX_ROUNDS (デフォルト 2)                           │  │
│  │                                                            │  │
│  │  ┌─────────────┐                                           │  │
│  │  │  PLANNER    │  目標 → 実行可能なサブタスク 1〜5 件             │  │
│  │  │             │  ASR/OCR/タイムスタンプのみ依存、主観的推測は禁止│  │
│  │  └──────┬──────┘                                           │  │
│  │         │                                                    │  │
│  │         ▼                                                    │  │
│  │  ┌─────────────┐                                           │  │
│  │  │  EXECUTOR   │  各タスクを順次実行 → 構造化結果                │  │
│  │  │             │  {title, conclusions[], evidence[],       │  │
│  │  │             │   suggestions[]}                          │  │
│  │  └──────┬──────┘                                           │  │
│  │         │                                                    │  │
│  │         ▼                                                    │  │
│  │  ┌─────────────┐                                           │  │
│  │  │  CRITIC     │  目標カバレッジ/構造完全性/タイムスタンプ証拠/ハルシネーションなし       │  │
│  │  │             │  passed? → 最終結果を出力                    │  │
│  │  │             │  failed → feedback + requiredTimestamps   │  │
│  │  └──────┬──────┘                                           │  │
│  │         │                                                    │  │
│  │         ├─▶ passed → BREAK                                   │  │
│  │         │                                                    │  │
│  │         └─▶ failed → エビデンス厳格検証 → 補完 → Plan 修正 → NEXT ROUND │
│  │                                                            │  │
│  │  Checkpoint: 毎ラウンド AgentState を永続化 (PostgreSQL + Redis) │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
      │
      ▼
最終結果：AnalysisResult + Evidence（ミリ秒単位のタイムスタンプまで遡ってトレース可能）
```

---

## 2. コアデータ構造

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
    tasks: list[SubTask]  # 1〜5 件
    reasoning: str        # プランニングの理由

@dataclass
class SubTask:
    id: str               # "task_1", "task_2"...
    description: str      # 具体的な実行内容
    required_evidence_type: Literal["asr", "ocr", "frame", "mixed"]
    time_range_hint: tuple[int, int] | None  # (start_ms, end_ms) オプションのヒント

@dataclass
class AnalysisResult:
    title: str
    conclusions: list[Conclusion]
    evidence: list[Evidence]
    suggestions: list[str]  # フォローアップ質問の提案

@dataclass
class Conclusion:
    point: str                    # 核心的な論点
    evidence_ids: list[str]       # 参照している Evidence ID
    confidence: float             # 0-1

@dataclass
class Evidence:
    id: str                       # ChunkEvidenceID: EID_{chunk_id[:8]}_{idx:02d}(統一形式, INTENT-ROUTING.md の make_evidence_id を参照)
    timestamp_ms: int             # 動画のタイムスタンプ（ミリ秒）
    source: Literal["asr", "ocr", "frame"]
    content: str                  # 原文の断片（≤500 文字）
    chunk_id: UUID                # chunk テーブルとの関連付け

@dataclass
class CriticResult:
    passed: bool
    feedback: str                 # Executor への修正提案
    required_timestamps: list[int]  # エビデンス補完が必須のタイムスタンプ（ミリ秒）
    coverage_score: float         # 目標カバレッジ 0-1
    structure_ok: bool            # 構造が完全かどうか
    evidence_verified: bool       # エビデンスがすべて検証を通過したかどうか
    hallucination_risk: float     # ハルシネーションリスク 0-1
```

---

## 3. 3 つのロールの実装

### 3.1 Planner（プランナー）

```python
class Planner:
    SYSTEM_PROMPT = """
    あなたは動画分析タスクのプランナーです。ユーザーの目標と動画コンテキスト（セグメント化済みの ASR+OCR）が与えられたとき、
    目標を 1〜5 個の具体的で実行可能なサブタスクに分解してください。
    
    ルール：
    1. 各サブタスクは動画エビデンス（ASR/OCR/キーフレーム）に基づいて検証可能でなければならない
    2. 主観的な推測、外部知識、未来の予測は禁止
    3. サブタスクの粒度：単一事実の検証、論点の抽出、タイムライン上の位置特定、比較・帰納
    4. JSON を出力：{"tasks": [...], "reasoning": "..."}
    
    動画コンテキストの概要：{context_summary}
    ユーザーの目標：{goal}
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
            for i, t in enumerate(data["tasks"][:5])  # 最大 5 件
        ]
        
        return AgentPlan(tasks=tasks, reasoning=data["reasoning"])
    
    def _summarize_context(self, ctx: VideoContext) -> str:
        """Planner 向けのコンテキスト概要を生成（トークン効率を考慮）"""
        return f"""
    動画の長さ：{ms_to_hms(ctx.duration_ms)}
    総セグメント数：{len(ctx.segments)} (各セグメント 60s)
    キーフレーム数：{sum(len(s.evidence_frames) for s in ctx.segments)}
    全文の先頭 2000 文字：{ctx.full_transcript[:2000]}...
    代表的なセグメントの例：
    {chr(10).join(f"  [{ms_to_ts(s.start_ms)}-{ms_to_ts(s.end_ms)}] {s.transcript[:100]}..." for s in ctx.segments[:3])}
    """
```

---

### 3.2 Executor（エグゼキューター）

```python
class Executor:
    SYSTEM_PROMPT = """
    あなたは動画エビデンスアナリストです。サブタスクと関連するエビデンス断片が与えられたとき、構造化された結論を生成してください。
    
    ルール：
    1. 与えられたエビデンスのみを使用し、捏造は禁止
    2. 各結論にはエビデンス ID とタイムスタンプの紐付けが必須
    3. エビデンス ID の形式：EID_{chunk_id[:8]}_{idx:02d}（統一形式、INTENT-ROUTING.md の make_evidence_id を参照）
    4. JSON を出力：{"title": "...", "conclusions": [...], "suggestions": [...]}
    
    サブタスク：{task}
    関連エビデンス：{evidence}
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
            # 1. このタスクに関連するエビデンスを検索
            evidence_chunks = await self._retrieve_for_task(task, state.video_context)
            
            # 2. エビデンスコンテキストを構築
            evidence_text = self._format_evidence(evidence_chunks)
            
            # 3. LLM が結論を生成
            response = await self.llm.chat(
                messages=[{"role": "user", "content": self.SYSTEM_PROMPT.format(
                    task=task.description,
                    evidence=evidence_text
                )}],
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            
            data = json.loads(response.content)
            
            # 4. 結論をパースしてエビデンスを紐付け
            for conc in data.get("conclusions", []):
                conclusion = Conclusion(
                    point=conc["point"],
                    evidence_ids=conc["evidence_ids"],
                    confidence=conc.get("confidence", 0.8)
                )
                all_conclusions.append(conclusion)
                
                # Evidence の詳細を補完
                for eid in conc["evidence_ids"]:
                    if eid not in [e.id for e in all_evidence]:
                        ev = self._find_evidence(eid, evidence_chunks)
                        if ev:
                            all_evidence.append(ev)
            
            all_suggestions.extend(data.get("suggestions", []))
        
        # 提案の重複排除
        unique_suggestions = list(dict.fromkeys(all_suggestions))[:5]
        
        return AnalysisResult(
            title=data.get("title", f"分析：{plan.tasks[0].description[:30]}"),
            conclusions=all_conclusions,
            evidence=all_evidence,
            suggestions=unique_suggestions
        )
    
    async def _retrieve_for_task(self, task: SubTask, ctx: VideoContext) -> list[SearchResult]:
        """サブタスクに対してエビデンスを検索：時間範囲ヒントを優先し、なければセマンティック検索"""
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

### 3.3 Critic（クリティック/検証器）

```python
class Critic:
    SYSTEM_PROMPT = """
    あなたは厳格な品質レビュアーです。分析結果がユーザーの目標を満たしているか、構造が完全か、エビデンスが検証可能かを確認します。
    
    評価の観点：
    1. 目標カバレッジ：ユーザーの目標の重要ポイントすべてに回答しているか
    2. 構造の完全性：title/conclusions/evidence/suggestions がすべて揃っているか
    3. エビデンスの紐付け：各 conclusion に evidence_ids があるか、各 evidence に timestamp_ms+source+content があるか
    4. エビデンスの検証：タイムスタンプが動画の範囲内にあるか、内容が ASR/OCR に実在するか
    5. ハルシネーションなし：結論がエビデンスの範囲を超えていないか
    
    JSON を出力：
    {
      "passed": true/false,
      "feedback": "Executor への具体的な修正提案",
      "required_timestamps": [123456, 234567],  // エビデンス補完が必須の時点
      "coverage_score": 0.9,
      "structure_ok": true,
      "evidence_verified": true/false,
      "hallucination_risk": 0.1
    }
    
    ユーザーの目標：{goal}
    分析結果：{result_json}
    動画の長さ：{duration_ms}ms
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
        
        # 1. LLM による評価
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
        
        # 2. ハード制約によるエビデンス検証（EvidenceVerifier）
        verified, failed_evidence = await self.verifier.verify_all(
            result.evidence,
            state.video_context
        )
        
        # 3. 結果をマージ：エビデンス検証が失敗した場合は強制的に passed=false
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

### 3.4 EvidenceVerifier（エビデンス厳格検証器）

```python
class EvidenceVerifier:
    """元の ASR/OCR に対してエビデンスのタイムスタンプと内容の真実性を検証する"""
    
    TOLERANCE_MS = 5000  # ±5s の許容誤差
    CONTENT_SIMILARITY_THRESHOLD = 0.7  # 文字レベルの類似度しきい値
    
    async def verify_all(self, evidence: list[Evidence], ctx: VideoContext) -> tuple[bool, list[Evidence]]:
        failed = []
        for ev in evidence:
            ok = await self._verify_one(ev, ctx)
            if not ok:
                failed.append(ev)
        return len(failed) == 0, failed
    
    async def _verify_one(self, ev: Evidence, ctx: VideoContext) -> bool:
        # 1. タイムスタンプの範囲チェック
        if not (0 <= ev.timestamp_ms <= ctx.duration_ms):
            return False
        
        # 2. 対応する segment を特定
        segment = self._find_segment(ctx, ev.timestamp_ms)
        if not segment:
            return False
        
        # 3. source タイプに応じて内容を検証
        if ev.source == "asr":
            return self._verify_asr(ev, segment)
        elif ev.source == "ocr":
            return self._verify_ocr(ev, segment)
        elif ev.source == "frame":
            return self._verify_frame(ev, segment)
        return False
    
    def _verify_asr(self, ev: Evidence, segment: VideoSegment) -> bool:
        # segment.transcript 内で ev.content の部分文字列を検索（あいまいマッチング）
        return self._fuzzy_contains(segment.transcript, ev.content)
    
    def _verify_ocr(self, ev: Evidence, segment: VideoSegment) -> bool:
        # segment.ocr_texts のリスト内を検索
        for ocr in segment.ocr_texts:
            if self._fuzzy_contains(ocr, ev.content):
                return True
        return False
    
    def _verify_frame(self, ev: Evidence, segment: VideoSegment) -> bool:
        # evidence_frames 内で timestamp_ms による一致を判定
        for frame in segment.evidence_frames:
            if abs(frame.timestamp_ms - ev.timestamp_ms) <= self.TOLERANCE_MS:
                if frame.ocr_text and self._fuzzy_contains(frame.ocr_text, ev.content):
                    return True
        return False
    
    def _fuzzy_contains(self, haystack: str, needle: str) -> bool:
        """文字レベルの編集距離類似度"""
        if needle in haystack:
            return True
        # 部分マッチを許可：needle のコア語が haystack 内に存在すればよい
        needle_words = set(jieba.lcut(needle))
        haystack_words = set(jieba.lcut(haystack))
        overlap = len(needle_words & haystack_words) / max(len(needle_words), 1)
        return overlap >= self.CONTENT_SIMILARITY_THRESHOLD
    
    def _find_segment(self, ctx: VideoContext, timestamp_ms: int) -> VideoSegment | None:
        for seg in ctx.segments:
            if seg.start_ms <= timestamp_ms < seg.end_ms:
                return seg
        # 境界ケース：最後のフレーム
        if timestamp_ms == ctx.duration_ms and ctx.segments:
            return ctx.segments[-1]
        return None
```

---

## 4. AgentLoop のメインループ

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
        
        # 初期イベントを送信
        await self.sse.send(trace_id, TaskEvent(stage="planning", message="プランニングを開始..."))
        
        for round_num in range(1, self.MAX_ROUNDS + 1):
            state.round = round_num
            
            # === PLANNER ===
            await self.sse.send(trace_id, TaskEvent(stage="planning", message=f"第 {round_num} ラウンドのプランニング"))
            plan = await self.planner.plan(state)
            state.plan = plan
            
            # Checkpoint: プランニング完了
            await self.checkpoint.save(Checkpoint(
                task_id=UUID(trace_id),  # = analysis_task.id（FK）。trace_id はその hex 形式
                trace_id=trace_id,  # 32-hex。ログ/Trace の紐付けに使用
                round=round_num,
                phase="planning",
                agent_state=state
            ))
            
            # === EXECUTOR ===
            await self.sse.send(trace_id, TaskEvent(stage="executing", message=f"第 {round_num} ラウンドの実行"))
            result = await self.executor.execute(state)
            state.result = result
            
            # Checkpoint: 実行完了
            await self.checkpoint.save(Checkpoint(
                task_id=UUID(trace_id),  # = analysis_task.id（FK）。trace_id はその hex 形式
                trace_id=trace_id,  # 32-hex。ログ/Trace の紐付けに使用
                round=round_num,
                phase="executing",
                agent_state=state
            ))
            
            # === CRITIC ===
            await self.sse.send(trace_id, TaskEvent(stage="critic_check", message=f"第 {round_num} ラウンドのレビュー"))
            critique = await self.critic.critique(state)
            state.critique = critique
            
            # Checkpoint: レビュー完了
            await self.checkpoint.save(Checkpoint(
                task_id=UUID(trace_id),  # = analysis_task.id（FK）。trace_id はその hex 形式
                trace_id=trace_id,  # 32-hex。ログ/Trace の紐付けに使用
                round=round_num,
                phase="critic_check",
                agent_state=state,
                critique=critique
            ))
            
            if critique.passed:
                await self.sse.send(trace_id, TaskEvent(stage="completed", message="分析が承認されました"))
                break
            
            # 不合格：次ラウンドの準備
            await self.sse.send(trace_id, TaskEvent(
                stage="retry",
                message=f"第 {round_num} ラウンドは不合格、エビデンスの補完を準備: {critique.feedback}"
            ))
            
            # required_timestamps を次ラウンドの Planner コンテキストに注入
            state = self._prepare_next_round(state, critique)
        
        else:
            # 最大ラウンド数に到達
            await self.sse.send(trace_id, TaskEvent(
                stage="failed",
                message=f"最大ラウンド数 {self.MAX_ROUNDS} に達したため、現時点の最良結果を返します"
            ))
        
        # 最終結果の Checkpoint
        await self.checkpoint.save(Checkpoint(
            task_id=UUID(trace_id),  # = analysis_task.id（FK）。trace_id はその hex 形式
            trace_id=trace_id,  # 32-hex。ログ/Trace の紐付けに使用
            round=state.round,
            phase="completed",
            agent_state=state,
            result=state.result
        ))
        
        return state.result
    
    def _prepare_next_round(self, state: AgentState, critique: CriticResult) -> AgentState:
        """次ラウンドの状態を構築：検証済みの結論を保持し、エビデンス補完が必要な箇所をマーク"""
        # ここでは簡略化：現在の state をそのまま返す。Planner が critique.required_timestamps を読み取る
        # 実装するなら：未検証の結論を除外し、"revision_context" を構築することも可能
        return state
```

---

## 5. チェックポイントによる中断・復帰

**ストレージの階層化**（PostgreSQL が真のソース + Redis がホットキャッシュ）：

```python
class CheckpointStore:
    def __init__(self, pg_repo: CheckpointRepo, redis: Redis):
        self.pg = pg_repo
        self.redis = redis
    
    async def save(self, cp: Checkpoint):
        # 1. PostgreSQL へ永続化（真のソース）
        await self.pg.upsert(cp)
        
        # 2. Redis ホットキャッシュ（直近 10 ラウンド）
        key = f"checkpoint:{cp.task_id}:{cp.round}:{cp.phase}"
        await self.redis.setex(key, 3600, cp.model_dump_json())
        
        # 3. タスク単位のインデックスを維持
        await self.redis.lpush(f"checkpoint:index:{cp.task_id}", key)
        await self.redis.ltrim(f"checkpoint:index:{cp.task_id}", 0, 19)  # 20 件を保持
    
    async def load_latest(self, task_id: str) -> Checkpoint | None:
        # Redis を優先
        index = await self.redis.lrange(f"checkpoint:index:{task_id}", 0, 0)
        if index:
            data = await self.redis.get(index[0])
            if data:
                return Checkpoint.model_validate_json(data)
        
        # PostgreSQL にフォールバック
        return await self.pg.get_latest(task_id)
    
    async def load_round(self, task_id: str, round: int, phase: str) -> Checkpoint | None:
        key = f"checkpoint:{task_id}:{round}:{phase}"
        data = await self.redis.get(key)
        if data:
            return Checkpoint.model_validate_json(data)
        return await self.pg.get(task_id, round, phase)
```

**復帰エントリポイント**：
```python
async def resume_analysis(task_id: str) -> AnalysisResult:
    # 1. 最新の Checkpoint をロード
    cp = await checkpoint_store.load_latest(task_id)
    if not cp:
        raise ResumeError("利用可能なチェックポイントがありません")
    
    # 2. AgentState を再構築
    state = cp.agent_state
    
    # 3. 中断したフェーズから再開
    if cp.phase == "planning":
        # プランニングは完了済みのため、Executor へ直接進む
        return await agent_loop._run_from_executor(state)
    elif cp.phase == "executing":
        # 実行が中断されたため、Executor を再実行
        return await agent_loop._run_from_executor(state)
    elif cp.phase == "critic_check":
        # レビューは完了済み、結果に応じて分岐
        if cp.critique.passed:
            return state.result
        else:
            return await agent_loop._run_from_critic(state, cp.critique)
    elif cp.phase == "completed":
        return state.result
```

---

## 6. フォローアップ質問の再利用

```python
async def follow_up(media_id: UUID, previous_trace_id: str, new_goal: str) -> AnalysisResult:
    # 1. 元の VideoContext をロード（ASR/OCR のやり直しは不要）
    video_ctx = await video_context_repo.get(media_id)
    
    # 2. オプション：前ラウンドの AgentState をコンテキストとしてロード
    prev_cp = await checkpoint_store.load_latest(previous_trace_id)
    prev_conclusions = prev_cp.agent_state.result.conclusions if prev_cp else []
    
    # 3. 新しい目標を構築（前回までのまとめを含む）
    enhanced_goal = f"""
    前回ラウンドの分析結論の要約：
    {chr(10).join(f"- {c.point}" for c in prev_conclusions)}
    
    新しい目標：{new_goal}
    """
    
    # 4. 新しい trace_id で新たな AgentLoop ラウンドを実行
    new_trace_id = uuid4().hex[:32]
    return await agent_loop.run(enhanced_goal, video_ctx, new_trace_id)
```

---

## 7. 監視メトリクス

| メトリクス名 | 型 | ラベル | 用途 |
|--------|------|------|------|
| `vm_agent_loop_rounds_total` | Histogram | trace_id | ラウンド数の分布 |
| `vm_agent_loop_duration_seconds` | Histogram | trace_id, phase | 各フェーズの所要時間 |
| `vm_critic_passed_total` | Counter | trace_id, passed | 合格/不合格のカウント |
| `vm_evidence_verification_rate` | Gauge | trace_id | エビデンス検証の通過率 |
| `vm_checkpoint_save_duration_seconds` | Histogram | phase | Checkpoint 書き込み所要時間 |
| `vm_follow_up_reuse_rate` | Counter | trace_id | フォローアップ質問での Context 再利用率 |

---

> **関連ドキュメント**：[ARCHITECTURE_JP.md](ARCHITECTURE_JP.md) · [RAG-RETRIEVAL_JP.md](RAG-RETRIEVAL_JP.md) · [VIDEO-PIPELINE_JP.md](VIDEO-PIPELINE_JP.md) · [TASK-ORCHESTRATION_JP.md](TASK-ORCHESTRATION_JP.md) · [MODEL-GATEWAY_JP.md](MODEL-GATEWAY_JP.md)