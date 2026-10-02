# VideoMind 可観測性設計

> 構造化 JSON ログ + Prometheus メトリクス + Grafana ダッシュボード + エンドツーエンドのトレーシング + rag-eval オフライン評価
> 主要参考：Ragent `infra-observability/` + DOVideo-AI 監視体系 + Google SRE 実践 (SLO/SLI/Error Budget)

---

## 1. 可観測性の全体像

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

## 2. 構造化ログ

### 2.1 ログ規約

```python
# core/logging.py
import structlog
import logging
import sys
from typing import Any

# 統一ログフォーマット
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
    
    # 標準ライブラリの logging も structlog を使う
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
    # contextvars から trace_id, span_id を取得
    trace_id = trace_context.get("trace_id")
    span_id = trace_context.get("span_id")
    if trace_id:
        event_dict["trace_id"] = trace_id
    if span_id:
        event_dict["span_id"] = span_id
    return event_dict


# 使用例
logger = structlog.get_logger()

# 業務ログ
logger.info(
    "video_ingestion_started",
    video_id=str(video.id),
    url=video.url,
    duration_sec=video.duration,
    user_id=str(user.id),
    priority=task.priority
)

# エラーログ（スタックを自動的に含む）
logger.error(
    "asr_transcription_failed",
    video_id=str(video.id),
    segment_index=segment.index,
    error_type=type(e).__name__,
    error_message=str(e),
    retry_count=task.retry_count
)

# 監査ログ
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

### 2.2 ログフィールド標準

| フィールド | 型 | 必須 | 説明 |
|------|------|------|------|
| `timestamp` | ISO8601 UTC | ✅ | イベント時刻 |
| `level` | string | ✅ | debug/info/warn/error/critical |
| `service` | string | ✅ | サービス名 |
| `trace_id` | string | ⭕ | W3C Trace ID (32桁 hex) |
| `span_id` | string | ⭕ | W3C Span ID (16桁 hex) |
| `event` | string | ✅ | イベント名（snake_case） |
| `logger` | string | ⭕ | logger 名 |
| `message` | string | ❌ | 人間が読めるメッセージ（任意） |
| `...業務フィールド` | any | ⭕ | イベント種別による |

---

## 3. Prometheus メトリクス体系

### 3.1 メトリクス命名規約

```
<namespace>_<subsystem>_<metric_name>_<unit>
namespace: vm (videomind)
subsystem: api, worker, rag, agent, pipeline, gateway, gpu, db, cache
metric_name: 意味を持たせ、Prometheus の命名ベストプラクティスに従う
unit: total(カウント), seconds(時間), bytes(バイト), ratio(比率)
```

### 3.2 コアメトリクス一覧

#### API 層 (RED メトリクス)
```python
# api/metrics.py
from prometheus_client import Counter, Histogram, Gauge

# リクエスト総数
vm_api_http_requests_total = Counter(
    "vm_api_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code", "user_tier"]
)

# リクエスト遅延
vm_api_http_request_duration_seconds = Histogram(
    "vm_api_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "endpoint"],
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
)

# 同時リクエスト数
vm_api_http_requests_in_flight = Gauge(
    "vm_api_http_requests_in_flight",
    "Current in-flight HTTP requests",
    ["method", "endpoint"]
)

# 認証
vm_api_auth_attempts_total = Counter(
    "vm_api_auth_attempts_total",
    "Authentication attempts",
    ["method", "result"]  # result: success/failed/rate_limited
)
```

#### Worker タスク層
```python
# タスク投入／完了
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

# リトライ
vm_worker_task_retries_total = Counter(
    "vm_worker_task_retries_total",
    "Task retries",
    ["task_type", "error_category"]
)

# リース
vm_worker_lease_acquired_total = Counter(
    "vm_worker_lease_acquired_total",
    "Lease acquisitions",
    ["worker_id", "result"]  # result: acquired/expired/stolen
)
```

#### RAG 検索パイプライン
```python
# 検索の各段階
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

#### モデルゲートウェイ
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

#### GPU リソース
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

