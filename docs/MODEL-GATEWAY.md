# VideoMind 模型网关设计

> 三态熔断 + 优先级路由 + 首包探测 + Token 计费 + 多供应商抽象
> 核心参考：Ragent `infra-ai/` (RoutingLLMService + ModelHealthStore + LlmFirstPacketProbe + Token 计费) + vid-lens `internal/ai/` (Factory + Observed 装饰器 + Admission)

---

## 1. 模型网关总览

```
业务层 (AgentLoop / RAG / 视频管线)
                │
                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      LLMService (统一接口)                        │
│  chat() / stream_chat() / embed() / rerank()                    │
└────────────────────────────────┬────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                     ModelRouter (路由层)                          │
│  1. 按任务类型选候选 (thinking/normal/fast)                       │
│  2. 按优先级排序 (primary → fallback)                            │
│  3. 熔断过滤 (跳过 OPEN 状态模型)                                 │
└────────────────────────────────┬────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│            ModelHealthStore (三态熔断器状态机)                     │
│  ┌──────────┐  failureThreshold   ┌──────────┐  openDuration  ┌──────────┐ │
│  │  CLOSED  │───────────────────▶│   OPEN   │──────────────▶│HALF_OPEN │ │
│  │ (正常)   │                    │ (熔断)   │  探测成功       │ (半开)   │ │
│  └──────────┘                    └──────────┘◀──────────────└──────────┘ │
└────────────────────────────────┬────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│               LlmFirstPacketProbe (首包探测)                     │
│  启动流式 → 60s 内等首 token → 成功转发 / 失败标记 + 换下一个       │
└────────────────────────────────┬────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                  Provider 实现层 (多供应商)                       │
│  ┌─────────┐ ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐  │
│  │ Ollama  │ │ OpenAI  │ │Anthropic │ │ DeepSeek │ │ Custom │  │
│  │Provider │ │Provider │ │ Provider │ │ Provider │ │Provider│  │
│  └─────────┘ └─────────┘ └──────────┘ └──────────┘ └────────┘  │
└──────────────────────────────────┬──────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│            Observed 装饰器 + TokenAccounting (观测层)            │
│  记录: 耗时 / token / 成本 / 错误码 → ai_call_logs 表 + Prometheus│
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 统一接口抽象

```python
class LLMService(Protocol):
    """业务层唯一依赖的接口，屏蔽所有供应商差异"""
    
    async def chat(self, request: ChatRequest) -> ChatResponse: ...
    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatChunk]: ...
    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]: ...
    async def rerank(self, query: str, documents: list[str]) -> list[RerankResult]: ...


@dataclass
class ChatRequest:
    messages: list[dict]              # [{"role":, "content":}]
    model: str | None = None          # 覆盖默认
    temperature: float = 0.3
    max_tokens: int = 4096
    thinking: bool = False            # 是否需要思考模式
    response_format: dict | None = None  # {"type": "json_object"}
    stream: bool = False
    user_id: UUID | None = None      # 配额归属
    trace_id: str = field(default_factory=lambda: uuid4().hex[:32])
    timeout: float = 120.0


@dataclass
class ChatResponse:
    content: str
    model: str
    usage: TokenUsage
    finish_reason: str
    latency_ms: int
    provider: str


@dataclass
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_usd: float = 0.0
```

---

## 3. 三态熔断器（ModelHealthStore）

```python
class CircuitState(StrEnum):
    CLOSED = "closed"       # 正常放行
    OPEN = "open"            # 熔断，拒绝请求
    HALF_OPEN = "half_open" # 探测期，限量放行


