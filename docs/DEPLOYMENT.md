# VideoMind 部署设计

> Docker Compose 一键起、环境变量、GPU 直通、MinIO/Qdrant/Ollama 本地化部署
> 核心参考：Ragent `deploy/` + DOVideo-AI k8s 配置 + VidLens docker-compose + 生产级最佳实践

---

## 1. 部署架构

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                        VideoMind 单机/小集群部署拓扑                             │
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
│  │                    Monitoring Stack (可选)                                │    │
│  │  Prometheus + Grafana + Loki + Tempo + Alertmanager                      │    │
│  └─────────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Docker Compose 生产配置

```yaml
# docker-compose.yml
version: '3.9'

services:
  # 反向代理 + SSL 终止
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
  
  # 静态前端
  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile.prod
    volumes:
      - frontend-dist:/usr/share/nginx/html:ro
    networks:
      - videomind-frontend
  
  # 核心 API 服务
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
      - WORKER_CONCURRENCY=1  # 单卡串行
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
  
  # Celery Worker (CPU 密集型：下载、索引、RAG 检索)
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
  
  # Qdrant 向量数据库
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
  
  # MinIO 对象存储
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
  
  # Ollama 本地大模型服务
  ollama:
    image: ollama/ollama:0.1.47
    volumes:
      - ollama-data:/root/.ollama
      - ./models:/models:ro  # 预下载模型文件
    environment:
      - OLLAMA_HOST=0.0.0.0:11434
      - OLLAMA_KEEP_ALIVE=24h  # 模型常驻内存
      - OLLAMA_NUM_PARALLEL=1  # 单卡串行
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
  
  # 监控栈 (可选，通过 profile 启用)
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

## 3. 环境变量配置

```bash
# .env.prod (生产环境模板，复制为 .env 并填入真实值)

# ========== 基础 ==========
ENV=production
LOG_LEVEL=INFO
LOG_JSON=true
SECRET_KEY=your-256-bit-secret-key-here  # openssl rand -hex 32
API_HOST=0.0.0.0
API_PORT=8000
WORKER_CONCURRENCY=4

# ========== 数据库 ==========
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
QDRANT_API_KEY=  # 可选
QDRANT_COLLECTION_PREFIX=vm_

# ========== MinIO ==========
MINIO_ENDPOINT=minio:9000
MINIO_ROOT_USER=videomind
MINIO_ROOT_PASSWORD=changeme-minio-password
MINIO_BUCKET_VIDEOS=videos
MINIO_BUCKET_THUMBNAILS=thumbnails
MINIO_BUCKET_MODELS=models
MINIO_SECURE=false  # 内网 http，外网需 true + TLS

# ========== Ollama ==========
OLLAMA_BASE_URL=http://ollama:11434
OLLAMA_MODELS=qwen2.5:7b,bge-m3,bge-reranker-v2-m3,qwen2.5:7b-instruct-q4_K_M

# ========== 模型网关 ==========
OPENAI_API_KEY=  # 可选，云端兜底
ANTHROPIC_API_KEY=  # 可选
DEEPSEEK_API_KEY=  # 可选

# ========== 安全 ==========
JWT_PRIVATE_KEY_PATH=/secrets/jwt_private.pem
JWT_PUBLIC_KEY_PATH=/secrets/jwt_public.pem
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=15
JWT_REFRESH_TOKEN_EXPIRE_DAYS=30
API_KEY_MASTER_KEY=your-32-byte-master-key  # openssl rand -hex 32
CORS_ORIGINS=https://videomind.com,https://app.videomind.com
ALLOWED_HOSTS=api.videomind.com,localhost

# ========== 速率限流 ==========
RATE_LIMIT_GLOBAL_IP_PER_MIN=1000
RATE_LIMIT_USER_PER_MIN=200
RATE_LIMIT_APIKEY_PER_MIN=1000

# ========== 视频管线 ==========
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

# ========== 监控 ==========
OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4317
PROMETHEUS_PUSHGATEWAY=http://pushgateway:9091
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=changeme-grafana-password

# ========== 邮件/通知 ==========
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=alerts@videomind.com
SMTP_PASSWORD=changeme-smtp-password
ALERT_WEBHOOK_URL=https://open.feishu.cn/open-apis/bot/v2/hook/xxx
```

---

## 4. Dockerfile 定义

```dockerfile
# Dockerfile.api
FROM python:3.11-slim AS base

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖
WORKDIR /app
COPY requirements.txt requirements-lock.txt ./
RUN pip install --no-cache-dir -r requirements-lock.txt

# 应用代码
COPY ./app ./app
COPY ./alembic ./alembic
COPY ./alembic.ini ./

# 非 root 用户
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
```

```dockerfile
# Dockerfile.worker
FROM python:3.11-slim AS base

# 系统依赖：FFmpeg + yt-dlp + GPU 驱动库
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

# 安装 yt-dlp
RUN curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp \
    && chmod +x /usr/local/bin/yt-dlp

WORKDIR /app
COPY requirements.txt requirements-lock.txt ./
RUN pip install --no-cache-dir -r requirements-lock.txt

COPY ./app ./app
COPY ./alembic ./alembic
COPY ./alembic.ini ./