#### 業務 KPI
```python
vm_biz_videos_ingested_total = Counter(
    "vm_biz_videos_ingested_total",
    "Videos successfully ingested",
    ["source"]  # youtube/bilibili/douyin/local（douyin ⏳ Phase 2+ で実装延期、初版は youtube/bilibili/local のみ）
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

## 4. エンドツーエンドのトレーシング

### 4.1 Trace Context の伝播

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
    
    # OTLP で Tempo/Jaeger へエクスポート
    exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    
    trace.set_tracer_provider(provider)
    
    # 伝播フォーマット：B3 (Zipkin) + W3C TraceContext をサポート
    set_global_textmap(B3MultiFormat())
    
    # 自動インストルメンテーション
    FastAPIInstrumentor.instrument_app(app)
    RedisInstrumentor().instrument()
    SQLAlchemyInstrumentor().instrument(engine=engine)
    HTTPXClientInstrumentor().instrument()
    
    return trace.get_tracer(__name__)


# Span を手動生成する例
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
            # ダウンロード段階
            with tracer.start_as_current_span("video.download") as dl_span:
                path = await download(video_id)
                dl_span.set_attribute("file.size_bytes", os.path.getsize(path))
            
            # トランスコード段階
            with tracer.start_as_current_span("video.transcode") as tc_span:
                segments = await transcode(path)
                tc_span.set_attribute("segments.count", len(segments))
            
            # ... その他の段階
            
            span.set_status(Status(StatusCode.OK))
        except Exception as e:
            span.record_exception(e)
            span.set_status(Status(StatusCode.ERROR, str(e)))
            raise
```

### 4.2 セマンティック Span 属性

```python
# セマンティック規約（OpenTelemetry Semantic Conventions を参考）
SPAN_ATTRIBUTES = {
    # 共通
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
    
    # データベース
    "db.system": "postgresql",
    "db.operation": "SELECT",
    "db.statement": "SELECT * FROM videos WHERE id = $1",
    "db.collection": "videos",
    
    # メッセージキュー
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
    
    # 動画処理
    "video.id": "uuid",
    "video.duration_seconds": 1200,
    "video.source": "youtube",
    "video.segment.count": 20,
    "video.transcription.language": "zh",
    
    # RAG
    "rag.query": "動画は何を話しているか",
    "rag.intent": "video_qa.summary",
    "rag.retrieval.vector_count": 60,
    "rag.retrieval.bm25_count": 60,
    "rag.rerank.top_k": 10,
    "rag.answer.confidence": 0.92
}
```

---

## 5. Grafana ダッシュボード設計

### 5.1 ダッシュボード階層

| ダッシュボード | 用途 | 更新頻度 | 対象者 |
|------|------|----------|------|
| **System Overview** | クラスタ健全性、RED メトリクス、リソース | 10s | On-call, SRE |
| **Business KPI** | 動画取り込み量、アクティブユーザー、分析完了率 | 1min | PM, 運用 |
| **RAG Pipeline** | 検索各段階の遅延、再現率、リランク分布 | 10s | RAG エンジニア |
| **AgentLoop** | ラウンド分布、Critic 通過率、証拠検証 | 10s | Agent エンジニア |
| **Model Gateway** | プロバイダ健全性、サーキットブレーカー状態、Token コスト | 10s | プラットフォームエンジニア |
| **Video Pipeline** | 各段階のスループット、GPU 待ち行列、失敗率 | 10s | 動画エンジニア |
| **GPU Utilization** | メモリ／演算リソースの占有、段階タイムライン | 5s | 計算リソース運用 |

### 5.2 主要 Panel の例

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

## 6. アラートルール

### 6.1 アラートの重大度

| レベル | 定義 | 対応時間 | 通知チャネル |
|------|------|----------|----------|
| **P0 (Page)** | コアサービス停止、SLO が枯渇間近 | 5 分 | PagerDuty + 電話 + 飛書 |
| **P1 (Ticket)** | コア機能の劣化、エラー率上昇 | 30 分 | 飛書 + メール |
| **P2 (Log)** | 非コアの異常、リソース警告 | 4 時間 | 飛書グループ |
| **P3 (Info)** | 容量計画、トレンド変化 | 翌日 | 週報 |

