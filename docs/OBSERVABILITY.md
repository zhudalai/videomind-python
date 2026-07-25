# VideoMind 可观测性设计

> 结构化 JSON 日志 + Prometheus 指标 + Grafana 看板 + 全链路 Trace + rag-eval 离线评测
> 核心参考：Ragent `infra-observability/` + DOVideo-AI 监控体系 + Google SRE 实践 (SLO/SLI/Error Budget)

---

## 1. 可观测性总览

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         VideoMind Observability Stack                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌────────────────────┐  │
│  │  Metrics     │  │  Logs        │  │  Traces      │  │  Evaluation        │  │
│  │  (Prometheus)│  │  (Loki/ELK)  │  │  (Tempo/     │  │  (rag-eval)        │  │
│  │              │  │              │  │   Jaeger)    │  │                    │  │
│  │ • RED/USE    │  │ • JSON       │  │              │  │ • Retrieval:       │  │
│  │   Rate/Err/  │  │   Structured │  │ • W3C        │  │   Recall@K, NDCG   │  │
│  │   Duration   │  │ • Levels:    │  │   Trace      │  │ • Generation:      │  │
│  │ • Business   │  │   debug/     │  │   Context    │  │   Faithfulness,    │  │
│  │   KPIs       │  │   info/      │  │ • Span       │  │   Relevance,       │  │
│  │ • System     │  │   warn/error │  │   Attributes │  │   Hallucination    │  │
│  │   (CPU/MEM/  │  │ • Sampling   │  │ • Service    │  │ • End-to-End       │  │
│  │   GPU/Disk)  │  │   (tail-based)               │  │   Answer Quality   │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  └────────────────────┘  │
│          │               │               │                    │                │
│          └───────────────┼───────────────┼────────────────────┘                │
│                          ▼               ▼                                     │
│              ┌─────────────────────────────────────────────┐                   │
│              │           Grafana Dashboards                 │                   │
│              │  • System Overview  • Business KPIs         │                   │
│              │  • RAG Pipeline     • AgentLoop             │                   │
│              │  • Video Pipeline   • Model Gateway         │                   │
│              │  • GPU Utilization  • Cost Tracking         │                   │
│              └─────────────────────────────────────────────┘                   │
│                                    │                                           │
│                                    ▼                                           │
│              ┌─────────────────────────────────────────────┐                   │
│              │         Alerting (Alertmanager)              │                   │
│              │  • PagerDuty / Feishu / Email / Webhook     │                   │
│              │  • Multi-level: Page / Ticket / Log         │                   │
│              └─────────────────────────────────────────────┘                   │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. 结构化日志

### 2.1 日志规范

```python
# core/logging.py
import structlog
import logging
import sys
from typing import Any

# 统一日志格式
def configure_logging(service_name: str, log_level: str = "INFO", json_output: bool = True):
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.format_exc_info,
        _add_service_name(service_name),
        _add_trace_context,
    ]
    
    if json_output:
        processors = shared_processors + [structlog.processors.JSONRenderer()]
    else:
        processors = shared_processors + [
            structlog.dev.ConsoleRenderer(colors=True)
        ]
    
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, log_level)),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    
    # 标准库 logging 也用 structlog
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level)
    )


def _add_service_name(service_name: str):
    def processor(logger, method_name, event_dict):
        event_dict["service"] = service_name
        return event_dict
    return processor


def _add_trace_context(logger, method_name, event_dict):
    # 从 contextvars 获取 trace_id, span_id
    trace_id = trace_context.get("trace_id")
    span_id = trace_context.get("span_id")
    if trace_id:
        event_dict["trace_id"] = trace_id
    if span_id:
        event_dict["span_id"] = span_id
    return event_dict


# 使用示例
logger = structlog.get_logger()

# 业务日志
logger.info(
    "video_ingestion_started",
    video_id=str(video.id),
    url=video.url,
    duration_sec=video.duration,
    user_id=str(user.id),
    priority=task.priority
)

# 错误日志（自动包含堆栈）
logger.error(
    "asr_transcription_failed",
    video_id=str(video.id),
    segment_index=segment.index,
    error_type=type(e).__name__,
    error_message=str(e),
    retry_count=task.retry_count
)

# 审计日志
logger.info(
    "audit",
    event_type="VIDEO_UPLOAD",
    user_id=str(user.id),
    resource_type="video",
    resource_id=str(video.id),
    action="create",
    result="success"
)
```

