"""FastAPI 应用工厂 —— VideoMind 接口层入口。

对应 docs/ARCHITECTURE.md Interface 层。
生命周期：启动 → 连接基础设施 → 挂载路由 → 监听；关闭 → 断开连接池。

使用方式:
    uv run uvicorn videomind.interface.app:app --reload
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from videomind.config import get_settings
from videomind.observability.metrics import get_metrics_app
from videomind.observability.middleware import PrometheusMiddleware
from videomind.observability.tracing import init_tracing

_settings = get_settings()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """应用生命周期：启动时校验基础设施连接；关闭时 dispose 连接池。"""
    # startup: 初始化追踪
    init_tracing("videomind-api")
    from videomind.infrastructure.storage.database import dispose_engine

    yield  # 应用运行中
    # shutdown
    await dispose_engine()


app = FastAPI(
    title=_settings.app_name,
    version="0.1.0",
    lifespan=_lifespan,
    docs_url="/docs" if _settings.is_dev else None,
    redoc_url=None,
)

# ── Prometheus 指标中间件（需在 CORS 之前）──
app.add_middleware(PrometheusMiddleware)

# CORS（开发阶段全开；生产从 .env 白名单）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _settings.is_dev else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Prometheus /metrics 端点 ──
metrics_app = get_metrics_app()
app.mount("/metrics", metrics_app)

# ── 路由挂载 ──
from videomind.interface.routes.health import router as health_router  # noqa: E402
from videomind.interface.routes.video import router as video_router  # noqa: E402
from videomind.interface.routes.sse import router as sse_router  # noqa: E402
from videomind.interface.routes.agent import router as agent_router  # noqa: E402
from videomind.interface.routes.rag import router as rag_router  # noqa: E402
from videomind.interface.routes.user import router as user_router  # noqa: E402

app.include_router(health_router, prefix="/api")
app.include_router(video_router, prefix="/api")
app.include_router(sse_router, prefix="/api")
app.include_router(agent_router, prefix="/api")
app.include_router(rag_router, prefix="/api")
app.include_router(user_router, prefix="/api")