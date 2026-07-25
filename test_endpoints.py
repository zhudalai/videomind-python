import asyncio
import httpx
from videomind.interface import app
import uvicorn
from multiprocessing import Process
import time

def run_server():
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")

async def test_endpoints():
    # Wait for server to start
    await asyncio.sleep(2)

    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000") as client:
        # Test health endpoint
        r = await client.get("/api/health")
        print(f"Health: {r.status_code} - {r.json()}")

        # Test ready endpoint
        r = await client.get("/api/health/ready")
        print(f"Ready: {r.status_code} - {r.json()}")

        # Test metrics endpoint
        r = await client.get("/metrics")
        print(f"Metrics: {r.status_code}, length: {len(r.text)}")

        # Test video pipeline endpoint
        r = await client.post("/api/videos/pipeline", json={
            "source_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "title": "Test Video"
        })
        print(f"Pipeline: {r.status_code} - {r.json()}")

if __name__ == "__main__":
    # Start server in background
    server_process = Process(target=run_server)
    server_process.start()

    try:
        asyncio.run(test_endpoints())
    finally:
        server_process.terminate()
        server_process.join()