### 6.2 コアアラートルール

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
      summary: "API 5xx エラー率 > 5%"
      description: "現在のエラー率: {{ $value | humanizePercentage }}"
      runbook_url: "https://wiki.videomind.com/runbooks/api-high-error-rate"
  
  # API 遅延
  - alert: APIHighLatency
    expr: |
      histogram_quantile(0.95, rate(vm_api_http_request_duration_seconds_bucket[5m])) > 2
    for: 5m
    labels:
      severity: P1
    annotations:
      summary: "API P95 遅延 > 2s"
  
  # タスクキューの滞留
  - alert: TaskQueueBacklog
    expr: |
      sum(vm_worker_tasks_submitted_total - vm_worker_tasks_completed_total) by (task_type) > 100
    for: 10m
    labels:
      severity: P1
    annotations:
      summary: "{{ $labels.task_type }} のタスク滞留 > 100"
  
  # GPU リソース枯渇
  - alert: GPUExhausted
    expr: |
      vm_gpu_queue_wait_seconds{pquantile="0.95"} > 600
    for: 5m
    labels:
      severity: P0
    annotations:
      summary: "GPU 待ち時間 P95 > 10 分、デッドロックまたは容量不足の可能性"
  
  # モデルゲートウェイのサーキットブレーカー
  - alert: ModelCircuitOpen
    expr: |
      vm_gateway_circuit_state == 1
    for: 1m
    labels:
      severity: P1
    annotations:
      summary: "モデル {{ $labels.model_id }} のサーキットブレーカーがオープン"
  
  # RAG 検索の再現率低下（オフライン評価データが必要）
  - alert: RAGRecallDegraded
    expr: |
      vm_rag_eval_recall_at_10 < 0.7
    for: 1h
    labels:
      severity: P2
    annotations:
      summary: "RAG Recall@10 が 70% 以下に低下"

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

## 7. オフライン評価フレームワーク (rag-eval)

```python
# eval/rag_evaluator.py
"""
オフライン評価：定期的にテストセットを実行し、メトリクスを生成して Prometheus へプッシュする
RAG 品質のドリフト監視、異なる設定の比較、リグレッションテストに使用する
"""

class RetrievalPipeline:
    """検索評価用シェル（core/observability）：実際のオーケストレーション入口の上に薄くラップし、オフライン rag-eval に
    検索チェーンを再利用させ、IntentRouter / AnswerGenerator の業務オーケストレーションとは結合させない。

    実際のオーケストレーションチェーンの正は INTENT-ROUTING_JP.md を参照：IntentRouter → QueryRewriter → MultiChannelRetrieval。"""

    def __init__(self, multi_channel: "MultiChannelRetrieval"):
        self.multichannel = multi_channel

    async def search(
        self, query: str, sub_queries: list[str], quota: "ChannelQuota", filters: "SearchFilters"
    ) -> "MultiChannelResult":
        # INTENT-ROUTING の MultiChannelRetrieval.search の実際のオーケストレーションチェーンを再利用する
        # （ベクトル + BM25 + SQL → RRF → Rerank）、INTENT-ROUTING との二重ソースによるドリフトを避ける。
        return await self.multichannel.search(query, sub_queries, quota, filters)


class RAGEvaluator:
    def __init__(self, rag_pipeline: RetrievalPipeline, llm: LLMService):
        self.pipeline = rag_pipeline
        self.llm = llm
    
    async def evaluate_dataset(self, dataset: EvaluationDataset) -> EvaluationReport:
        results = []
        
        for sample in dataset.samples:
            # 1. 検索評価
            retrieval_result = await self._eval_retrieval(sample)
            
            # 2. 生成評価
            generation_result = await self._eval_generation(sample, retrieval_result)
            
            # 3. エンドツーエンド評価
            e2e_result = await self._eval_end_to_end(sample)
            
            results.append(EvaluationResult(
                sample_id=sample.id,
                retrieval=retrieval_result,
                generation=generation_result,
                end_to_end=e2e_result
            ))
        
        # メトリクスを集計
        report = self._aggregate(results)
        
        # Prometheus Pushgateway へプッシュ
        await self._push_metrics(report)
        
        return report
    
    async def _eval_retrieval(self, sample: EvalSample) -> RetrievalMetrics:
        # 検索を実行
        hits = await self.pipeline.search(sample.query, top_k=20)
        retrieved_ids = [h.chunk_id for h in hits]
        
        # 標準的な IR メトリクス
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
        # 回答を生成
        answer = await self.pipeline.generate(sample.query, retrieval.hits)
        
        # LLM-as-Judge でスコアリング
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
        prompt = f"""回答がコンテキストの証拠に忠実かどうかを判定する。

コンテキスト：
{context}

回答：
{answer}

0〜1 でスコアリングせよ：1=完全に証拠に基づく、0=完全な捏造。数字のみを出力すること。"""
        
        response = await self.llm.chat(ChatRequest(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=10
        ))
        return float(response.content.strip())
    
    def _push_metrics(self, report: EvaluationReport):
        """Pushgateway へプッシュし、Grafana で可視化する"""
        from prometheus_client import CollectorRegistry, Gauge, push_to_gateway
        
        registry = CollectorRegistry()
        
        # 検索メトリクス
        Gauge("vm_rag_eval_recall_at_1", "Recall@1", registry=registry).set(report.avg_recall_at_1)
        Gauge("vm_rag_eval_recall_at_5", "Recall@5", registry=registry).set(report.avg_recall_at_5)
        Gauge("vm_rag_eval_recall_at_10", "Recall@10", registry=registry).set(report.avg_recall_at_10)
        Gauge("vm_rag_eval_ndcg_at_10", "NDCG@10", registry=registry).set(report.avg_ndcg_at_10)
        Gauge("vm_rag_eval_mrr", "MRR", registry=registry).set(report.avg_mrr)
        
        # 生成メトリクス
        Gauge("vm_rag_eval_faithfulness", "Faithfulness", registry=registry).set(report.avg_faithfulness)
        Gauge("vm_rag_eval_relevance", "Relevance", registry=registry).set(report.avg_relevance)
        Gauge("vm_rag_eval_hallucination", "Hallucination Rate", registry=registry).set(report.avg_hallucination)
        
        push_to_gateway("pushgateway:9091", job="rag-eval", registry=registry)
```