### 2.2 日志字段标准

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `timestamp` | ISO8601 UTC | ✅ | 事件时间 |
| `level` | string | ✅ | debug/info/warn/error/critical |
| `service` | string | ✅ | 服务名 |
| `trace_id` | string | ⭕ | W3C Trace ID (32位hex) |
| `span_id` | string | ⭕ | W3C Span ID (16位hex) |
| `event` | string | ✅ | 事件名（snake_case） |
| `logger` | string | ⭕ | logger 名称 |
| `message` | string | ❌ | 人类可读消息（可选） |
| `...业务字段` | any | ⭕ | 根据事件类型 |

---

## 3. Prometheus 指标体系

### 3.1 指标命名规范

```
<namespace>_<subsystem>_<metric_name>_<unit>
namespace: vm (videomind)
subsystem: api, worker, rag, agent, pipeline, gateway, gpu, db, cache
metric_name: 语义化，遵循 Prometheus 命名最佳实践
unit: total(计数), seconds(时长), bytes(字节), ratio(比率)
```

### 3.2 核心指标清单

#### API 层 (RED 指标)
```python
# api/metrics.py
from prometheus_client import Counter, Histogram, Gauge

# 请求总量
vm_api_http_requests_total = Counter(
    "vm_api_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code", "user_tier"]
)

# 请求延迟
vm_api_http_request_duration_seconds = Histogram(
    "vm_api_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "endpoint"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

# 并发请求数
vm_api_http_requests_in_flight = Gauge(
    "vm_api_http_requests_in_flight",
    "Current in-flight HTTP requests",
    ["method", "endpoint"]
)

# 认证
vm_api_auth_attempts_total = Counter(
    "vm_api_auth_attempts_total",
    "Authentication attempts",
    ["method", "result"]  # result: success/failed/rate_limited
)
```

#### Worker 任务层
```python
# 任务提交/完成
vm_worker_tasks_submitted_total = Counter(
    "vm_worker_tasks_submitted_total",
    "Tasks submitted to queue",
    ["task_type", "priority"]
)

vm_worker_tasks_completed_total = Counter(
    "vm_worker_tasks_completed_total",
    "Tasks completed",
    ["task_type", "status"]  # status: success/failed/cancelled
)

vm_worker_task_duration_seconds = Histogram(
    "vm_worker_task_duration_seconds",
    "Task execution duration",
    ["task_type", "phase"],  # phase: download/transcode/asr/ocr/index/analysis
    buckets=[1, 5, 10, 30, 60, 120, 300, 600, 1800, 3600]
)

# 重试
vm_worker_task_retries_total = Counter(
    "vm_worker_task_retries_total",
    "Task retries",
    ["task_type", "error_category"]
)

# 租约
vm_worker_lease_acquired_total = Counter(
    "vm_worker_lease_acquired_total",
    "Lease acquisitions",
    ["worker_id", "result"]  # result: acquired/expired/stolen
)
```

#### RAG 检索管线
```python
# 检索各阶段
vm_rag_retrieval_duration_seconds = Histogram(
    "vm_rag_retrieval_duration_seconds",
    "RAG retrieval pipeline latency",
    ["stage"],  # rewrite/route/vector/bm25/sql/fusion/expand/rerank/generate
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5]
)

vm_rag_retrieval_results = Histogram(
    "vm_rag_retrieval_results",
    "Number of results at each stage",
    ["stage"],
    buckets=[1, 5, 10, 20, 50, 100, 200]
)

vm_rag_rerank_score = Histogram(
    "vm_rag_rerank_score",
    "Reranker scores distribution",
    ["reranker_type"],  # deterministic/cross_encoder
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
)

vm_rag_answer_quality = Histogram(
    "vm_rag_answer_quality",
    "Answer quality scores",
    ["metric"],  # faithfulness/relevance/hallucination
    buckets=[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
)
```

