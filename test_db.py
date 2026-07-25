import asyncpg
import asyncio

async def test():
    try:
        conn = await asyncpg.connect('postgresql://videomind:videomind@localhost:15432/videomind')
        print('PostgreSQL connection OK')
        await conn.close()
    except Exception as e:
        print(f'PostgreSQL connection failed: {e}')

asyncio.run(test())