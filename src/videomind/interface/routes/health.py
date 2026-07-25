"""健康检查路由 —— 检查所有基础设施连通性。

GET /api/health        → UP / DOWN
GET /api/health/ready   → 详细各服务状态（DB / Redis / Qdrant / MinIO）
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    """简易存活检查（K8s liveness probe）。"""
    return {"status": "UP", "version": "0.1.0"}


@router.get("/health/ready")
async def ready() -> dict:
    """就绪检查（K8s readiness probe）—— 检查所有基础设施连通性。"""
    components: dict[str, str] = {
        "postgres": await _check_postgres(),
        "redis": await _check_redis(),
        "qdrant": await _check_qdrant(),
        "minio": await _check_minio(),
    }
    all_up = all(v == "UP" for v in components.values())
    return {
        "status": "UP" if all_up else "DOWN",
        "components": components,
    }


# ── 组件健康检查实现 ──
async def _check_postgres() -> str:
    try:
        from videomind.infrastructure.storage.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return "UP"
    except Exception:
        return "DOWN"


async def _check_redis() -> str:
    try:
        from videomind.config import get_settings
        import redis.asyncio as aioredis

        settings = get_settings()
        r = aioredis.from_url(settings.redis_url)
        await r.ping()
        await r.aclose()
        return "UP"
    except Exception:
        return "DOWN"


async def _check_qdrant() -> str:
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.http.exceptions import UnexpectedResponse

        from videomind.config import get_settings

        settings = get_settings()
        client = QdrantClient(url=settings.qdrant_url)
        client.get_collections()
        return "UP"
    except Exception:
        return "DOWN"


async def _check_minio() -> str:
    try:
        from videomind.config import get_settings

        settings = get_settings()
        from minio import Minio

        client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        client.bucket_exists(settings.minio_bucket)
        return "UP"
    except Exception:
        return "DOWN"


from sqlalchemy import text  # noqa: E402