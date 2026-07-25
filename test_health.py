import asyncio
from sqlalchemy import text
from videomind.infrastructure.storage.database import AsyncSessionLocal
import redis.asyncio as aioredis
from qdrant_client import QdrantClient
from minio import Minio
from videomind.config import get_settings

async def check_postgres():
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        print("✅ PostgreSQL: UP")
    except Exception as e:
        print(f"❌ PostgreSQL: {e}")

async def check_redis():
    try:
        settings = get_settings()
        r = aioredis.from_url(settings.redis_url)
        await r.ping()
        await r.aclose()
        print("✅ Redis: UP")
    except Exception as e:
        print(f"❌ Redis: {e}")

async def check_qdrant():
    try:
        settings = get_settings()
        client = QdrantClient(url=settings.qdrant_url)
        client.collections_list()
        print("✅ Qdrant: UP")
    except Exception as e:
        print(f"❌ Qdrant: {e}")

async def check_minio():
    try:
        settings = get_settings()
        client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        client.bucket_exists(settings.minio_bucket)
        print("✅ MinIO: UP")
    except Exception as e:
        print(f"❌ MinIO: {e}")

async def main():
    await asyncio.gather(
        check_postgres(),
        check_redis(),
        check_qdrant(),
        check_minio(),
    )

asyncio.run(main())