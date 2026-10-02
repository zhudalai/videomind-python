# VideoMind デプロイ設計

> Docker Compose ワンコマンド起動、環境変数、GPU パススルー、MinIO/Qdrant/Ollama ローカルデプロイ
> 主要参考：Ragent `deploy/` + DOVideo-AI の k8s 設定 + VidLens docker-compose + 本番レベルのベストプラクティス

---

## 1. デプロイアーキテクチャ

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                        VideoMind 単一サーバー/小規模クラスタ デプロイ構成図      │
│                                                                                  │
│  ┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐   │
│  │   Nginx     │────▶│  Frontend   │     │   API       │────▶│  PostgreSQL │   │
│  │  (Ingress)  │     │  (Static)   │     │  (FastAPI)  │     │  (Primary)  │   │
│  └─────────────┘     └─────────────┘     └──────┬──────┘     └─────────────┘   │
│                                                  │                              │
│                    ┌─────────────────────────────┼─────────────────────────┐    │
│                    ▼                             ▼                         ▼    │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│  │   Redis     │  │   Qdrant    │  │   MinIO     │  │   Ollama    │  │  Celery     │
│  │  (Broker/   │  │  (VectorDB) │  │  (Object    │  │  (LLM/Emb/  │  │  Workers    │
│  │   Cache)    │  │             │  │   Storage)  │  │   Rerank)   │  │  (GPU)      │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘
│                                                                                  │
│  ┌─────────────────────────────────────────────────────────────────────────┐    │
│  │                    Monitoring Stack (任意)                                │    │
│  │  Prometheus + Grafana + Loki + Tempo + Alertmanager                      │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Docker Compose 本番構成

