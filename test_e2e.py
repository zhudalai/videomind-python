import uvicorn
from multiprocessing import Process
import asyncio
import httpx
import time

def run_server():
    uvicorn.run("videomind.interface:app", host="127.0.0.1", port=8000, log_level="info", reload=False)

async def test_api():
    # Wait for server to start
    await asyncio.sleep(3)

    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=30.0) as client:
        # Health check
        r = await client.get("/api/health")
        print(f"Health: {r.status_code} - {r.json()}")

        # Ready check
        r = await client.get("/api/health/ready")
        print(f"Ready: {r.status_code} - {r.json()}")

        # Metrics
        r = await client.get("/metrics")
        print(f"Metrics: {r.status_code} - {len(r.text)} bytes")

        # Create pipeline task
        r = await client.post("/api/videos/pipeline", json={
            "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "title": "Test Video"
        })
        print(f"Pipeline: {r.status_code} - {r.json()}")

if __name__ == "__main__":
    server = Process(target=run_server)
    server.start()

    try:
        asyncio.run(test_api())
    finally:
        server.terminate()
        server.join()