# VideoMind タスクオーケストレーション設計

> Celery + Redis によるタスクオーケストレーション、ステートマシン、冪等キー、リトライ予算、SSE による段階ブロードキャスト
> 主要参考：Ragent `infra-task/`（TaskEngine + LeaseManager + IdempotencyKey）+ DOVideo-AI `CeleryWorkQueue` + vid-lens `IngestionTask`

---

## 1. タスクオーケストレーション全体像

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           FastAPI API 層                                          │
│  POST /api/v1/videos/ingest  →  create_task()  → task_id を返す                  │
└────────────────────────────────┬────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                      TaskEngine (Celery + Redis)                                  │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────┐  │
│  │ Idempotency │  │  State       │  │  Lease       │  │  RetryBudget        │  │
│  │ KeyStore    │  │  Machine     │  │  Manager     │  │  (指数バックオフ + 予算) │ │
│  └─────────────┘  └──────────────┘  └──────────────┘  └─────────────────────┘  │
└────────────────────────────────┬────────────────────────────────────────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
       ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
       │ Ingestion   │    │ Analysis    │    │ RAG         │
       │ Worker      │    │ Worker      │    │ Worker      │
       │ (GPU 直列)  │    │ (AgentLoop) │    │ (検索)      │
       └─────────────┘    └─────────────┘    └─────────────┘
              │                  │                  │
              └──────────────────┼──────────────────┘
                                 ▼
              ┌─────────────────────────────────────────┐
              │  SSE Phase Broadcaster                  │
              │  フェーズ: PENDING → DOWNLOADING →      │
              │  TRANSCODING → ASR → OCR →              │
              │  VIDEO_CONTEXT_BUILDING → INDEXING →    │
              │  ANALYZING → COMPLETED/FAILED           │
              └─────────────────────────────────────────┘
```

---

## 2. コアデータ構造

```python
class TaskType(StrEnum):
    INGESTION = "ingestion"          # 動画取り込み
    ANALYSIS = "analysis"            # AgentLoop 分析
    RAG_QUERY = "rag_query"          # RAG 検索 Q&A
    EXPORT = "export"                # 結果エクスポート


class TaskStatus(StrEnum):
    PENDING = "pending"              # キュー投入済み、スケジュール待ち
    CLAIMED = "claimed"              # Worker が取得済み（リース保持）
    RUNNING = "running"              # 実行中
    PAUSED = "paused"                # 一時停止（GPU 競合／外部依存）
    COMPLETED = "completed"          # 成功
    FAILED = "failed"                # 失敗（リトライ枯渇を含む）
    CANCELLED = "cancelled"          # ユーザーによるキャンセル


