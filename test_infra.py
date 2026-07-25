import asyncio
import asyncpg
import redis.asyncio as redis
import httpx

async def test_postgres():
    try:
        conn = await asyncpg.connect('postgresql://videomind:videomind@localhost:15432/videomind')
        await conn.close()
        print('✅ PostgreSQL (15432): OK')
    except Exception as e:
        print(f'❌ PostgreSQL: {e}')

async def test_redis():
    try:
        r = redis.from_url('redis://localhost:16379/0')
        await r.ping()
        await r.close()
        print('✅ Redis (16379): OK')
    except Exception as e:
        print(f'❌ Redis: {e}')

async def test_qdrant():
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get('http://localhost:16333/collections')
            if resp.status_code == 200:
                print('✅ Qdrant (16333): OK')
            else:
                print(f'❌ Qdrant: {resp.status_code}')
    except Exception as e:
        print(f'❌ Qdrant: {e}')

async def test_minio():
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get('http://localhost:9000/minio/health/live')
            if resp.status_code == 200:
                print('✅ MinIO (9000): OK')
            else:
                print(f'❌ MinIO: {resp.status_code}')
    except Exception as e:
        print(f'❌ MinIO: {e}')

async def main():
    await asyncio.gather(
        test_postgres(),
        test_redis(),
        test_qdrant(),
        test_minio(),
    )

asyncio.run(main())