#### AgentLoop
```python
vm_agent_loop_rounds = Histogram(
    "vm_agent_loop_rounds",
    "AgentLoop rounds per analysis",
    buckets=[1, 2, 3, 4, 5]
)

vm_agent_loop_duration_seconds = Histogram(
    "vm_agent_loop_duration_seconds",
    "AgentLoop total duration",
    buckets=[1, 5, 10, 30, 60, 120, 300]
)

vm_agent_critic_passed_total = Counter(
    "vm_agent_critic_passed_total",
    "Critic evaluation passed",
    ["analysis_type"]
)

vm_agent_evidence_verification = Counter(
    "vm_agent_evidence_verification_total",
    "Evidence verification results",
    ["evidence_type", "result"]  # result: pass/fail/partial
)
```

#### 模型网关
```python
vm_gateway_requests_total = Counter(
    "vm_gateway_requests_total",
    "Gateway requests",
    ["provider", "model", "task_type", "status"]
)

vm_gateway_duration_seconds = Histogram(
    "vm_gateway_duration_seconds",
    "Gateway end-to-end latency",
    ["provider", "model"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0]
)

vm_gateway_first_packet_seconds = Histogram(
    "vm_gateway_first_packet_seconds",
    "First token latency (streaming)",
    ["provider", "model"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0]
)

vm_gateway_tokens_total = Counter(
    "vm_gateway_tokens_total",
    "Token consumption",
    ["provider", "model", "type"]  # type: prompt/completion
)

vm_gateway_cost_usd_total = Counter(
    "vm_gateway_cost_usd_total",
    "Cost in USD",
    ["provider", "model"]
)

vm_gateway_circuit_state = Gauge(
    "vm_gateway_circuit_state",
    "Circuit breaker state (0=closed, 1=open, 2=half_open)",
    ["model_id"]
)
```

#### GPU 资源
```python
vm_gpu_utilization = Gauge(
    "vm_gpu_utilization",
    "GPU utilization percentage",
    ["gpu_id"]
)

vm_gpu_memory_used_bytes = Gauge(
    "vm_gpu_memory_used_bytes",
    "GPU memory used",
    ["gpu_id", "process"]
)

vm_gpu_queue_wait_seconds = Histogram(
    "vm_gpu_queue_wait_seconds",
    "Time waiting for GPU lock",
    ["stage"],  # asr/ocr/embedding
    buckets=[1, 5, 10, 30, 60, 120, 300, 600]
)

vm_gpu_stage_duration_seconds = Histogram(
    "vm_gpu_stage_duration_seconds",
    "GPU stage execution time",
    ["stage", "model"],
    buckets=[1, 5, 10, 30, 60, 120, 300]
)
```

#### 业务 KPI
```python
vm_biz_videos_ingested_total = Counter(
    "vm_biz_videos_ingested_total",
    "Videos successfully ingested",
    ["source"]  # youtube/bilibili/douyin/local（douyin ⏳ 延后实现 Phase 2+，首版仅 youtube/bilibili/local）
)

vm_biz_active_users = Gauge(
    "vm_biz_active_users",
    "Active users (5min window)",
    ["tier"]  # free/pro/enterprise
)

vm_biz_analysis_sessions = Counter(
    "vm_biz_analysis_sessions_total",
    "Analysis sessions started",
    ["goal_type"]  # summary/qa/analysis/generation
)
```

---

## 4. 全链路追踪

### 4.1 Trace Context 传播