@dataclass
class Task:
    id: UUID
    type: TaskType
    status: TaskStatus = TaskStatus.PENDING
    
    # 冪等キー: content_hash + goal_hash により同一動画・同一ゴールの重複実行を防止
    idempotency_key: str
    
    # ビジネスパラメータ
    payload: dict                    # {"video_url": ..., "analysis_goal": ...}
    
    # 実行コンテキスト
    trace_id: str
    user_id: UUID
    priority: int = 5                # 1=最高, 10=最低
    
    # リトライ予算
    max_retries: int = 3
    retry_count: int = 0
    retry_budget_tokens: int = 100000
    consumed_retry_tokens: int = 0
    
    # リース
    lease_id: UUID | None = None
    lease_expires_at: datetime | None = None
    
    # 進捗
    phase: str = "PENDING"
    progress: int = 0                # 0-100
    current_step: str = ""
    
    # 結果
    result: dict | None = None
    error: str | None = None
    
    # 時刻
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
```

---

## 3. 冪等キー設計

```python
class IdempotencyKeyStore:
    """
    冪等キー = content_hash + goal_hash
    - content_hash: 動画ファイルの SHA256（ダウンロード後に計算）または URL 正規化ハッシュ
    - goal_hash: 分析ゴール JSON の正規化ハッシュ
    
    シナリオ:
    1. ユーザーが同一 URL を再送信 → 元の task_id を返す
    2. 別ユーザーが同一動画を送信 → 取り込み結果を再利用し、新規分析タスクを作成
    3. 同一動画で異なる分析ゴール → goal_hash が異なるため、新規タスクを作成
    """
    
    def __init__(self, redis: Redis, ttl: int = 86400 * 30):  # 30 日
        self.redis = redis
        self.ttl = ttl
    
    def make_key(self, content_hash: str, goal_hash: str) -> str:
        return f"idempotency:{content_hash}:{goal_hash}"
    
    def make_content_key(self, url_or_hash: str) -> str:
        # 取り込み重複排除専用
        return f"content:{url_or_hash}"
    
    async def try_acquire(self, idempotency_key: str, task_id: UUID) -> bool:
        """アトミックに取得: 存在しなければ設定して True を返す。既存なら False を返す"""
        return await self.redis.set(
            self.make_key(*idempotency_key.split(":", 1)),
            str(task_id),
            nx=True,
            ex=self.ttl
        )
    
    async def get_existing(self, idempotency_key: str) -> UUID | None:
        val = await self.redis.get(self.make_key(*idempotency_key.split(":", 1)))
        return UUID(val) if val else None
    
    async def release(self, idempotency_key: str):
        await self.redis.delete(self.make_key(*idempotency_key.split(":", 1)))
    
    @staticmethod
    def compute_content_hash(video_bytes: bytes | None, url: str) -> str:
        if video_bytes:
            return hashlib.sha256(video_bytes).hexdigest()[:32]
        # URL 正規化: クエリパラメータ除去、ドメイン統一、末尾スラッシュ除去
        normalized = normalize_video_url(url)
        return hashlib.sha256(normalized.encode()).hexdigest()[:32]
    
    @staticmethod
    def compute_goal_hash(goal: dict) -> str:
        # JSON 正規化: キーのソート、空白除去
        canonical = json.dumps(goal, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]
```

---

## 4. リース機構

```python
class LeaseManager:
    """
    Worker のハートビートによるリース更新でゾンビタスクを防止:
    - リース期間: 30s（ハートビート間隔 10s）
    - リース期限切れ → タスクは PENDING に戻り、他の Worker が取得可能
    - タスク完了／失敗 → リース解放
    """
    
    LEASE_TTL = 30      # 秒
    HEARTBEAT_INTERVAL = 10
    
    def __init__(self, redis: Redis):
        self.redis = redis
    
    async def acquire(self, task_id: UUID, worker_id: str) -> UUID | None:
        """リース取得を試み、成功なら lease_id、失敗なら None を返す"""
        lease_id = uuid4()
        key = f"lease:{task_id}"
        
        # Lua: アトミックなチェック + セット
        lua = """
        local current = redis.call("get", KEYS[1])
        if current == false then
            redis.call("setex", KEYS[1], ARGV[2], ARGV[1])
            return ARGV[1]
        end
        return false
        """
        result = await self.redis.eval(lua, 1, key, str(lease_id), self.LEASE_TTL)
        return UUID(result) if result else None
    
    async def heartbeat(self, task_id: UUID, lease_id: UUID, worker_id: str) -> bool:
        """リース更新: 保持者のみ更新可能"""
        key = f"lease:{task_id}"
        lua = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            redis.call("expire", KEYS[1], ARGV[2])
            return 1
        end
        return 0
        """
        return bool(await self.redis.eval(lua, 1, key, str(lease_id), self.LEASE_TTL))
    
    async def release(self, task_id: UUID, lease_id: UUID):
        key = f"lease:{task_id}"
        lua = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            redis.call("del", KEYS[1])
            return 1
        end
        return 0
        """
        await self.redis.eval(lua, 1, key, str(lease_id))
    
    async def force_expire(self, task_id: UUID):
        """管理者による強制解放"""
        await self.redis.delete(f"lease:{task_id}")