# 模型缓存目录
RUN mkdir -p /root/.cache/huggingface /root/.cache/torch /models \
    && chmod 777 /root/.cache/huggingface /root/.cache/torch /models

USER root  # Worker 需要访问视频文件和 GPU

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

## 5. GPU 直通与模型预热

```yaml
# docker-compose.gpu.yml (GPU 专用 override)
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
      - OLLAMA_MAX_LOADED_MODELS=2  # 同时最多加载 2 个模型
      - OLLAMA_FLASH_ATTENTION=1
```

### 模型预热脚本

```bash
#!/bin/bash
# scripts/warmup-models.sh
# 容器启动时预加载模型到显存，避免首次请求冷启动

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
    # 发送一个简单请求触发模型加载
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

## 6. 数据库迁移

```bash
# 迁移命令（在 api 容器内或单独运行）
# 生成迁移
alembic revision --autogenerate -m "add video_segment table"

# 执行迁移
alembic upgrade head

# 回滚
alembic downgrade -1

# 查看历史
alembic history --verbose
```

```python
# alembic/env.py 关键配置
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

## 7. 备份与恢复

```bash
#!/bin/bash
# scripts/backup.sh
# 每日自动备份 PostgreSQL + MinIO + Qdrant + Redis

set -e

BACKUP_DIR="/backups/$(date +%Y%m%d)"
mkdir -p "$BACKUP_DIR"

# 1. PostgreSQL 逻辑备份
echo "Backing up PostgreSQL..."
docker exec videomind-postgres-1 pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
    --no-owner --no-privileges --clean --if-exists | gzip > "$BACKUP_DIR/postgres.sql.gz"

# 2. MinIO 数据备份 (使用 mc mirror)
echo "Backing up MinIO..."
mc mirror --quiet minio/videos "$BACKUP_DIR/minio/videos"
mc mirror --quiet minio/thumbnails "$BACKUP_DIR/minio/thumbnails"

# 3. Qdrant 快照
echo "Backing up Qdrant..."
curl -X POST "http://qdrant:6333/collections/vm_chunks/snapshots" \
    -H "Content-Type: application/json"
# 等待快照完成并下载
sleep 10
curl -o "$BACKUP_DIR/qdrant_snapshot.snap" "http://qdrant:6333/collections/vm_chunks/snapshots/latest"

# 4. Redis RDB (可选，通常不需要)
# docker exec videomind-redis-1 redis-cli -a "$REDIS_PASSWORD" BGSAVE

# 5. 上传到远程存储 (可选)
# rclone copy "$BACKUP_DIR" remote:videomind-backups/

# 6. 清理旧备份 (保留 30 天)
find /backups -type d -mtime +30 -exec rm -rf {} +

echo "Backup completed: $BACKUP_DIR"
```

---

## 8. 扩容指南

| 场景 | 扩容策略 | 操作 |
|------|----------|------|
| API QPS 高 | 水平扩容 api 服务 | `docker compose up -d --scale api=3` + Nginx upstream 自动发现 |
| CPU Worker 积压 | 增加 worker-cpu 副本 | `docker compose up -d --scale worker-cpu=4` |
| GPU 任务排队久 | 增加 GPU 物理卡 + worker-gpu | 新增 GPU 服务器，加入 Docker Swarm/K8s |
| 向量检索慢 | Qdrant 集群模式 + 分片 | 修改 qdrant 配置启用集群模式 |
| 存储不足 | MinIO 扩容 / NAS 挂载 | 增加磁盘或挂载网络存储到 video-storage |
| 并发用户多 | Redis Cluster + 读写分离 | 部署 Redis Cluster，修改连接池配置 |

---

## 9. 生产核对清单

- [ ] **密钥管理**：所有密钥通过 Docker Secrets / Vault / Sealed Secrets 注入，不写在镜像/代码/Compose 中
- [ ] **TLS**：Nginx 配置有效证书（Let's Encrypt 自动续期），HSTS 开启
- [ ] **网络隔离**：前端/后端/监控 三个网络隔离，仅必要端口互通
- [ ] **资源限制**：所有容器配置 `limits` 和 `reservations`，防止 OOM
- [ ] **健康检查**：所有关键服务配置 `healthcheck`，依赖服务用 `condition: service_healthy`
- [ ] **日志收集**：标准输出 JSON 日志，Loki/Promtail 自动采集
- [ ] **指标暴露**：`/metrics` 端点仅内网访问，Prometheus 抓取
- [ ] **备份验证**：每周演练恢复流程，确认 RPO/RTO
- [ ] **滚动更新**：`docker compose up -d --no-deps api` 零停机部署
- [ ] **防火墙**：宿主机仅开放 80/443/22，内部服务端口不暴露公网
- [ ] **GPU 监控**：`nvidia-smi` 指标采集，显存泄漏告警
- [ ] **模型版本锁定**：Ollama 镜像标签固定版本，模型文件存入 MinIO 版本化管理

---

> **关联文档**：[ARCHITECTURE.md](ARCHITECTURE.md) · [OBSERVABILITY.md](OBSERVABILITY.md) · [SECURITY.md](SECURITY.md) · [TASK-ORCHESTRATION.md](TASK-ORCHESTRATION.md) · [VIDEO-PIPELINE.md](VIDEO-PIPELINE.md)