```yaml
# docker-compose.yml
version: '3.9'

services:
  # リバースプロキシ + SSL 終端
  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./nginx/conf.d:/etc/nginx/conf.d:ro
      - ./certbot/conf:/etc/letsencrypt:ro
      - ./certbot/www:/var/www/certbot:ro
    depends_on:
      - frontend
      - api
    restart: unless-stopped
    networks:
      - videomind-frontend
  
  # 静的フロントエンド
  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile.prod
    volumes:
      - frontend-dist:/usr/share/nginx/html:ro
    networks:
      - videomind-frontend
  
  # コア API サービス
  api:
    build:
      context: .
      dockerfile: Dockerfile.api
    env_file:
      - .env.prod
    environment:
      - DATABASE_URL=postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
      - REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0
      - QDRANT_URL=http://qdrant:6333
      - MINIO_ENDPOINT=minio:9000
      - OLLAMA_BASE_URL=http://ollama:11434
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      qdrant:
        condition: service_healthy
      minio:
        condition: service_healthy
    deploy:
      resources:
        limits:
          memory: 2G
        reservations:
          memory: 1G
    restart: unless-stopped
    networks:
      - videomind-backend
      - videomind-frontend
  
  # Celery Worker (GPU)
  worker-gpu:
    build:
      context: .
      dockerfile: Dockerfile.worker
    env_file:
      - .env.prod
    environment:
      - CELERY_QUEUE=gpu
      - WORKER_CONCURRENCY=1  # 単一 GPU で直列実行
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
    depends_on:
      - redis
      - ollama
    restart: unless-stopped
    networks:
      - videomind-backend
    volumes:
      - video-storage:/data/videos
      - model-cache:/root/.cache
  
  # Celery Worker (CPU 集約型：ダウンロード、インデックス作成、RAG 検索)
  worker-cpu:
    build:
      context: .
      dockerfile: Dockerfile.worker
    env_file:
      - .env.prod
    environment:
      - CELERY_QUEUE=cpu
      - WORKER_CONCURRENCY=4
    deploy:
      replicas: 2
      resources:
        limits:
          cpus: '2'
          memory: 4G
    depends_on:
      - redis
      - qdrant
    restart: unless-stopped
    networks:
      - videomind-backend
    volumes:
      - video-storage:/data/videos
  
  # PostgreSQL
  postgres:
    image: pgvector/pgvector:pg16
    env_file:
      - .env.prod
    environment:
      - POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256
    volumes:
      - postgres-data:/var/lib/postgresql/data
      - ./scripts/init-db.sql:/docker-entrypoint-initdb.d/init-db.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 10s
      timeout: 5s
      retries: 5
    deploy:
      resources:
        limits:
          memory: 2G
    restart: unless-stopped
    networks:
      - videomind-backend
  
  # Redis
  redis:
    image: redis:7-alpine
    command: redis-server --requirepass ${REDIS_PASSWORD} --maxmemory 1gb --maxmemory-policy allkeys-lru
    volumes:
      - redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "-a", "${REDIS_PASSWORD}", "ping"]
      interval: 10s
      timeout: 3s
      retries: 5
    deploy:
      resources:
        limits:
          memory: 1.5G
    restart: unless-stopped
    networks:
      - videomind-backend
  
  # Qdrant ベクトルデータベース
  qdrant:
    image: qdrant/qdrant:v1.8.0
    volumes:
      - qdrant-data:/qdrant/storage
    environment:
      - QDRANT__SERVICE__HTTP_PORT=6333
      - QDRANT__SERVICE__GRPC_PORT=6334
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:6333/health"]
      interval: 10s
      timeout: 5s
      retries: 5
    deploy:
      resources:
        limits:
          memory: 4G
    restart: unless-stopped
    networks:
      - videomind-backend
  
  # MinIO オブジェクトストレージ
  minio:
    image: minio/minio:RELEASE.2024-01-16T16-07-38Z
    command: server /data --console-address ":9001"
    env_file:
      - .env.prod
    environment:
      - MINIO_ROOT_USER=${MINIO_ROOT_USER}
      - MINIO_ROOT_PASSWORD=${MINIO_ROOT_PASSWORD}
    volumes:
      - minio-data:/data
    ports:
      - "9000:9000"
      - "9001:9001"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:9000/minio/health/live"]
      interval: 30s
      timeout: 20s
      retries: 3
    deploy:
      resources:
        limits:
          memory: 2G
    restart: unless-stopped
    networks:
      - videomind-backend
  
  # Ollama ローカル LLM サービス
  ollama:
    image: ollama/ollama:0.1.47
    volumes:
      - ollama-data:/root/.ollama
      - ./models:/models:ro  # 事前ダウンロード済みのモデルファイル
    environment:
      - OLLAMA_HOST=0.0.0.0:11434
      - OLLAMA_KEEP_ALIVE=24h  # モデルをメモリに常駐
      - OLLAMA_NUM_PARALLEL=1  # 単一 GPU で直列実行
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu]
        limits:
          memory: 10G
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:11434/api/tags"]
      interval: 30s
      timeout: 10s
      retries: 5
      start_period: 60s
    restart: unless-stopped
    networks:
      - videomind-backend
  
  # モニタリングスタック (任意、profile で有効化)
  prometheus:
    image: prom/prometheus:v2.47.0
    profiles: ["monitoring"]
    command:
      - '--config.file=/etc/prometheus/prometheus.yml'
      - '--storage.tsdb.path=/prometheus'
      - '--storage.tsdb.retention.time=30d'
      - '--web.enable-lifecycle'
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus-data:/prometheus
    ports:
      - "9090:9090"
    restart: unless-stopped
    networks:
      - videomind-monitoring
  
  grafana:
    image: grafana/grafana:10.1.0
    profiles: ["monitoring"]
    env_file:
      - .env.prod
    environment:
      - GF_SECURITY_ADMIN_USER=${GRAFANA_ADMIN_USER}
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_ADMIN_PASSWORD}
      - GF_INSTALL_PLUGINS=grafana-piechart-panel
    volumes:
      - ./monitoring/grafana/dashboards:/etc/grafana/provisioning/dashboards:ro
      - ./monitoring/grafana/datasources:/etc/grafana/provisioning/datasources:ro
      - grafana-data:/var/lib/grafana
    ports:
      - "3000:3000"
    depends_on:
      - prometheus
    restart: unless-stopped
    networks:
      - videomind-monitoring
  
  loki:
    image: grafana/loki:2.9.0
    profiles: ["monitoring"]
    command: -config.file=/etc/loki/local-config.yaml
    volumes:
      - ./monitoring/loki.yaml:/etc/loki/local-config.yaml:ro
      - loki-data:/loki
    ports:
      - "3100:3100"
    restart: unless-stopped
    networks:
      - videomind-monitoring
  
  tempo:
    image: grafana/tempo:2.3.0
    profiles: ["monitoring"]
    command: -config.file=/etc/tempo.yaml
    volumes:
      - ./monitoring/tempo.yaml:/etc/tempo.yaml:ro
      - tempo-data:/var/tempo
    ports:
      - "3200:3200"  # tempo
      - "4317:4317"  # otlp grpc
      - "4318:4318"  # otlp http
    restart: unless-stopped
    networks:
      - videomind-monitoring

volumes:
  postgres-data:
  redis-data:
  qdrant-data:
  minio-data:
  ollama-data:
  prometheus-data:
  grafana-data:
  loki-data:
  tempo-data:
  video-storage:
  model-cache:
  frontend-dist:

networks:
  videomind-frontend:
    driver: bridge
  videomind-backend:
    driver: bridge
  videomind-monitoring:
    driver: bridge
```