```

---

## 5. ステートマシンと Celery タスク定義

```python
# tasks/ingestion.py
class IngestionTask(Task):
    """動画取り込みタスク: Download → Transcode → ASR → OCR → Index"""
    
    def __init__(self):
        super().__init__()
        self.phases = [
            "DOWNLOADING",
            "TRANSCODING", 
            "ASR",
            "OCR",
            "VIDEO_CONTEXT_BUILDING",
            "INDEXING"
        ]
        self.gpu_exclusive = True  # GPU 独占が必要であることを示すフラグ
    
    async def run(self, task: Task, lease: LeaseManager, progress_cb: Callable):
        try:
            # 1. ダウンロード
            await progress_cb("DOWNLOADING", 10)
            video_path = await self.download(task.payload["video_url"])
            
            # 2. トランスコード（GPU 独占区間の開始）
            await self._acquire_gpu(task)
            await progress_cb("TRANSCODING", 25)
            segments = await self.transcode(video_path)
            
            # 3. ASR（Whisper、GPU 独占）
            await progress_cb("ASR", 45)
            transcription = await self.run_asr(segments)
            
            # 4. OCR（PaddleOCR、GPU 独占）
            await progress_cb("OCR", 65)
            ocr_results = await self.run_ocr(segments)
            
            # 5. VideoContext 構築
            await progress_cb("VIDEO_CONTEXT_BUILDING", 80)
            context = await self.build_context(transcription, ocr_results)
            
            # 6. ベクトル化 + Qdrant 書き込み + BM25
            await progress_cb("INDEXING", 95)
            await self.index(context)
            
            await progress_cb("COMPLETED", 100)
            return {"video_id": context.video_id, "segments": len(segments)}
            
        except Exception as e:
            await self._release_gpu(task)
            raise
        finally:
            await self._release_gpu(task)


# tasks/analysis.py
class AnalysisTask(Task):
    """AgentLoop 分析タスク"""
    
    def __init__(self):
        super().__init__()
        self.phases = ["PLANNING", "EXECUTING", "CRITIC_CHECK", "COMPLETED"]
    
    async def run(self, task: Task, lease: LeaseManager, progress_cb: Callable):
        agent_loop = AgentLoop()
        
        # trace_id = analysis_task.id のハイフンなし hex: checkpoint.trace_id（ログ/Trace 連結）としても使い、
        # UUID(trace_id) で checkpoint.task_id（FK → analysis_task.id）にも復元できる
        trace_id = task.id.hex
        result = await agent_loop.run(
            goal=task.payload["goal"],
            video_context=task.payload["video_context"],   # 前段の VIDEO_CONTEXT_BUILDING フェーズで生成（VIDEO-PIPELINE を参照）
            trace_id=trace_id,
        )
        return result
```

---

## 6. リトライ予算と指数バックオフ

```python
class RetryPolicy:
    """
    リトライポリシー:
    - 基本バックオフ: 2^retry_count * base_delay（base=5s）
    - 最大遅延: 300s
    - ジッター: ±25%
    - Token 予算: タスクあたり 100k tokens、リトライごとに推定 tokens を消費
    - 特定エラーはリトライしない: QUOTA_EXCEEDED, INVALID_INPUT, CANCELLED
    """
    
    BASE_DELAY = 5
    MAX_DELAY = 300
    JITTER = 0.25
    
    NON_RETRYABLE_ERRORS = {
        "QUOTA_EXCEEDED",
        "INVALID_INPUT", 
        "CANCELLED",
        "UNAUTHORIZED",
        "CONTENT_POLICY_VIOLATION"
    }
    
    def __init__(self, max_retries: int = 3, token_budget: int = 100000):
        self.max_retries = max_retries
        self.token_budget = token_budget
    
    def should_retry(self, task: Task, error: Exception) -> bool:
        if task.retry_count >= self.max_retries:
            return False
        if task.consumed_retry_tokens >= self.token_budget:
            return False
        
        error_code = getattr(error, "code", "UNKNOWN")
        if error_code in self.NON_RETRYABLE_ERRORS:
            return False
        
        return True
    
    def next_delay(self, retry_count: int) -> float:
        delay = min(self.BASE_DELAY * (2 ** retry_count), self.MAX_DELAY)
        jitter = delay * self.JITTER * (random.random() * 2 - 1)
        return delay + jitter
    
    def estimate_retry_cost(self, task: Task) -> int:
        """次回リトライの token 消費を推定"""
        if task.type == TaskType.INGESTION:
            return 2000  # ASR/OCR の再実行は比較的高コスト
        elif task.type == TaskType.ANALYSIS:
            return 5000  # AgentLoop の複数回実行
        return 1000