```python
# core/tracing.py
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.b3 import B3MultiFormat
from opentelemetry.trace import SpanKind, Status, StatusCode

def setup_tracing(service_name: str, otlp_endpoint: str):
    provider = TracerProvider(resource=Resource.create({
        "service.name": service_name,
        "service.version": "1.0.0",
        "deployment.environment": os.getenv("ENV", "dev")
    }))
    
    # OTLP 导出到 Tempo/Jaeger
    exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    
    trace.set_tracer_provider(provider)
    
    # 传播格式：支持 B3 (Zipkin) + W3C TraceContext
    set_global_textmap(B3MultiFormat())
    
    # 自动插桩
    FastAPIInstrumentor.instrument_app(app)
    RedisInstrumentor().instrument()
    SQLAlchemyInstrumentor().instrument(engine=engine)
    HTTPXClientInstrumentor().instrument()
    
    return trace.get_tracer(__name__)


# 手动创建 Span 示例
tracer = trace.get_tracer("videomind.pipeline")

async def process_video(video_id: UUID):
    with tracer.start_as_current_span(
        "video.process",
        kind=SpanKind.INTERNAL,
        attributes={
            "video.id": str(video_id),
            "video.source": "youtube"
        }
    ) as span:
        try:
            # 下载阶段
            with tracer.start_as_current_span("video.download") as dl_span:
                path = await download(video_id)
                dl_span.set_attribute("file.size_bytes", os.path.getsize(path))
            
            # 转码阶段
            with tracer.start_as_current_span("video.transcode") as tc_span:
                segments = await transcode(path)
                tc_span.set_attribute("segments.count", len(segments))
            
            # ... 其他阶段
            
            span.set_status(Status(StatusCode.OK))
        except Exception as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise
```

### 4.2 语义化 Span 属性

```python
# 语义约定（参考 OpenTelemetry Semantic Conventions）
SPAN_ATTRIBUTES = {
    # 通用
    "service.name": "string",
    "service.namespace": "string",
    "service.instance.id": "string",
    
    # HTTP
    "http.method": "GET|POST|PUT|DELETE",
    "http.scheme": "http|https",
    "http.target": "/api/v1/videos",
    "http.status_code": 200,
    "http.request_content_length": 1024,
    "http.response_content_length": 2048,
    
    # 数据库
    "db.system": "postgresql",
    "db.operation": "SELECT",
    "db.statement": "SELECT * FROM videos WHERE id = $1",
    "db.collection": "videos",
    
    # 消息队列
    "messaging.system": "redis",
    "messaging.destination": "tasks:ingestion",
    "messaging.operation": "publish|receive",
    "messaging.message_id": "uuid",
    "messaging.message_conversation_id": "trace_id",
    
    # AI/LLM
    "gen_ai.system": "ollama|openai|anthropic",
    "gen_ai.request.model": "qwen2.5:7b",
    "gen_ai.request.temperature": 0.3,
    "gen_ai.request.max_tokens": 4096,
    "gen_ai.response.model": "qwen2.5:7b",
    "gen_ai.usage.prompt_tokens": 1500,
    "gen_ai.usage.completion_tokens": 800,
    "gen_ai.usage.total_tokens": 2300,
    
    # 视频处理
    "video.id": "uuid",
    "video.duration_seconds": 1200,
    "video.source": "youtube",
    "video.segment.count": 20,
    "video.transcription.language": "zh",
    
    # RAG
    "rag.query": "视频讲了什么",
    "rag.intent": "video_qa.summary",
    "rag.retrieval.vector_count": 60,
    "rag.retrieval.bm25_count": 60,
    "rag.rerank.top_k": 10,
    "rag.answer.confidence": 0.92
}
```

---

## 5. Grafana 看板设计

### 5.1 看板层级

| 看板 | 用途 | 刷新频率 | 受众 |
|------|------|----------|------|
| **System Overview** | 集群健康、RED 指标、资源 | 10s | On-call, SRE |
| **Business KPI** | 视频入库量、活跃用户、分析完成率 | 1min | PM, 运营 |
| **RAG Pipeline** | 检索各阶段延迟、召回率、重排分布 | 10s | RAG 工程师 |
| **AgentLoop** | 轮次分布、Critic 通过率、证据核验 | 10s | Agent 工程师 |
| **Model Gateway** | 供应商健康、熔断状态、Token 成本 | 10s | 平台工程师 |
| **Video Pipeline** | 各阶段吞吐、GPU 排队、失败率 | 10s | 视频工程师 |
| **GPU Utilization** | 显存/算力占用、阶段时间线 | 5s | 算力运维 |

### 5.2 关键 Panel 示例