---

## 3. 環境変数の設定

```bash
# .env.prod (本番環境テンプレート。.env としてコピーし、実際の値を入力)

# ========== 基本 ==========
ENV=production
LOG_LEVEL=INFO
LOG_JSON=true
SECRET_KEY=your-256-bit-secret-key-here  # openssl rand -hex 32
API_HOST=0.0.0.0
API_PORT=8000
WORKER_CONCURRENCY=4

# ========== データベース ==========
POSTGRES_USER=videomind
POSTGRES_PASSWORD=changeme-postgres-password
POSTGRES_DB=videomind
DATABASE_URL=postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
DATABASE_POOL_SIZE=20
DATABASE_MAX_OVERFLOW=10

# ========== Redis ==========
REDIS_PASSWORD=changeme-redis-password
REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0
REDIS_MAX_CONNECTIONS=50

# ========== Qdrant ==========
QDRANT_URL=http://qdrant:6333
QDRANT_API_KEY=  # 任意
QDRANT_COLLECTION_PREFIX=vm_

# ========== MinIO ==========
MINIO_ENDPOINT=minio:9000
MINIO_ROOT_USER=videomind
MINIO_ROOT_PASSWORD=changeme-minio-password
MINIO_BUCKET_VIDEOS=videos
MINIO_BUCKET_THUMBNAILS=thumbnails
MINIO_BUCKET_MODELS=models
MINIO_SECURE=false  # 内部ネットワークは http、外部ネットワークは true + TLS が必要

# ========== Ollama ==========
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_MODELS=qwen2.5:7b,bge-m3,bge-reranker-v2-m3,qwen2.5:7b-instruct-q4_K_M

# ========== モデルゲートウェイ ==========
OPENAI_API_KEY=  # 任意、クラウドへのフォールバック
ANTHROPIC_API_KEY=  # 任意
DEEPSEEK_API_KEY=  # 任意

# ========== セキュリティ ==========
JWT_PRIVATE_KEY_PATH=/secrets/jwt_private.pem
JWT_PUBLIC_KEY_PATH=/secrets/jwt_public.pem
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=15
JWT_REFRESH_TOKEN_EXPIRE_DAYS=30
API_KEY_MASTER_KEY=your-32-byte-master-key  # openssl rand -hex 32
CORS_ORIGINS=https://videomind.com,https://app.videomind.com
ALLOWED_HOSTS=api.videomind.com,localhost

# ========== レートリミット ==========
RATE_LIMIT_GLOBAL_IP_PER_MIN=1000
RATE_LIMIT_USER_PER_MIN=200
RATE_LIMIT_APIKEY_PER_MIN=1000

# ========== 動画パイプライン ==========
YTDLP_FORMAT=bestvideo[height<=1080]+bestaudio/best[height<=1080]
FFMPEG_THREADS=4
WHISPER_MODEL=large-v3
WHISPER_DEVICE=cuda
WHISPER_COMPUTE_TYPE=float16
PADDLE_OCR_USE_GPU=true
SEGMENT_DURATION=60
KEYFRAME_INTERVAL=5

# ========== GPU ==========
GPU_MEMORY_FRACTION=0.9
GPU_VISIBLE_DEVICES=0
NVIDIA_VISIBLE_DEVICES=0

# ========== モニタリング ==========
OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4317
PROMETHEUS_PUSHGATEWAY=http://pushgateway:9091
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=changeme-grafana-password

# ========== メール/通知 ==========
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=alerts@videomind.com
SMTP_PASSWORD=changeme-smtp-password
ALERT_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx
```

---

## 4. Dockerfile 定義

```dockerfile
# Dockerfile.api
FROM python:3.11-slim AS base

# システム依存パッケージ
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python 依存パッケージ
WORKDIR /app
COPY requirements.txt requirements-lock.txt ./
RUN pip install --no-cache-dir -r requirements-lock.txt

# アプリケーションコード
COPY ./app ./app
COPY ./alembic ./alembic
COPY ./alembic.ini ./

# 非 root ユーザー
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# ヘルスチェック
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

```dockerfile
# Dockerfile.worker
FROM python:3.11-slim AS base