class TaskEngine:
    """Celery タスクエンジン入口"""
    
    def __init__(
        self,
        celery: Celery,
        redis: Redis,
        idempotency: IdempotencyKeyStore,
        lease: LeaseManager,
        retry_policy: RetryPolicy,
        sse_broadcaster: SSEBroadcaster
    ):
        self.celery = celery
        self.redis = redis
        self.idempotency = idempotency
        self.lease = lease
        self.retry = retry_policy
        self.sse = sse_broadcaster
    
    async def submit(self, task: Task) -> Task:
        # 1. 冪等チェック
        existing = await self.idempotency.get_existing(task.idempotency_key)
        if existing:
            return await self.get_task(existing)  # 既存タスクを返す
        
        # 2. アドミッション制御
        admission = await self.admission.check(task.user_id, task.type, estimated_tokens=5000)
        if not admission.allowed:
            raise AdmissionError(admission.reason)
        
        # 3. DB 登録
        await self.repo.insert(task)
        await self.idempotency.try_acquire(task.idempotency_key, task.id)
        
        # 4. Celery へディスパッチ
        self.celery.send_task(
            f"tasks.{task.type.value}",
            args=[str(task.id)],
            priority=task.priority,
            task_id=str(task.id)
        )
        
        return task
    
    @celery.task(bind=True, max_retries=3, default_retry_delay=5)
    def execute_task(self, task_id: str):
        """Celery Worker 入口"""
        asyncio.run(self._execute_async(UUID(task_id)))
    
    async def _execute_async(self, task_id: UUID):
        task = await self.repo.get(task_id)
        if not task:
            return
        
        # リース取得
        lease_id = await self.lease.acquire(task_id, f"worker-{os.getpid()}")
        if not lease_id:
            # 他の Worker が既に取得済み。後ほどリトライ
            raise self.retry(exc=LeaseAcquisitionError())
        
        task.lease_id = lease_id
        task.status = TaskStatus.CLAIMED
        task.started_at = datetime.utcnow()
        await self.repo.update(task)
        await self.sse.broadcast(task_id, "CLAIMED", 0, "Worker claimed task")
        
        try:
            # ハートビートタスク
            heartbeat_task = asyncio.create_task(self._heartbeat_loop(task_id, lease_id))
            
            # ビジネスロジック実行
            task.status = TaskStatus.RUNNING
            await self.repo.update(task)
            
            handler = self._get_handler(task.type)
            result = await handler.run(
                task,
                self.lease,
                lambda phase, progress, step="": self._on_progress(task_id, phase, progress, step)
            )
            
            heartbeat_task.cancel()
            
            # 成功
            task.status = TaskStatus.COMPLETED
            task.result = result
            task.completed_at = datetime.utcnow()
            task.progress = 100
            await self.repo.update(task)
            await self.sse.broadcast(task_id, "COMPLETED", 100, "Task completed")
            await self.lease.release(task_id, lease_id)
            
        except Exception as e:
            heartbeat_task.cancel()
            await self._handle_failure(task, e, lease_id)
    
    async def _handle_failure(self, task: Task, error: Exception, lease_id: UUID):
        if self.retry.should_retry(task, error):
            task.retry_count += 1
            task.consumed_retry_tokens += self.retry.estimate_retry_cost(task)
            task.status = TaskStatus.PENDING
            task.error = str(error)
            await self.repo.update(task)
            await self.lease.release(task.id, lease_id)
            
            # 指数バックオフで再キュー投入
            delay = self.retry.next_delay(task.retry_count)
            self.celery.send_task(
                f"tasks.{task.type.value}",
                args=[str(task.id)],
                countdown=int(delay),
                task_id=str(task.id)
            )
        else:
            task.status = TaskStatus.FAILED
            task.error = str(error)
            task.completed_at = datetime.utcnow()
            await self.repo.update(task)
            await self.sse.broadcast(task.id, "FAILED", task.progress, str(error))
            await self.lease.release(task.id, lease_id)
