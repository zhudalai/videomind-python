# VideoMind 任务编排设计

> Celery + Redis 任务编排、状态机、幂等键、重试预算、SSE 阶段广播
> 核心参考：Ragent `infra-task/` (TaskEngine + LeaseManager + IdempotencyKey) + DOVideo-AI `CeleryWorkQueue` + vid-lens `IngestionTask`

---

## 1. 任务编排总览

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                           FastAPI API 层                                         │
│  POST /api/v1/videos/ingest  →  create_task()  → 返回 task_id                  │
└────────────────────────────────┬────────────────────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│                      TaskEngine (Celery + Redis)                                │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐  ┌─────────────────────┐  │
│  │ Idempotency │  │  State       │  │  Lease       │  │  RetryBudget        │  │
│  │ KeyStore    │  │  Machine     │  │  Manager     │  │  (指数退避 + 预算)   │  │
│  └─────────────┘  └──────────────┘  └──────────────┘  └─────────────────────┘  │
└────────────────────────────────┬────────────────────────────────────────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
       ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
       │ Ingestion   │    │ Analysis    │    │ RAG         │
       │ Worker      │    │ Worker      │    │ Worker      │
       │ (GPU 串行)  │    │ (AgentLoop) │    │ (检索)       │
       └─────────────┘    └─────────────┘    └─────────────┘
              │                  │                  │
              └──────────────────┼──────────────────┘
                                 ▼
              ┌─────────────────────────────────────────┐
              │  SSE Phase Broadcaster                  │
              │  阶段: PENDING → DOWNLOADING →          │
              │  TRANSCODING → ASR → OCR →              │
              │  VIDEO_CONTEXT_BUILDING → INDEXING →  │
              │  ANALYZING → COMPLETED/FAILED│
              └─────────────────────────────────────────┘
```

---

## 2. 核心数据结构

```python
class TaskType(StrEnum):
    INGESTION = "ingestion"          # 视频入库
    ANALYSIS = "analysis"            # AgentLoop 分析
    RAG_QUERY = "rag_query"          # RAG 检索问答
    EXPORT = "export"                # 结果导出


class TaskStatus(StrEnum):
    PENDING = "pending"              # 已入队，等待调度
    CLAIMED = "claimed"              # Worker 已领取（持有租约）
    RUNNING = "running"              # 实际执行中
    PAUSED = "paused"                # 暂停（GPU 冲突/外部依赖）
    COMPLETED = "completed"          # 成功
    FAILED = "failed"                # 失败（含重试耗尽）
    CANCELLED = "cancelled"          # 用户取消


@dataclass
class Task:
    id: UUID
    type: TaskType
    status: TaskStatus = TaskStatus.PENDING
    
    # 幂等键：content_hash + goal_hash 确保同一视频同一目标不重复执行
    idempotency_key: str
    
    # 业务参数
    payload: dict                    # {"video_url": ..., "analysis_goal": ...}
    
    # 执行上下文
    trace_id: str
    user_id: UUID
    priority: int = 5                # 1=最高, 10=最低
    
    # 重试预算
    max_retries: int = 3
    retry_count: int = 0
    retry_budget_tokens: int = 100000
    consumed_retry_tokens: int = 0
    
    # 租约
    lease_id: UUID | None = None
    lease_expires_at: datetime | None = None
    
    # 进度
    phase: str = "PENDING"
    progress: int = 0                # 0-100
    current_step: str = ""
    
    # 结果
    result: dict | None = None
    error: str | None = None
    
    # 时间
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
```

---

## 3. 幂等键设计

```python
class IdempotencyKeyStore:
    """
    幂等键 = content_hash + goal_hash
    - content_hash: 视频文件 SHA256（下载后计算）或 URL 规范化哈希
    - goal_hash: 分析目标 JSON 规范化哈希
    
    场景：
    1. 用户重复提交同一 URL → 返回原 task_id
    2. 不同用户提交同一视频 → 复用入库结果，创建新分析任务
    3. 同一视频不同分析目标 → 不同 goal_hash，创建新任务
    """
    
    def __init__(self, redis: Redis, ttl: int = 86400 * 30):  # 30 天
        self.redis = redis
        self.ttl = ttl
    
    def make_key(self, content_hash: str, goal_hash: str) -> str:
        return f"idempotency:{content_hash}:{goal_hash}"
    
    def make_content_key(self, url_or_hash: str) -> str:
        # 仅用于入库去重
        return f"content:{url_or_hash}"
    
    async def try_acquire(self, idempotency_key: str, task_id: UUID) -> bool:
        """原子性获取：不存在则设置，返回 True；已存在返回 False"""
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
        # URL 规范化：去查询参数、统一域名、去尾部斜杠
        normalized = normalize_video_url(url)
        return hashlib.sha256(normalized.encode()).hexdigest()[:32]
    
    @staticmethod
    def compute_goal_hash(goal: dict) -> str:
        # 规范化 JSON：排序键、去空格
        canonical = json.dumps(goal, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]
