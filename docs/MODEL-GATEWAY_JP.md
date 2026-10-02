# VideoMind モデルゲートウェイ設計

> 三状態サーキットブレーカー + 優先度ルーティング + 初回応答プローブ + トークン課金 + 複数プロバイダ抽象
> 主要参考：Ragent `infra-ai/` (RoutingLLMService + ModelHealthStore + LlmFirstPacketProbe + トークン課金) + vid-lens `internal/ai/` (Factory + Observed デコレータ + Admission)

---

## 1. モデルゲートウェイ概要

```
業務層 (AgentLoop / RAG / 動画パイプライン)
                │
                ▼
┌─────────────────────────────────────────────────────────────────┐
│                      LLMService (統一インターフェース)            │
│  chat() / stream_chat() / embed() / rerank()                    │
└────────────────────────────────┬────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                     ModelRouter (ルーティング層)                  │
│  1. タスク種別で候補を選択 (thinking/normal/fast)                 │
│  2. 優先度でソート (primary → fallback)                          │
│  3. サーキットブレーカーでフィルタ (OPEN 状態のモデルをスキップ)    │
└────────────────────────────────┬────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│            ModelHealthStore (三状態サーキットブレーカー状態機械)   │
│  ┌────────────────┐  failureThreshold   ┌────────────────┐  openDuration  ┌────────────────┐ │
│  │     CLOSED     │───────────────────▶│      OPEN      │──────────────▶│   HALF_OPEN    │ │
│  │    (正常)      │                    │    (遮断)      │ プローブ成功   │(ハーフオープン)  │ │
│  └────────────────┘                    └────────────────┘◀──────────────└────────────────┘ │
└────────────────────────────────┬────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│               LlmFirstPacketProbe (初回応答プローブ)             │
│  ストリーム開始 → 60s 以内に初回 token を待機 →                   │
│  成功なら転送 / 失敗ならマークして次の候補へ                       │
└────────────────────────────────┬────────────────────────────────┘
                                 │
                                 ▼
┌─────────────────────────────────────────────────────────────────┐
│                  Provider 実装層 (複数プロバイダ)                 │
│  ┌─────────┐ ┌─────────┐ ┌──────────┐ ┌──────────┐ ┌────────┐  │
│  │ Ollama  │ │ OpenAI  │ │Anthropic │ │ DeepSeek │ │ Custom │  │
│  │Provider │ │Provider │ │ Provider │ │ Provider │ │Provider│  │
│  └─────────┘ └─────────┘ └──────────┘ └──────────┘ └────────┘  │
└──────────────────────────────────┬──────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────┐
│            Observed デコレータ + TokenAccounting (観測層)        │
│  記録: 所要時間 / token / コスト / エラーコード →                 │
│  ai_call_logs テーブル + Prometheus                              │
└─────────────────────────────────────────────────────────────────┘
```

---

## 2. 統一インターフェース抽象

```python
class LLMService(Protocol):
    """業務層が唯一依存するインターフェース。すべてのプロバイダ差異を隠蔽する"""
    
    async def chat(self, request: ChatRequest) -> ChatResponse: ...
    async def stream_chat(self, request: ChatRequest) -> AsyncIterator[ChatChunk]: ...
    async def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]: ...
    async def rerank(self, query: str, documents: list[str]) -> list[RerankResult]: ...


@dataclass
class ChatRequest:
    messages: list[dict]              # [{"role":, "content":}]
    model: str | None = None          # デフォルトを上書き
    temperature: float = 0.3
    max_tokens: int = 4096
    thinking: bool = False            # 思考モードが必要かどうか
    response_format: dict | None = None  # {"type": "json_object"}
    stream: bool = False
    user_id: UUID | None = None      # クォータの帰属先
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

## 3. 三状態サーキットブレーカー（ModelHealthStore）

```python
class CircuitState(StrEnum):
    CLOSED = "closed"       # 正常時は通過させる
    OPEN = "open"            # 遮断中、リクエストを拒否
    HALF_OPEN = "half_open" # プローブ期間、限定的に通過させる