```

---

## 7. SSE による段階ブロードキャスト

```python
class SSEBroadcaster:
    """Server-Sent Events によるリアルタイム進捗プッシュ"""
    
    def __init__(self, redis: Redis):
        self.redis = redis
        self.channels: dict[UUID, list[asyncio.Queue]] = {}
    
    async def subscribe(self, task_id: UUID) -> AsyncIterator[SSEEvent]:
        queue = asyncio.Queue()
        if task_id not in self.channels:
            self.channels[task_id] = []
        self.channels[task_id].append(queue)
        
        try:
            # 現在の状態を送信
            task = await self.repo.get(task_id)
            if task:
                yield SSEEvent(phase=task.phase, progress=task.progress, step=task.current_step)
            
            while True:
                event = await queue.get()
                yield event
                if event.phase in ("COMPLETED", "FAILED", "CANCELLED"):
                    break
        finally:
            self.channels[task_id].remove(queue)
    
    async def broadcast(self, task_id: UUID, phase: str, progress: int, step: str):
        event = SSEEvent(
            task_id=task_id,
            phase=phase,
            progress=progress,
            step=step,
            timestamp=datetime.utcnow().isoformat()
        )
        
        # ローカルキュー
        for queue in self.channels.get(task_id, []):
            await queue.put(event)
        
        # Redis へ Publish（マルチインスタンス同期）
        await self.redis.publish(f"sse:{task_id}", event.model_dump_json())


@dataclass
class SSEEvent:
    task_id: UUID
    phase: str
    progress: int
    step: str
    timestamp: str
    
    # 標準 SSE フォーマット
    def to_sse(self) -> str:
        data = self.model_dump_json()
        return f"data: {data}\n\n"


# FastAPI SSE エンドポイント
@router.get("/tasks/{task_id}/stream")
async def task_stream(task_id: UUID, request: Request):
    async def event_generator():
        async for event in sse_broadcaster.subscribe(task_id):
            if await request.is_disconnected():
                break
            yield event.to_sse()
    
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
    )
```

---

## 8. GPU リソース調整（シングル GPU 直列実行）

```python
class GPUResourceManager:
    """シングル RTX 4060 8GB: グローバル直列ロック + VRAM の段階的解放"""
    
    def __init__(self, redis: Redis):
        self.redis = redis
        self.lock_key = "gpu:lock"
        self.lock_ttl = 3600  # 最長 1 時間保持
    
    @asynccontextmanager
    async def acquire(self, task_id: UUID, stage: str) -> AsyncIterator[GPUHandle]:
        """GPU 独占ロックを取得、ハートビートで自動延長"""
        lock_value = f"{task_id}:{stage}:{uuid4().hex[:8]}"
        
        # ロック取得を待機（最長 30min）
        acquired = False
        for _ in range(180):  # 10s * 180 = 30min
            acquired = await self.redis.set(self.lock_key, lock_value, nx=True, ex=30)
            if acquired:
                break
            await asyncio.sleep(10)
        
        if not acquired:
            raise GPUAcquisitionTimeoutError("GPU busy for 30 minutes")
        
        handle = GPUHandle(self.redis, self.lock_key, lock_value, task_id, stage)
        
        try:
            yield handle
        finally:
            await handle.release()
    
    async def get_status(self) -> GPUStatus:
        """現在の保持者、ステージ、待機キュー長"""
        holder = await self.redis.get(self.lock_key)
        waiting = await self.redis.llen("gpu:waiting")
        return GPUStatus(
            is_busy=bool(holder),
            holder=holder,
            waiting_count=waiting
        )