```

---

## 4. 租约机制

```python
class LeaseManager:
    """
    Worker 心跳续租，防止僵尸任务：
    - 租约时长：30s（心跳间隔 10s）
    - 租约到期 → 任务回 PENDING，其他 Worker 可领取
    - 任务完成/失败 → 释放租约
    """
    
    LEASE_TTL = 30      # 秒
    HEARTBEAT_INTERVAL = 10
    
    def __init__(self, redis: Redis):
        self.redis = redis
    
    async def acquire(self, task_id: UUID, worker_id: str) -> UUID | None:
        """尝试获取租约，成功返回 lease_id，失败返回 None"""
        lease_id = uuid4()
        key = f"lease:{task_id}"
        
        # Lua: 原子检查 + 设置
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
        """续租：仅持有者可续"""
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
        """管理员强制释放"""
        await self.redis.delete(f"lease:{task_id}")
```

---

## 5. 状态机与 Celery 任务定义

```python
# tasks/ingestion.py
class IngestionTask(Task):
    """视频入库任务：Download → Transcode → ASR → OCR → Index"""
    
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
        self.gpu_exclusive = True  # 标记需要 GPU 独占
    
    async def run(self, task: Task, lease: LeaseManager, progress_cb: Callable):
        try:
            # 1. 下载
            await progress_cb("DOWNLOADING", 10)
            video_path = await self.download(task.payload["video_url"])
            
            # 2. 转码（GPU 独占段开始）
            await self._acquire_gpu(task)
            await progress_cb("TRANSCODING", 25)
            segments = await self.transcode(video_path)
            
            # 3. ASR（Whisper，GPU 独占）
            await progress_cb("ASR", 45)
            transcription = await self.run_asr(segments)
            
            # 4. OCR（PaddleOCR，GPU 独占）
            await progress_cb("OCR", 65)
            ocr_results = await self.run_ocr(segments)
            
            # 5. 构建 VideoContext
            await progress_cb("VIDEO_CONTEXT_BUILDING", 80)
            context = await self.build_context(transcription, ocr_results)
            
            # 6. 向量化 + 写入 Qdrant + BM25
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
    """AgentLoop 分析任务"""
    
    def __init__(self):
        super().__init__()
        self.phases = ["PLANNING", "EXECUTING", "CRITIC_CHECK", "COMPLETED"]
    
    async def run(self, task: Task, lease: LeaseManager, progress_cb: Callable):
        agent_loop = AgentLoop()
        
        # trace_id = analysis_task.id 的无连字符 hex：既作 checkpoint.trace_id（日志/Trace 串联），
        # 又可经 UUID(trace_id) 复原为 checkpoint.task_id（FK → analysis_task.id）
        trace_id = task.id.hex
        result = await agent_loop.run(
            goal=task.payload["goal"],
            video_context=task.payload["video_context"],   # 由前置 VIDEO_CONTEXT_BUILDING 阶段产出（见 VIDEO-PIPELINE）
            trace_id=trace_id,
        )
        return result