# システム依存パッケージ：FFmpeg + yt-dlp + GPU ドライバーライブラリ
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# yt-dlp のインストール
RUN curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp \
    && chmod +x /usr/local/bin/yt-dlp

WORKDIR /app
COPY requirements.txt requirements-lock.txt ./
RUN pip install --no-cache-dir -r requirements-lock.txt

COPY ./app ./app
COPY ./alembic ./alembic
COPY ./alembic.ini ./

# モデルキャッシュディレクトリ
RUN mkdir -p /root/.cache/huggingface /root/.cache/torch /models \
    && chmod 777 /root/.cache/huggingface /root/.cache/torch /models

USER root  # Worker は動画ファイルと GPU へのアクセスが必要

CMD ["celery", "-A", "app.tasks.celery_app", "worker", \
     "-Q", "${CELERY_QUEUE:-default}", \
     "-c", "${WORKER_CONCURRENCY:-4}", \
     "-l", "INFO", \
     "--max-tasks-per-child", "10", \
     "--prefetch-multiplier", "1"]
```

```dockerfile
# frontend/Dockerfile.prod
FROM node:20-alpine AS builder
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
```

---

## 5. GPU パススルーとモデルウォームアップ

```yaml
# docker-compose.gpu.yml (GPU 専用 override)
version: '3.9'

services:
  worker-gpu:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu, compute, utility]
        limits:
          cpus: '4'
          memory: 12G
    environment:
      - NVIDIA_VISIBLE_DEVICES=0
      - CUDA_VISIBLE_DEVICES=0
      - PYTORCH_CUDA_ALLOC_CONF=max_split_size_mb:128
    volumes:
      - /usr/lib/x86_64-linux-gnu/libnvidia-ml.so:/usr/lib/x86_64-linux-gnu/libnvidia-ml.so:ro
      - /usr/lib/x86_64-linux-gnu/libnvidia-encode.so:/usr/lib/x86_64-linux-gnu/libnvidia-encode.so:ro
  
  ollama:
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: 1
              capabilities: [gpu, compute, utility]
        limits:
          memory: 12G
    environment:
      - NVIDIA_VISIBLE_DEVICES=0
      - CUDA_VISIBLE_DEVICES=0
      - OLLAMA_NUM_PARALLEL=1
      - OLLAMA_MAX_LOADED_MODELS=2  # 同時にロードできるモデルは最大 2 個
      - OLLAMA_FLASH_ATTENTION=1
```

### モデルウォームアップスクリプト

```bash
#!/bin/bash
# scripts/warmup-models.sh
# コンテナ起動時にモデルを VRAM に事前ロードし、初回リクエストのコールドスタートを回避

set -e

OLLAMA_URL=${OLLAMA_BASE_URL:-http://localhost:11434}
MODELS=("qwen2.5:7b" "bge-m3" "bge-reranker-v2-m3" "qwen2.5:7b-instruct-q4_K_M")

echo "Waiting for Ollama to be ready..."
until curl -sf "$OLLAMA_URL/api/tags" > /dev/null; do
    sleep 2
done

for model in "${MODELS[@]}"; do
    echo "Pulling $model if not exists..."
    curl -sf -X POST "$OLLAMA_URL/api/pull" -d "{\"name\": \"$model\"}" || true
    
    echo "Warming up $model..."
    # 簡単なリクエストを送信してモデルのロードをトリガー
    curl -sf -X POST "$OLLAMA_URL/api/generate" -d "{
        \"model\": \"$model\",
        \"prompt\": \"Hello\",
        \"stream\": false,
        \"options\": {\"num_predict\": 1}
    }" > /dev/null
    
    echo "$model ready"
done

echo "All models warmed up!"
```

---

## 6. データベースマイグレーション

```bash
# マイグレーションコマンド（api コンテナ内で実行、または単体で実行）
# マイグレーションを生成
alembic revision --autogenerate -m "add video_segment table"

# マイグレーションを適用
alembic upgrade head

# ロールバック
alembic downgrade -1

# 履歴を確認
alembic history --verbose
```

```python
# alembic/env.py の主要設定
from app.core.config import settings
from app.models import Base

config = context.config
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

target_metadata = Base.metadata

def run_migrations_online():
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_schemas=True,
        )
        with context.begin_transaction():
            context.run_migrations()
```

---

## 7. バックアップとリストア

```bash
#!/bin/bash
# scripts/backup.sh
# PostgreSQL + MinIO + Qdrant + Redis を毎日自動バックアップ

set -e

