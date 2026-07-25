import asyncpg
import asyncio

async def test():
    try:
        conn = await asyncpg.connect('postgresql://videomind:videomind@localhost:15432/videomind')
        tables = await conn.fetch("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_name
        """)
        print('Tables in PostgreSQL:')
        for t in tables:
            print(f'  {t["table_name"]}')
        await conn.close()
    except Exception as e:
        print(f'Error: {e}')

asyncio.run(test())