```

---

## 6. 重试预算与指数退避

```python
class RetryPolicy:
    """
    重试策略：
    - 基础退避：2^retry_count * base_delay（base=5s）
    - 最大延迟：300s
    - 抖动：±25%
    - Token 预算：每任务 100k tokens，每次重试消耗估算 tokens
    - 特定错误不重试：QUOTA_EXCEEDED, INVALID_INPUT, CANCELLED
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
        """估算下一次重试的 token 消耗"""
        if task.type == TaskType.INGESTION:
            return 2000  # 重新跑 ASR/OCR 较贵
        elif task.type == TaskType.ANALYSIS:
            return 5000  # AgentLoop 多轮
        return 1000


class TaskEngine:
    """Celery 任务引擎入口"""
    
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
        # 1. 幂等检查
        existing = await self.idempotency.get_existing(task.idempotency_key)
        if existing:
            return await self.get_task(existing)  # 返回已有任务
        
        # 2. 准入控制
        admission = await self.admission.check(task.user_id, task.type, estimated_tokens=5000)
        if not admission.allowed:
            raise AdmissionError(admission.reason)
        
        # 3. 入库
        await self.repo.insert(task)
        await self.idempotency.try_acquire(task.idempotency_key, task.id)
        
        # 4. 分发到 Celery
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
        
        # 领取租约
        lease_id = await self.lease.acquire(task_id, f"worker-{os.getpid()}")
        if not lease_id:
            # 已被其他 Worker 领取，稍后重试
            raise self.retry(exc=LeaseAcquisitionError())
        
        task.lease_id = lease_id
        task.status = TaskStatus.CLAIMED
        task.started_at = datetime.utcnow()
        await self.repo.update(task)
        await self.sse.broadcast(task_id, "CLAIMED", 0, "Worker claimed task")
        
        try:
            # 心跳任务
            heartbeat_task = asyncio.create_task(self._heartbeat_loop(task_id, lease_id))
            
            # 执行业务逻辑
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
            
            # 指数退避重新入队
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

## 7. SSE 阶段广播

```python
class SSEBroadcaster:
    """Server-Sent Events 实时进度推送"""
    
    def __init__(self, redis: Redis):
        self.redis = redis
        self.channels: dict[UUID, list[asyncio.Queue]] = {}
    
    async def subscribe(self, task_id: UUID) -> AsyncIterator[SSEEvent]:
        queue = asyncio.Queue()
        if task_id not in self.channels:
            self.channels[task_id] = []
        self.channels[task_id].append(queue)
        
        try:
            # 发送当前状态
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
        
        # 本地队列
        for queue in self.channels.get(task_id, []):
            await queue.put(event)
        
        # Redis 发布（多实例同步）
        await self.redis.publish(f"sse:{task_id}", event.model_dump_json())


@dataclass
class SSEEvent:
    task_id: UUID
    phase: str
    progress: int
    step: str
    timestamp: str
    
    # 标准 SSE 格式
    def to_sse(self) -> str:
        data = self.model_dump_json()
        return f"data: {data}\n\n"


# FastAPI SSE 端点
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

## 8. GPU 资源协调（单卡串行）

```python
class GPUResourceManager:
    """单卡 RTX 4060 8GB：全局串行锁 + 显存分阶段释放"""
    
    def __init__(self, redis: Redis):
        self.redis = redis
        self.lock_key = "gpu:lock"
        self.lock_ttl = 3600  # 1 小时最大持有
    
    @asynccontextmanager
    async def acquire(self, task_id: UUID, stage: str) -> AsyncIterator[GPUHandle]:
        """获取 GPU 独占锁，自动心跳续期"""
        lock_value = f"{task_id}:{stage}:{uuid4().hex[:8]}"
        
        # 等待获取锁（最长等 30min）
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
        """当前持有者、阶段、等待队列长度"""
        holder = await self.redis.get(self.lock_key)
        waiting = await self.redis.llen("gpu:waiting")
        return GPUStatus(
            is_busy=bool(holder),
            holder=holder,
            waiting_count=waiting
        )


class GPUHandle:
    """GPU 持有句柄：心跳 + 显存阶段标记"""
    
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
            # Lua: 仅持有者可续期
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

## 9. 配置

```yaml
# config/task_orchestration.yaml
task_engine:
  celery:
    broker_url: "redis://localhost:6379/1"
    result_backend: "redis://localhost:6379/2"
    task_serializer: "json"
    result_serializer: "json"
    worker_prefetch_multiplier: 1  # GPU 任务不预取
    task_acks_late: true
    worker_max_tasks_per_child: 10  # 防内存泄漏
  
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

## 10. 监控指标

| 指标名 | 类型 | 标签 | 用途 |
|--------|------|------|------|
| `vm_task_submitted_total` | Counter | type, priority | 任务提交计数 |
| `vm_task_duration_seconds` | Histogram | type, status | 任务端到端耗时 |
| `vm_task_retries_total` | Counter | type, error_code | 重试次数 |
| `vm_lease_acquired_total` | Counter | worker_id | 租约获取成功 |
| `vm_lease_expired_total` | Counter | task_id | 租约过期（心跳丢失） |
| `vm_gpu_lock_wait_seconds` | Histogram | stage | GPU 等待时间 |
| `vm_gpu_utilization` | Gauge | stage | GPU 占用率（0/1） |
| `vm_sse_connections_active` | Gauge | | 活跃 SSE 连接数 |
| `vm_admission_rejected_total` | Counter | reason | 准入拒绝 |

---

> **关联文档**：[ARCHITECTURE.md](ARCHITECTURE.md) · [VIDEO-PIPELINE.md](VIDEO-PIPELINE.md) · [AGENT-LOOP.md](AGENT-LOOP.md) · [OBSERVABILITY.md](OBSERVABILITY.md)