BACKUP_DIR="/backups/$(date +%Y%m%d)"
mkdir -p "$BACKUP_DIR"

# 1. PostgreSQL 論理バックアップ
echo "Backing up PostgreSQL..."
docker exec videomind-postgres-1 pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    --no-owner --no-privileges --clean --if-exists | gzip > "$BACKUP_DIR/postgres.sql.gz"

# 2. MinIO データバックアップ (mc mirror を使用)
echo "Backing up MinIO..."
mc mirror --quiet minio/videos "$BACKUP_DIR/minio/videos"
mc mirror --quiet minio/thumbnails "$BACKUP_DIR/minio/thumbnails"

# 3. Qdrant スナップショット
echo "Backing up Qdrant..."
curl -X POST "http://qdrant:6333/collections/vm_chunks/snapshots" \
    -H "Content-Type: application/json"
# スナップショットの完了を待ってダウンロード
sleep 10
curl -o "$BACKUP_DIR/qdrant_snapshot.snap" "http://qdrant:6333/collections/vm_chunks/snapshots/latest"

# 4. Redis RDB (任意、通常は不要)
# docker exec videomind-redis-1 redis-cli -a "$REDIS_PASSWORD" BGSAVE

# 5. リモートストレージへアップロード (任意)
# rclone copy "$BACKUP_DIR" remote:videomind-backups/

# 6. 古いバックアップのクリーンアップ (30 日保持)
find /backups -type d -mtime +30 -exec rm -rf {} +

echo "Backup completed: $BACKUP_DIR"
```

---

## 8. スケールアウトガイド

| シナリオ | スケールアウト戦略 | 操作 |
|------|----------|------|
| API QPS が高い | api サービスを水平スケール | `docker compose up -d --scale api=3` + Nginx upstream による自動検出 |
| CPU Worker のタスク滞留 | worker-cpu レプリカを追加 | `docker compose up -d --scale worker-cpu=4` |
| GPU タスクの待ち時間が長い | GPU 物理カード + worker-gpu を追加 | GPU サーバーを追加し、Docker Swarm/K8s に参加 |
| ベクトル検索が遅い | Qdrant クラスタモード + シャーディング | qdrant の設定を変更してクラスタモードを有効化 |
| ストレージ不足 | MinIO 拡張 / NAS マウント | ディスクを増設、またはネットワークストレージを video-storage にマウント |
| 同時接続ユーザーが多い | Redis Cluster + 読み書き分離 | Redis Cluster をデプロイし、接続プール設定を変更 |

---

## 9. 本番チェックリスト

- [ ] **シークレット管理**：すべてのシークレットは Docker Secrets / Vault / Sealed Secrets で注入し、イメージ/コード/Compose には直書きしない
- [ ] **TLS**：Nginx に有効な証明書を設定（Let's Encrypt の自動更新）、HSTS を有効化
- [ ] **ネットワーク分離**：フロントエンド/バックエンド/モニタリングの 3 ネットワークを分離し、必要なポートのみ相互接続
- [ ] **リソース制限**：すべてのコンテナに `limits` と `reservations` を設定し、OOM を防止
- [ ] **ヘルスチェック**：すべての重要サービスに `healthcheck` を設定し、依存サービスには `condition: service_healthy` を使用
- [ ] **ログ収集**：標準出力の JSON ログを Loki/Promtail で自動収集
- [ ] **メトリクス公開**：`/metrics` エンドポイントは内部ネットワークからのみアクセス可能、Prometheus がスクレイプ
- [ ] **バックアップ検証**：毎週リストア手順をリハーサルし、RPO/RTO を確認
- [ ] **ローリング更新**：`docker compose up -d --no-deps api` でゼロダウンタイムデプロイ
- [ ] **ファイアウォール**：ホストマシンは 80/443/22 のみ開放し、内部サービスのポートはインターネットに公開しない
- [ ] **GPU モニタリング**：`nvidia-smi` のメトリクス収集、VRAM リークのアラート
- [ ] **モデルバージョン固定**：Ollama のイメージタグでバージョンを固定、モデルファイルは MinIO でバージョン管理

---

> **関連ドキュメント**：[ARCHITECTURE_JP.md](ARCHITECTURE_JP.md) · [OBSERVABILITY_JP.md](OBSERVABILITY_JP.md) · [SECURITY_JP.md](SECURITY_JP.md) · [TASK-ORCHESTRATION_JP.md](TASK-ORCHESTRATION_JP.md) · [VIDEO-PIPELINE_JP.md](VIDEO-PIPELINE_JP.md)