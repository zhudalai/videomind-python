"""FastAPI 应用工厂 —— VideoMind 接口层入口。

对应 docs/ARCHITECTURE.md Interface 层。
生命周期：启动 → 连接基础设施 → 挂载路由 → 监听；关闭 → 断开连接池。

使用方式:
    uv run uvicorn videomind.interface:app --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from videomind.config import get_settings
from videomind.observability.metrics import get_metrics_app
from videomind.observability.middleware import PrometheusMiddleware
from videomind.observability.tracing import init_tracing

_settings = get_settings()
log = logging.getLogger(__name__)


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

# ── 全局兜底异常处理器（3.9）──
# 旧版未捕获异常回落到 Starlette 默认 plain-text "Internal Server Error"：客户端拿
# 不到可对账的线索，排障得人肉复现请求。统一结构化 500：error_code 机读、trace_id
# 与 structlog/OTel 日志交叉定位。detail 不回传 exc 原文——异常消息可能含内部
# 路径/连接串等敏感信息，真实细节走 trace_id 到日志里查。
# Prometheus 计数不受影响：PrometheusMiddleware 自己 except→记 500→re-raise，
# 异常穿透后仍由本 handler 生成响应。
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    from videomind.observability.tracing import get_current_trace_id

    log.exception("未处理异常 %s %s: %r", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal Server Error",
            "error_code": "INTERNAL_ERROR",
            "trace_id": get_current_trace_id(),
        },
    )

# ── Disable OpenAPI schema caching (dev reload needs fresh schema) ──
def _custom_openapi():
    from fastapi.openapi.utils import get_openapi
    return get_openapi(
        title=app.title,
        version=app.version,
        routes=app.routes,
    )
app.openapi = _custom_openapi