class ModelHealthStore:
    """
    状态机：
    CLOSED ──(连续失败 ≥ threshold)──▶ OPEN
    OPEN   ──(经过 openDuration)──▶ HALF_OPEN
    HALF_OPEN ──(探测成功)──▶ CLOSED
    HALF_OPEN ──(探测失败)──▶ OPEN (重置计时)
    """
    
    def __init__(self, redis: Redis):
        self.redis = redis
        self.failure_threshold = 3       # 连续失败 3 次熔断
        self.open_duration_ms = 30000    # 熔断 30s 后探测
        self.half_open_max_calls = 2     # 半开期最多 2 个探测
    
    async def allow_call(self, model_id: str) -> bool:
        state = await self._get_state(model_id)
        if state == CircuitState.CLOSED:
            return True
        if state == CircuitState.OPEN:
            return False
        # HALF_OPEN：限量放行
        if await self._try_acquire_probe_slot(model_id):
            return True
        return False
    
    async def mark_success(self, model_id: str):
        # 任意成功 → CLOSED，清零计数
        await self.redis.hset(f"health:{model_id}", mapping={
            "state": CircuitState.CLOSED.value,
            "failures": 0,
            "opened_at": ""
        })
        await self.redis.delete(f"probe_slots:{model_id}")
    
    async def mark_failure(self, model_id: str):
        failures = await self.redis.hincrby(f"health:{model_id}", "failures", 1)
        if failures >= self.failure_threshold:
            await self._transition_to_open(model_id)
        elif await self._get_state(model_id) == CircuitState.HALF_OPEN:
            # 半开期失败直接回 OPEN
            await self._transition_to_open(model_id)
    
    async def _get_state(self, model_id: str) -> CircuitState:
        data = await self.redis.hgetall(f"health:{model_id}")
        if not data:
            return CircuitState.CLOSED
        
        state = data.get("state", CircuitState.CLOSED.value)
        if state == CircuitState.OPEN.value:
            # 检查是否到探测时间
            opened_at = int(data.get("opened_at", 0))
            if (time.time() * 1000) - opened_at >= self.open_duration_ms:
                await self._transition_to_half_open(model_id)
                return CircuitState.HALF_OPEN
            return CircuitState.OPEN
        return CircuitState(state)
    
    async def _transition_to_open(self, model_id: str):
        await self.redis.hset(f"health:{model_id}", mapping={
            "state": CircuitState.OPEN.value,
            "opened_at": int(time.time() * 1000),
            "failures": 0
        })
        await self.redis.delete(f"probe_slots:{model_id}")
        logger.warning(f"Circuit OPEN for model {model_id}")
    
    async def _transition_to_half_open(self, model_id: str):
        await self.redis.hset(f"health:{model_id}", "state", CircuitState.HALF_OPEN.value)
        logger.info(f"Circuit HALF_OPEN for model {model_id}")
    
    async def _try_acquire_probe_slot(self, model_id: str) -> bool:
        # Lua 原子操作：剩余探测槽 -1
        lua = """
        local current = tonumber(redis.call("get", KEYS[1]) or "0")
        if current < tonumber(ARGV[1]) then
            redis.call("incr", KEYS[1])
            return 1
        end
        return 0
        """
        return bool(await self.redis.eval(lua, 1, f"probe_slots:{model_id}", self.half_open_max_calls))
```

---

## 4. 首包探测（LlmFirstPacketProbe）

```python
class LlmFirstPacketProbe:
    """启动流式后 60s 内等首 token，超时则判定失败"""
    
    FIRST_PACKET_TIMEOUT = 60.0
    
    async def await_first_packet(self, bridge: ProbeStreamBridge) -> ProbeResult:
        try:
            async with asyncio.timeout(self.FIRST_PACKET_TIMEOUT):
                await bridge.first_packet_event.wait()
                return ProbeResult(success=True, first_packet_latency_ms=bridge.get_latency_ms())
        except TimeoutError:
            return ProbeResult(success=False, error="first_packet_timeout")
        except Exception as e:
            return ProbeResult(success=False, error=str(e))


class ProbeStreamBridge:
    """装饰器：包装真实流式回调，首个 chunk 触发事件"""
    
    def __init__(self, callback: AsyncCallable):
        self.callback = callback
        self.first_packet_event = asyncio.Event()
        self.start_time = time.time()
        self.buffered_chunks: list[ChatChunk] = []
    
    async def on_chunk(self, chunk: ChatChunk):
        if not self.first_packet_event.is_set():
            self.first_packet_event.set()
        # 透传给真实回调
        await self.callback(chunk)
    
    def get_latency_ms(self) -> int:
        return int((time.time() - self.start_time) * 1000)
    
    def cancel(self):
        self.first_packet_event = None