```json
{
  "title": "RAG Retrieval Latency (p50/p95/p99)",
  "type": "timeseries",
  "targets": [
    {
      "expr": "histogram_quantile(0.50, rate(vm_rag_retrieval_duration_seconds_bucket[5m]))",
      "legendFormat": "p50"
    },
    {
      "expr": "histogram_quantile(0.95, rate(vm_rag_retrieval_duration_seconds_bucket[5m]))",
      "legendFormat": "p95"
    },
    {
      "expr": "histogram_quantile(0.99, rate(vm_rag_retrieval_duration_seconds_bucket[5m]))",
      "legendFormat": "p99"
    }
  ],
  "gridPos": {"x": 0, "y": 0, "w": 12, "h": 8}
}
```

---

## 6. 告警规则

### 6.1 告警分级

| 级别 | 定义 | 响应时间 | 通知渠道 |
|------|------|----------|----------|
| **P0 (Page)** | 核心服务不可用、SLO 即将耗尽 | 5 分钟 | PagerDuty + 电话 + 飞书 |
| **P1 (Ticket)** | 核心功能降级、错误率升高 | 30 分钟 | 飞书 + 邮件 |
| **P2 (Log)** | 非核心异常、资源预警 | 4 小时 | 飞书群 |
| **P3 (Info)** | 容量规划、趋势变化 | 次日 | 周报 |

### 6.2 核心告警规则

```yaml
# alerts/videomind.yml
groups:
- name: videomind-critical
  interval: 30s
  rules:
  # API 可用性
  - alert: APIHighErrorRate
    expr: |
      sum(rate(vm_api_http_requests_total{status_code=~"5.."}[5m])) 
      / sum(rate(vm_api_http_requests_total[5m])) > 0.05
    for: 2m
    labels:
      severity: P0
      team: platform
    annotations:
      summary: "API 5xx 错误率 > 5%"
      description: "当前错误率: {{ $value | humanizePercentage }}"
      runbook_url: "https://wiki.videomind.com/runbooks/api-high-error-rate"
  
  # API 延迟
  - alert: APIHighLatency
    expr: |
      histogram_quantile(0.95, rate(vm_api_http_request_duration_seconds_bucket[5m])) > 2
    for: 5m
    labels:
      severity: P1
    annotations:
      summary: "API P95 延迟 > 2s"
  
  # 任务队列积压
  - alert: TaskQueueBacklog
    expr: |
      sum(vm_worker_tasks_submitted_total - vm_worker_tasks_completed_total) by (task_type) > 100
    for: 10m
    labels:
      severity: P1
    annotations:
      summary: "{{ $labels.task_type }} 任务积压 > 100"
  
  # GPU 资源耗尽
  - alert: GPUExhausted
    expr: |
      vm_gpu_queue_wait_seconds{pquantile="0.95"} > 600
    for: 5m
    labels:
      severity: P0
    annotations:
      summary: "GPU 等待时间 P95 > 10 分钟，可能死锁或容量不足"
  
  # 模型网关熔断
  - alert: ModelCircuitOpen
    expr: |
      vm_gateway_circuit_state == 1
    for: 1m
    labels:
      severity: P1
    annotations:
      summary: "模型 {{ $labels.model_id }} 熔断器开启"
  
  # RAG 检索召回率下降（需离线评测数据）
  - alert: RAGRecallDegraded
    expr: |
      vm_rag_eval_recall_at_10 < 0.7
    for: 1h
    labels:
      severity: P2
    annotations:
      summary: "RAG Recall@10 降至 70% 以下"

- name: videomind-warning
  interval: 1m
  rules:
  - alert: HighMemoryUsage
    expr: |
      (container_memory_usage_bytes / container_spec_memory_limit_bytes) > 0.85
    for: 10m
    labels:
      severity: P2
  
  - alert: DiskSpaceLow
    expr: |
      (node_filesystem_avail_bytes / node_filesystem_size_bytes) < 0.15
    for: 5m
    labels:
      severity: P1
  
  - alert: CertificateExpiring
    expr: |
      ssl_certificate_expiry_timestamp_seconds - time() < 86400 * 14
    for: 1h
    labels:
      severity: P2
```

---