class ModelHealthStore:
    """
    状態機械：
    CLOSED ──(連続失敗 ≥ threshold)──▶ OPEN
    OPEN   ──(openDuration 経過)──▶ HALF_OPEN
    HALF_OPEN ──(プローブ成功)──▶ CLOSED
    HALF_OPEN ──(プローブ失敗)──▶ OPEN (タイマーをリセット)
    """
    
    def __init__(self, redis: Redis):
        self.redis = redis
        self.failure_threshold = 3       # 3 回連続失敗で遮断
        self.open_duration_ms = 30000    # 遮断 30s 後にプローブ
        self.half_open_max_calls = 2     # ハーフオープン期のプローブは最大 2 件
    
    async def allow_call(self, model_id: str) -> bool:
        state = await self._get_state(model_id)
        if state == CircuitState.CLOSED:
            return True
        if state == CircuitState.OPEN:
            return False
        # HALF_OPEN：限定的に通過させる
        if await self._try_acquire_probe_slot(model_id):
            return True
        return False
    
    async def mark_success(self, model_id: str):
        # いずれかの成功 → CLOSED、カウントをクリア
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
            # ハーフオープン期の失敗は直接 OPEN へ戻す
            await self._transition_to_open(model_id)
    
    async def _get_state(self, model_id: str) -> CircuitState:
        data = await self.redis.hgetall(f"health:{model_id}")
        if not data:
            return CircuitState.CLOSED
        
        state = data.get("state", CircuitState.CLOSED.value)
        if state == CircuitState.OPEN.value:
            # プローブ時刻に達したか確認
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
        # Lua によるアトミック操作：残りのプローブ枠を -1
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

## 4. 初回応答プローブ（LlmFirstPacketProbe）

```python
class LlmFirstPacketProbe:
    """ストリーム開始後 60s 以内に初回 token を待機し、タイムアウトなら失敗と判定する"""
    
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
    """デコレータ：実際のストリームコールバックをラップし、最初の chunk でイベントを発火する"""
    
    def __init__(self, callback: AsyncCallable):
        self.callback = callback
        self.first_packet_event = asyncio.Event()
        self.start_time = time.time()
        self.buffered_chunks: list[ChatChunk] = []
    
    async def on_chunk(self, chunk: ChatChunk):
        if not self.first_packet_event.is_set():
            self.first_packet_event.set()
        # 実際のコールバックへ透過的に転送
        await self.callback(chunk)
    
    def get_latency_ms(self) -> int:
        return int((time.time() - self.start_time) * 1000)
    
    def cancel(self):
        self.first_packet_event = None
```

---

## 5. ルーティングとオーケストレーション（RoutingLLMService）

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
        # タスク種別ごとの優先度フォールバックチェーン（設定駆動）
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
            
            # 実際のストリームを起動（バックグラウンドタスク）
            stream_task = asyncio.create_task(
                self._consume_stream(provider, request, model_id, bridge)
            )
            
            # 初回応答プローブ
            probe_result = await self.probe.await_first_packet(bridge)
            
            if probe_result.success:
                await self.health.mark_success(model_id)
                # バッファを再生 + 引き続き透過転送
                async for chunk in bridge.replay_and_continue():
                    yield chunk
                await stream_task  # 完了を待機し、token を記録
                return
            else:
                await self.health.mark_failure(model_id)
                stream_task.cancel()
                logger.warning(f"First packet failed for {model_id}: {probe_result.error}")
                continue
        
        raise AllProvidersFailedError(f"Stream failed for all candidates")
```

---

## 6. 複数プロバイダの Provider 実装

```python
class LLMProvider(Protocol):
    name: str
    async def chat(self, request: ChatRequest) -> ChatResponse: ...
    async def stream_chat(self, request: ChatRequest, bridge: ProbeStreamBridge) -> None: ...
    async def embed(self, texts: list[str]) -> list[list[float]]: ...
    def estimate_cost(self, usage: TokenUsage) -> float: ...