---

## 8. SLO / SLI 定義

| サービス | SLI | SLO 目標 | 誤差予算 | 備考 |
|------|-----|----------|----------|------|
| API Gateway | 可用性 (非 5xx) | 99.9% | 43min/月 | 30日ローリング |
| API Gateway | P95 遅延 | < 500ms | - | コアインターフェース |
| Video Ingestion | 取り込み成功率 | 99% | - | リトライ含む |
| Video Ingestion | エンドツーエンド遅延 (P95) | < 30min | - | 30分動画 |
| RAG Query | 検索 Recall@10 | > 80% | - | オフライン評価 |
| RAG Query | 回答 Faithfulness | > 90% | - | LLM-as-Judge |
| AgentLoop | 分析完了率 | 95% | - | リトライ含む |
| Model Gateway | 呼び出し成功率 | 99.5% | - | フォールバック含む |
| Model Gateway | 初回パケット遅延 P95 | < 10s | - | ストリーミングモード |

---

## 9. 運用ツール

```bash
# クイック診断スクリプト
#!/bin/bash
# diagnose.sh

echo "=== VideoMind Health Check ==="
echo "Time: $(date -u)"

# 1. API ヘルス
curl -s http://api:8000/health | jq .

# 2. コアメトリクス
echo "--- Prometheus Key Metrics ---"
curl -s "http://prometheus:9090/api/v1/query?query=vm_api_http_requests_total" | jq '.data.result[] | {metric: .metric, value: .value[1]}'

# 3. タスクキュー
echo "--- Celery Queue Status ---"
celery -A tasks.celery_app inspect active_queues

# 4. GPU ステータス
echo "--- GPU Status ---"
nvidia-smi --query-gpu=index,name,memory.used,memory.total,utilization.gpu --format=csv

# 5. サーキットブレーカー状態
echo "--- Circuit Breakers ---"
redis-cli KEYS "health:*" | xargs -I {} redis-cli HGETALL {}

# 6. 最近のエラー
echo "--- Recent Errors (last 100) ---"
curl -s "http://loki:3100/loki/api/v1/query_range?query={service=~\"videomind.*\"} |~ \"error\"&limit=100" | jq '.data.result[].values[][1]' -r | head -20
```

---

> **関連ドキュメント**：[ARCHITECTURE_JP.md](ARCHITECTURE_JP.md) · [TASK-ORCHESTRATION_JP.md](TASK-ORCHESTRATION_JP.md) · [MODEL-GATEWAY_JP.md](MODEL-GATEWAY_JP.md) · [RAG-RETRIEVAL_JP.md](RAG-RETRIEVAL_JP.md) · [DEPLOYMENT_JP.md](DEPLOYMENT_JP.md)
