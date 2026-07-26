"""L3 真实集成测试：/health/ready 各组件 UP。"""

import pytest
from fastapi.testclient import TestClient

from videomind.interface import app


@pytest.mark.infra
class TestHealthReady:
    """/api/health/ready 真实探测。"""

    def test_health_liveness(self):
        """GET /api/health 返回 UP。"""
        client = TestClient(app)
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "UP"

    def test_health_ready_all_up(self):
        """GET /api/health/ready 全部组件 UP。"""
        client = TestClient(app)
        resp = client.get("/api/health/ready")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "UP"
        for comp, status in data["components"].items():
            assert status == "UP", f"组件 {comp} 状态 {status}"