class GPUHandle:
    """GPU 保持ハンドル: ハートビート + VRAM ステージマーカー"""
    
    def __init__(self, redis: Redis, lock_key: str, lock_value: str, task_id: UUID, stage: str):
        self.redis = redis
        self.lock_key = lock_key
        self.lock_value = lock_value
        self.task_id = task_id
        self.stage = stage
        self.heartbeat_task: asyncio.Task | None = None
    
    async def __aenter__(self):
        self.heartbeat_task = asyncio.create_task(self._heartbeat())
        await self._update_stage(self.stage)
        return self
    
    async def __aexit__(self, *args):
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
        await self.release()
    
    async def _heartbeat(self):
        while True:
            await asyncio.sleep(10)
            # Lua: 保持者のみ延長可能
            lua = "if redis.call('get', KEYS[1]) == ARGV[1] then redis.call('expire', KEYS[1], 30) return 1 end return 0"
            await self.redis.eval(lua, 1, self.lock_key, self.lock_value)
    
    async def update_stage(self, stage: str):
        self.stage = stage
        await self._update_stage(stage)
    
    async def _update_stage(self, stage: str):
        await self.redis.hset("gpu:status", mapping={
            "stage": stage,
            "task_id": str(self.task_id),
            "updated_at": datetime.utcnow().isoformat()
        })
    
    async def release(self):
        lua = "if redis.call('get', KEYS[1]) == ARGV[1] then redis.call('del', KEYS[1]) return 1 end return 0"
        await self.redis.eval(lua, 1, self.lock_key, self.lock_value)
        await self.redis.hdel("gpu:status", "stage", "task_id")
```

---

## 9. 設定

```yaml
# config/task_orchestration.yaml
task_engine:
  celery:
    broker_url: "redis://localhost:6379/1"
    result_backend: "redis://localhost:6379/2"
    task_serializer: "json"
    result_serializer: "json"
    worker_prefetch_multiplier: 1  # GPU タスクはプリフェッチしない
    task_acks_late: true
    worker_max_tasks_per_child: 10  # メモリリーク防止
  
  idempotency:
    ttl_days: 30
  
  lease:
    ttl_seconds: 30
    heartbeat_interval_seconds: 10
  
  retry:
    max_retries: 3
    base_delay_seconds: 5
    max_delay_seconds: 300
    jitter: 0.25
    token_budget: 100000
  
  gpu:
    lock_ttl_seconds: 3600
    acquisition_timeout_seconds: 1800
  
  sse:
    heartbeat_interval_seconds: 15
    max_connection_duration_seconds: 3600
```

---

## 10. 監視メトリクス

| メトリクス名 | 型 | ラベル | 用途 |
|--------|------|------|------|
| `vm_task_submitted_total` | Counter | type, priority | タスク送信数 |
| `vm_task_duration_seconds` | Histogram | type, status | タスクのエンドツーエンド所要時間 |
| `vm_task_retries_total` | Counter | type, error_code | リトライ回数 |
| `vm_lease_acquired_total` | Counter | worker_id | リース取得成功 |
| `vm_lease_expired_total` | Counter | task_id | リース期限切れ（ハートビート喪失） |
| `vm_gpu_lock_wait_seconds` | Histogram | stage | GPU 待機時間 |
| `vm_gpu_utilization` | Gauge | stage | GPU 占有率（0/1） |
| `vm_sse_connections_active` | Gauge | | アクティブな SSE 接続数 |
| `vm_admission_rejected_total` | Counter | reason | アドミッション拒否 |

---

> **関連ドキュメント**：[ARCHITECTURE_JP.md](ARCHITECTURE_JP.md) · [VIDEO-PIPELINE_JP.md](VIDEO-PIPELINE_JP.md) · [AGENT-LOOP_JP.md](AGENT-LOOP_JP.md) · [OBSERVABILITY_JP.md](OBSERVABILITY_JP.md)