```

---

## 5. 路由与编排（RoutingLLMService）

```python
class RoutingLLMService:
    def __init__(
        self,
        providers: dict[str, LLMProvider],
        health_store: ModelHealthStore,
        probe: LlmFirstPacketProbe,
        token_accounting: TokenAccounting
    ):
        self.providers = providers
        self.health = health_store
        self.probe = probe
        self.accounting = token_accounting
        # 按任务类型的优先级降级链（配置驱动）
        self.routing = {
            "thinking": ["anthropic:claude", "ollama:qwen2.5-7b", "deepseek:deepseek-chat"],
            "normal":   ["ollama:qwen2.5-7b", "deepseek:deepseek-chat", "openai:gpt-4o-mini"],
            "fast":     ["ollama:qwen2.5-7b:q4", "openai:gpt-4o-mini"],
            "embedding": ["ollama:bge-m3", "openai:text-embedding-3-small"],
            "rerank":   ["ollama:bge-reranker-v2-m3"],
        }
    
    async def chat(self, request: ChatRequest) -> ChatResponse:
        task_type = "thinking" if request.thinking else "normal"
        candidates = self.routing[task_type]
        
        for model_id in candidates:
            if not await self.health.allow_call(model_id):
                continue
            
            provider = self._get_provider(model_id)
            try:
                start = time.time()
                response = await provider.chat(request)
                response.latency_ms = int((time.time() - start) * 1000)
                response.provider = model_id
                
                await self.health.mark_success(model_id)
                await self.accounting.record(request, response, model_id)
                return response
            except Exception as e:
                logger.warning(f"Model {model_id} failed: {e}")
                await self.health.mark_failure(model_id)
                continue
        
        raise AllProvidersFailedError(f"All candidates failed for task {task_type}")
    
    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatChunk]:
        task_type = "thinking" if request.thinking else "normal"
        candidates = self.routing[task_type]
        
        for model_id in candidates:
            if not await self.health.allow_call(model_id):
                continue
            
            provider = self._get_provider(model_id)
            bridge = ProbeStreamBridge_callback_holder()
            
            # 启动真实流式（后台任务）
            stream_task = asyncio.create_task(
                self._consume_stream(provider, request, model_id, bridge)
            )
            
            # 首包探测
            probe_result = await self.probe.await_first_packet(bridge)
            
            if probe_result.success:
                await self.health.mark_success(model_id)
                # 回放缓冲 + 继续透传
                async for chunk in bridge.replay_and_continue():
                    yield chunk
                await stream_task  # 等待完成，记录 token
                return
            else:
                await self.health.mark_failure(model_id)
                stream_task.cancel()
                logger.warning(f"First packet failed for {model_id}: {probe_result.error}")
                continue
        
        raise AllProvidersFailedError(f"Stream failed for all candidates")
```

---

## 6. 多供应商 Provider 实现

```python
class LLMProvider(Protocol):
    name: str
    async def chat(self, request: ChatRequest) -> ChatResponse: ...
    async def stream_chat(self, request: ChatRequest, bridge: ProbeStreamBridge) -> None: ...
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
    def estimate_cost(self, usage: TokenUsage) -> float: ...


class OllamaProvider:
    name = "ollama"
    # 本地优先：零费用、低延迟、数据不出域
    
    def __init__(self, base_url: str = "http://localhost:11434"):
        self.client = ollama.AsyncClient(host=base_url)
    
    async def chat(self, request: ChatRequest) -> ChatResponse:
        model = request.model or "qwen2.5:7b"
        response = await self.client.chat(
            model=model,
            messages=request.messages,
            options={
                "temperature": request.temperature,
                "num_predict": request.max_tokens,
                "num_ctx": 4096,
            },
            format=request.response_format.get("type") if request.response_format else None
        )
        return ChatResponse(
            content=response["message"]["content"],
            model=model,
            usage=TokenUsage(
                prompt_tokens=response.get("prompt_eval_count", 0),
                completion_tokens=response.get("eval_count", 0),
                total_tokens=response.get("prompt_eval_count", 0) + response.get("eval_count", 0),
                cost_usd=0.0  # 本地零成本
            ),
            finish_reason=response.get("done_reason", "stop"),
            latency_ms=0,
            provider=self.name
        )
    
    def estimate_cost(self, usage: TokenUsage) -> float:
        return 0.0  # 本地 LLM 零成本


class OpenAIProvider:
    name = "openai"
    PRICING = {  # USD / 1M tokens
        "gpt-4o": {"prompt": 2.5, "completion": 10.0},
        "gpt-4o-mini": {"prompt": 0.15, "completion": 0.6},
    }
    
    def __init__(self, api_key: str, base_url: str | None = None):
        self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
    
    async def chat(self, request: ChatRequest) -> ChatResponse:
        response = await self.client.chat.completions.create(
            model=request.model or "gpt-4o-mini",
            messages=request.messages,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            response_format=request.response_format,
            stream=False
        )
        choice = response.choices[0]
        usage = TokenUsage(
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            total_tokens=response.usage.total_tokens,
        )
        usage.cost_usd = self.estimate_cost(usage) * (response.usage.total_tokens / 1_000_000)
        return ChatResponse(
            content=choice.message.content,
            model=response.model,
            usage=usage,
            finish_reason=choice.finish_reason,
            latency_ms=0,
            provider=self.name
        )
    
    def estimate_cost(self, usage: TokenUsage) -> float:
        rate = self.PRICING.get("gpt-4o-mini", {"prompt": 0.15, "completion": 0.6})
        return (usage.prompt_tokens * rate["prompt"] + usage.completion_tokens * rate["completion"])


class DeepSeekProvider:
    name = "deepseek"
    # OpenAI 兼容接口
    def __init__(self, api_key: str):
        self.client = AsyncOpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    # ... 实现同 OpenAI，仅 base_url 与 pricing 不同