## 7. 离线评测框架 (rag-eval)

```python
# eval/rag_evaluator.py
"""
离线评测：定期跑测试集，产出指标推送到 Prometheus
用于监控 RAG 质量漂移、对比不同配置、回归测试
"""

class RetrievalPipeline:
    """检索评测外壳（core/observability）：在真实编排入口之上薄封装，供离线 rag-eval
    复用检索链而不耦合 IntentRouter / AnswerGenerator 的业务编排。

    真实编排链权威见 INTENT-ROUTING.md：IntentRouter → QueryRewriter → MultiChannelRetrieval。"""

    def __init__(self, multi_channel: "MultiChannelRetrieval"):
        self.multichannel = multi_channel

    async def search(
        self, query: str, sub_queries: list[str], quota: "ChannelQuota", filters: "SearchFilters"
    ) -> "MultiChannelResult":
        # 复用 INTENT-ROUTING 的 MultiChannelRetrieval.search 真实编排链
        # （向量 + BM25 + SQL → RRF → Rerank），避免与 INTENT-ROUTING 双源漂移。
        return await self.multichannel.search(query, sub_queries, quota, filters)


class RAGEvaluator:
    def __init__(self, rag_pipeline: RetrievalPipeline, llm: LLMService):
        self.pipeline = rag_pipeline
        self.llm = llm
    
    async def evaluate_dataset(self, dataset: EvaluationDataset) -> EvaluationReport:
        results = []
        
        for sample in dataset.samples:
            # 1. 检索评测
            retrieval_result = await self._eval_retrieval(sample)
            
            # 2. 生成评测
            generation_result = await self._eval_generation(sample, retrieval_result)
            
            # 3. 端到端评测
            e2e_result = await self._eval_end_to_end(sample)
            
            results.append(EvaluationResult(
                sample_id=sample.id,
                retrieval=retrieval_result,
                generation=generation_result,
                end_to_end=e2e_result
            ))
        
        # 汇总指标
        report = self._aggregate(results)
        
        # 推送到 Prometheus Pushgateway
        await self._push_metrics(report)
        
        return report
    
    async def _eval_retrieval(self, sample: EvalSample) -> RetrievalMetrics:
        # 运行检索
        hits = await self.pipeline.search(sample.query, top_k=20)
        retrieved_ids = [h.chunk_id for h in hits]
        
        # 标准 IR 指标
        relevant_ids = set(sample.ground_truth_chunk_ids)
        retrieved_relevant = [cid for cid in retrieved_ids if cid in relevant_ids]
        
        recall_at_k = {}
        for k in [1, 3, 5, 10, 20]:
            recall_at_k[f"recall@{k}"] = len([cid for cid in retrieved_ids[:k] if cid in relevant_ids]) / max(len(relevant_ids), 1)
        
        # NDCG
        ndcg = self._ndcg_at_k(retrieved_ids, relevant_ids, k=10)
        
        # MRR
        mrr = 0
        for i, cid in enumerate(retrieved_ids):
            if cid in relevant_ids:
                mrr = 1 / (i + 1)
                break
        
        return RetrievalMetrics(
            recall_at_k=recall_at_k,
            ndcg_at_10=ndcg,
            mrr=mrr,
            retrieved_count=len(retrieved_ids)
        )
    
    async def _eval_generation(self, sample: EvalSample, retrieval: RetrievalResult) -> GenerationMetrics:
        # 生成答案
        answer = await self.pipeline.generate(sample.query, retrieval.hits)
        
        # 使用 LLM-as-Judge 评分
        faithfulness = await self._llm_judge_faithfulness(answer, retrieval.hits)
        relevance = await self._llm_judge_relevance(answer, sample.query)
        hallucination = await self._llm_judge_hallucination(answer, retrieval.hits)
        
        return GenerationMetrics(
            faithfulness=faithfulness,
            relevance=relevance,
            hallucination_rate=hallucination,
            answer_length=len(answer)
        )
    
    async def _llm_judge_faithfulness(self, answer: str, hits: list[RetrievalHit]) -> float:
        context = "\n\n".join([f"[{h.chunk_id}] {h.content}" for h in hits])
        prompt = f"""判断回答是否忠实于上下文证据。

上下文：
{context}

回答：
{answer}

请打分 0-1：1=完全基于证据，0=完全编造。仅输出数字。"""
        
        response = await self.llm.chat(ChatRequest(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=10
        ))
        return float(response.content.strip())
    
    def _push_metrics(self, report: EvaluationReport):
        """推送到 Pushgateway，Grafana 可视化"""
        from prometheus_client import CollectorRegistry, Gauge, push_to_gateway
        
        registry = CollectorRegistry()
        
        # 检索指标
        Gauge("vm_rag_eval_recall_at_1", "Recall@1", registry=registry).set(report.avg_recall_at_1)
        Gauge("vm_rag_eval_recall_at_5", "Recall@5", registry=registry).set(report.avg_recall_at_5)
        Gauge("vm_rag_eval_recall_at_10", "Recall@10", registry=registry).set(report.avg_recall_at_10)
        Gauge("vm_rag_eval_ndcg_at_10", "NDCG@10", registry=registry).set(report.avg_ndcg_at_10)
        Gauge("vm_rag_eval_mrr", "MRR", registry=registry).set(report.avg_mrr)
        
        # 生成指标
        Gauge("vm_rag_eval_faithfulness", "Faithfulness", registry=registry).set(report.avg_faithfulness)
        Gauge("vm_rag_eval_relevance", "Relevance", registry=registry).set(report.avg_relevance)
        Gauge("vm_rag_eval_hallucination", "Hallucination Rate", registry=registry).set(report.avg_hallucination)
        
        push_to_gateway("pushgateway:9091", job="rag-eval", registry=registry)
```

