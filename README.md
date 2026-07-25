# VideoMind

VideoMind — 视频理解 Agent 系统（求职作品集实现，Python 版）

## 架构

- **Interface 层**: FastAPI + SSE 实时进度
- **Application 层**: Celery 任务编排 + GPU 资源管理
- **Core Services**: 视频管线 / RAG 检索 / 意图路由
- **Infrastructure**: PostgreSQL+pgvector / Qdrant / MinIO / Redis

## 快速开始

```bash
# 安装依赖
uv sync

# 启动基础设施
docker compose -f docker/docker-compose.yml up -d

# 运行数据库迁移
alembic upgrade head

# 启动 API 服务
uv run uvicorn videomind.interface.app:app --reload

# 启动 Celery Worker
uv run celery -A videomind.application.task_orchestration.celery worker -Q gpu -c 1
uv run celery -A videomind.application.task_orchestration.celery worker -Q cpu -c 4
```

## API 文档

启动后访问：http://localhost:8000/docs

## 环境变量

见 `.env.example`，复制为 `.env` 并修改。

## 许可证

MIT