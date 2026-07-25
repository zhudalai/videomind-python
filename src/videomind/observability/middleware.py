"""Prometheus FastAPI 中间件：自动收集 HTTP 请求指标。"""

from __future__ import annotations

import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from videomind.observability.metrics import HTTP_REQUEST_DURATION, HTTP_REQUEST_TOTAL


class PrometheusMiddleware(BaseHTTPMiddleware):
    """自动记录 HTTP 请求总数与延迟直方图。"""

    async def dispatch(self, request: Request, call_next):
        # 规范化路径（把 /api/videos/pipeline/uuid -> /api/videos/pipeline/{id}）
        path = self._normalize_path(request.url.path)

        method = request.method
        start = time.perf_counter()

        try:
            response: Response = await call_next(request)
            status = response.status_code
            return response
        except Exception:
            status = 500
            raise
        finally:
            duration = time.perf_counter() - start
            HTTP_REQUEST_TOTAL.labels(method=method, path=path, status=str(status)).inc()
            HTTP_REQUEST_DURATION.labels(method=method, path=path).observe(duration)

    @staticmethod
    def _normalize_path(path: str) -> str:
        """将动态路径参数归一化为模板形式。"""
        # 简单规则：UUID、数字 ID 替换为占位符
        import re

        # UUID 替换
        path = re.sub(
            r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
            "/{id}",
            path,
        )
        # 纯数字 ID 替换
        path = re.sub(r"/\d+(?=/|$)", "/{id}", path)
        return path