```

---

## 7. Token 计费与配额（TokenAccounting + Admission）

```python
class TokenAccounting:
    async def record(self, request: ChatRequest, response: ChatResponse, model_id: str):
        log = AICallLog(
            id=uuid4(),
            user_id=request.user_id,
            trace_id=request.trace_id,
            provider=model_id,
            model=response.model,
            prompt_tokens=response.usage.prompt_tokens,
            completion_tokens=response.usage.completion_tokens,
            total_tokens=response.usage.total_tokens,
            cost_usd=response.usage.cost_usd,
            latency_ms=response.latency_ms,
            status="success",
            created_at=datetime.utcnow()
        )
        await self.repo.insert(log)
        
        # Prometheus 指标
        ai_tokens_total.labels(provider=model_id, type="prompt").inc(response.usage.prompt_tokens)
        ai_tokens_total.labels(provider=model_id, type="completion").inc(response.usage.completion_tokens)
        ai_cost_usd_total.labels(provider=model_id).inc(response.usage.cost_usd)


class Admission:
    """准入控制：令牌桶限流 + 配额账本 + 重试预算"""
    
    async def check(self, user_id: UUID, model_id: str, estimated_tokens: int) -> AdmissionResult:
        # 1. 令牌桶：每用户每模型速率限制
        allowed = await self.token_bucket.acquire(
            key=f"rate:{user_id}:{model_id}",
            capacity=100,  # 桶容量
            refill_rate=10  # 每 10s / token
        )
        if not allowed:
            return AdmissionResult(allowed=False, reason="rate_limited")
        
        # 2. 月度配额：视频分钟数 / Token 数
        quota = await self.quota_repo.get(user_id)
        if quota.used_tokens + estimated_tokens > quota.max_tokens:
            return AdmissionResult(allowed=False, reason="quota_exceeded")
        
        # 3. 重试预算：关联分析任务
        if request.trace_id:
            budget = await self.budget_repo.get(request.trace_id)
            if budget.consumed + estimated_tokens > budget.max_tokens:
                return AdmissionResult(allowed=False, reason="retry_budget_exhausted")
        
        return AdmissionResult(allowed=True)
```

---

## 8. 配置与降级链

```yaml
# config/model_gateway.yaml
gateway:
  providers:
    ollama:
      base_url: "http://localhost:11434"
      models:
        chat: "qwen2.5:7b"
        chat_fast: "qwen2.5:7b-instruct-q4_K_M"
        embedding: "bge-m3"
        rerank: "bge-reranker-v2-m3"
    
    openai:
      api_key: "${OPENAI_API_KEY}"
      models:
        chat: "gpt-4o-mini"
    
    deepseek:
      api_key: "${DEEPSEEK_API_KEY}"
      base_url: "https://api.deepseek.com"
    
    anthropic:
      api_key: "${ANTHROPIC_API_KEY}"
      models:
        chat: "claude-sonnet-4-5"
  
  routing:
    thinking: ["anthropic:claude", "ollama:qwen2.5:7b", "deepseek:deepseek-chat"]
    normal: ["ollama:qwen2.5:7b", "deepseek:deepseek-chat"]
    fast: ["ollama:qwen2.5:7b-instruct-q4_K_M"]
    embedding: ["ollama:bge-m3"]
    rerank: ["ollama:bge-reranker-v2-m3"]
  
  circuit_breaker:
    failure_threshold: 3
    open_duration_ms: 30000
    half_open_max_calls: 2
  
  first_packet:
    timeout_seconds: 60
  
  admission:
    rate_limit:
      capacity: 100
      refill_rate_per_sec: 10
    monthly_quota:
      free: 50000
      pro: 500000
      enterprise: 5000000
```

---

## 9. 监控指标

| 指标名 | 类型 | 标签 | 用途 |
|--------|------|------|------|
| `vm_llm_request_total` | Counter | provider, model, status, task_type | 请求计数 |
| `vm_llm_duration_seconds` | Histogram | provider, model | 端到端耗时 |
| `vm_llm_first_packet_seconds` | Histogram | provider, model | 首包延迟 |
| `vm_llm_tokens_total` | Counter | provider, type(prompt/completion) | Token 消耗 |
| `vm_llm_cost_usd_total` | Counter | provider | 成本累计 |
| `vm_circuit_state` | Gauge | model_id | 当前熔断状态 (0=closed,1=open,2=half_open) |
| `vm_circuit_transitions_total` | Counter | model_id, from, to | 状态转换次数 |
| `vm_admission_rejected_total` | Counter | reason | 准入拒绝原因 |

---

> **关联文档**：[ARCHITECTURE.md](ARCHITECTURE.md) · [RAG-RETRIEVAL.md](RAG-RETRIEVAL.md) · [AGENT-LOOP.md](AGENT-LOOP.md) · [OBSERVABILITY.md](OBSERVABILITY.md)