class OllamaProvider:
    name = "ollama"
    # ローカル優先：ゼロコスト、低遅延、データを外部に出さない
    
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
                cost_usd=0.0  # ローカルはゼロコスト
            ),
            finish_reason=response.get("done_reason", "stop"),
            latency_ms=0,
            provider=self.name
        )
    
    def estimate_cost(self, usage: TokenUsage) -> float:
        return 0.0  # ローカル LLM はゼロコスト


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
    # OpenAI 互換インターフェース
    def __init__(self, api_key: str):
        self.client = AsyncOpenAI(api_key=api_key, base_url="https://api.deepseek.com")
    # ... 実装は OpenAI と同一で、base_url と pricing のみ異なる
```

---

## 7. トークン課金とクォータ（TokenAccounting + Admission）

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
        
        # Prometheus メトリクス
        ai_tokens_total.labels(provider=model_id, type="prompt").inc(response.usage.prompt_tokens)
        ai_tokens_total.labels(provider=model_id, type="completion").inc(response.usage.completion_tokens)
        ai_cost_usd_total.labels(provider=model_id).inc(response.usage.cost_usd)


class Admission:
    """アドミッション制御：トークンバケットによるレートリミット + クォータ台帳 + リトライ予算"""
    
    async def check(self, user_id: UUID, model_id: str, estimated_tokens: int) -> AdmissionResult:
        # 1. トークンバケット：ユーザーごと・モデルごとのレートリミット
        allowed = await self.token_bucket.acquire(
            key=f"rate:{user_id}:{model_id}",
            capacity=100,  # バケット容量
            refill_rate=10  # 10s ごと / token
        )
        if not allowed:
            return AdmissionResult(allowed=False, reason="rate_limited")
        
        # 2. 月次クォータ：動画の分数 / Token 数
        quota = await self.quota_repo.get(user_id)
        if quota.used_tokens + estimated_tokens > quota.max_tokens:
            return AdmissionResult(allowed=False, reason="quota_exceeded")
        
        # 3. リトライ予算：分析タスクに関連付け
        if request.trace_id:
            budget = await self.budget_repo.get(request.trace_id)
            if budget.consumed + estimated_tokens > budget.max_tokens:
                return AdmissionResult(allowed=False, reason="retry_budget_exhausted")
        
        return AdmissionResult(allowed=True)
```

---

## 8. 設定とフォールバックチェーン

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

## 9. 監視メトリクス

| メトリクス名 | 種別 | ラベル | 用途 |
|--------|------|------|------|
| `vm_llm_request_total` | Counter | provider, model, status, task_type | リクエスト数 |
| `vm_llm_duration_seconds` | Histogram | provider, model | エンドツーエンドの所要時間 |
| `vm_llm_first_packet_seconds` | Histogram | provider, model | 初回応答遅延 |
| `vm_llm_tokens_total` | Counter | provider, type(prompt/completion) | Token 消費 |
| `vm_llm_cost_usd_total` | Counter | provider | コスト累計 |
| `vm_circuit_state` | Gauge | model_id | 現在のサーキットブレーカー状態 (0=closed,1=open,2=half_open) |
| `vm_circuit_transitions_total` | Counter | model_id, from, to | 状態遷移回数 |
| `vm_admission_rejected_total` | Counter | reason | 受入拒否の理由 |

---

> **関連ドキュメント**：[ARCHITECTURE_JP.md](ARCHITECTURE_JP.md) · [RAG-RETRIEVAL_JP.md](RAG-RETRIEVAL_JP.md) · [AGENT-LOOP_JP.md](AGENT-LOOP_JP.md) · [OBSERVABILITY_JP.md](OBSERVABILITY_JP.md)