---

## 8. SLO / SLI 定义

| 服务 | SLI | SLO 目标 | 误差预算 | 备注 |
|------|-----|----------|----------|------|
| API Gateway | 可用性 (非 5xx) | 99.9% | 43min/月 | 30天滚动 |
| API Gateway | P95 延迟 | < 500ms | - | 核心接口 |
| Video Ingestion | 入库成功率 | 99% | - | 含重试 |
| Video Ingestion | 端到端延迟 (P95) | < 30min | - | 30分钟视频 |
| RAG Query | 检索 Recall@10 | > 80% | - | 离线评测 |
| RAG Query | 答案 Faithfulness | > 90% | - | LLM-as-Judge |
| AgentLoop | 分析完成率 | 95% | - | 含重试 |
| Model Gateway | 调用成功率 | 99.5% | - | 含降级 |
| Model Gateway | 首包延迟 P95 | < 10s | - | 流式模式 |

---

## 9. 运维工具

```bash
# 快速诊断脚本
#!/bin/bash
# diagnose.sh

echo "=== VideoMind Health Check ==="
echo "Time: $(date -u)"

# 1. API 健康
curl -s http://api:8000/health | jq .

# 2. 核心指标
echo "--- Prometheus Key Metrics ---"
curl -s "http://prometheus:9090/api/v1/query?query=vm_api_http_requests_total" | jq '.data.result[] | {metric: .metric, value: .value[1]}'

# 3. 任务队列
echo "--- Celery Queue Status ---"
celery -A tasks.celery_app inspect active_queues

# 4. GPU 状态
echo "--- GPU Status ---"
nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu --format=csv

# 5. 熔断器状态
echo "--- Circuit Breakers ---"
redis-cli KEYS "health:*" | xargs -I {} redis-cli HGETALL {}

# 6. 最近错误
echo "--- Recent Errors (last 100) ---"
curl -s "http://loki:3100/loki/api/v1/query_range?query={service=~\"videomind.*\"} |~ \"error\"&limit=100" | jq '.data.result[].values[][1]' -r | head -20
```

---

> **关联文档**：[ARCHITECTURE.md](ARCHITECTURE.md) · [TASK-ORCHESTRATION.md](TASK-ORCHESTRATION.md) · [MODEL-GATEWAY.md](MODEL-GATEWAY.md) · [RAG-RETRIEVAL.md](RAG-RETRIEVAL.md) · [DEPLOYMENT.md](DEPLOYMENT.md)