"""POST /api/agent/analyze 多视频：建任务+关联行/幂等/乱序归一/404/409/422."""

from __future__ import annotations

import uuid as _uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from videomind.infrastructure.storage import models as m
from videomind.infrastructure.storage.database import AsyncSessionLocal
from videomind.interface import app


async def _seed(session, use_ready=True):
    """建 user + 2 ready media (+1 not-ready if use_ready)，返回 ids."""
    uid = _uuid.uuid4()
    m1, m2 = _uuid.uuid4(), _uuid.uuid4()
    nr = _uuid.uuid4()
    session.add(m.User(id=uid, username=f"u{uid.hex[:8]}",
        email=f"{uid.hex[:8]}@t.com", is_active=True, is_superuser=False,
        password_hash="x", full_name=None, phone=None,
        avatar_url=None, real_name=None))
    await session.flush()  # ensure user is inserted before media_files
    session.add(m.MediaFile(id=m1, user_id=uid, source_type="upload",
        content_hash=f"h{m1.hex[:8]}", filename="a.mp4", mime_type="video/mp4",
        file_size=1, duration_ms=1000, status="ready",
        minio_bucket="test-bucket", minio_object=f"videos/{m1.hex[:8]}/original.mp4"))
    session.add(m.MediaFile(id=m2, user_id=uid, source_type="upload",
        content_hash=f"h{m2.hex[:8]}", filename="b.mp4", mime_type="video/mp4",
        file_size=1, duration_ms=2000, status="ready",
        minio_bucket="test-bucket", minio_object=f"videos/{m2.hex[:8]}/original.mp4"))
    if use_ready:
        session.add(m.MediaFile(id=nr, user_id=uid, source_type="upload",
            content_hash=f"h{nr.hex[:8]}", filename="c.mp4", mime_type="video/mp4",
            file_size=1, duration_ms=3000, status="downloading",
            minio_bucket="test-bucket", minio_object=f"videos/{nr.hex[:8]}/original.mp4"))
    await session.commit()
    return uid, m1, m2, nr


@pytest.mark.asyncio
async def test_analyze_multi_creates_task_and_association_rows(pg_session) -> None:
    from videomind.interface.routes import agent as agent_mod

    uid, m1, m2, _nr = await _seed(pg_session)
    with patch.object(agent_mod, "_run_agent_loop", AsyncMock()):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.post("/api/agent/analyze", json={
                "goal": "g1", "media_ids": [str(m1), str(m2)],
                "user_id": str(uid), "max_rounds": 2})
    assert r.status_code == 202, r.text
    tid = r.json()["task_id"]
    async with AsyncSessionLocal() as s:
        cnt = (await s.execute(select(func.count()).select_from(m.AnalysisTaskMedia)
            .where(m.AnalysisTaskMedia.task_id == _uuid.UUID(tid)))).scalar()
        assert cnt == 2
        positions = sorted(r[0] for r in (await s.execute(
            select(m.AnalysisTaskMedia.position).where(
                m.AnalysisTaskMedia.task_id == _uuid.UUID(tid)))).all())
        assert positions == [0, 1]


@pytest.mark.asyncio
async def test_analyze_idempotent_reorders_reuse_same_task(pg_session) -> None:
    from videomind.interface.routes import agent as agent_mod

    uid, m1, m2, _nr = await _seed(pg_session)
    with patch.object(agent_mod, "_run_agent_loop", AsyncMock()):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            r1 = await c.post("/api/agent/analyze", json={
                "goal": "g1", "media_ids": [str(m1), str(m2)],
                "user_id": str(uid), "max_rounds": 2})
            r2 = await c.post("/api/agent/analyze", json={
                "goal": "g1", "media_ids": [str(m2), str(m1)],   # 乱序
                "user_id": str(uid), "max_rounds": 2})
    assert r1.status_code == 202 and r2.status_code == 202
    assert r1.json()["task_id"] == r2.json()["task_id"]


@pytest.mark.asyncio
async def test_analyze_rejects_not_ready_media_409(pg_session) -> None:
    from videomind.interface.routes import agent as agent_mod

    uid, _m1, _m2, nr = await _seed(pg_session)
    with patch.object(agent_mod, "_run_agent_loop", AsyncMock()):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.post("/api/agent/analyze", json={
                "goal": "g1", "media_ids": [str(nr)],
                "user_id": str(uid), "max_rounds": 2})
    assert r.status_code == 409
    assert str(nr) in r.json()["detail"]
    assert "未就绪" in r.json()["detail"]


@pytest.mark.asyncio
async def test_analyze_rejects_missing_media_404(pg_session) -> None:
    from videomind.interface.routes import agent as agent_mod

    uid, _m1, _m2, _nr = await _seed(pg_session)
    missing = _uuid.uuid4()
    with patch.object(agent_mod, "_run_agent_loop", AsyncMock()):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.post("/api/agent/analyze", json={
                "goal": "g1", "media_ids": [str(missing)],
                "user_id": str(uid), "max_rounds": 2})
    assert r.status_code == 404
    assert "not found" in r.json()["detail"]


@pytest.mark.asyncio
async def test_analyze_rejects_empty_media_ids_422(pg_session) -> None:
    from videomind.interface.routes import agent as agent_mod

    uid, _m1, _m2, _nr = await _seed(pg_session)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.post("/api/agent/analyze", json={
            "goal": "g1", "media_ids": [],
            "user_id": str(uid), "max_rounds": 2})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_analyze_accepts_max_rounds_3(pg_session) -> None:
    """max_rounds 上限限 2 → 3（合作方建议），critic 反馈可被真实吸收修正。

    回归：浏览器跑深度对比任务 max_rounds=3 被 Pydantic  422 挡死。
    """
    from videomind.interface.routes import agent as agent_mod

    uid, m1, m2, _nr = await _seed(pg_session)
    with patch.object(agent_mod, "_run_agent_loop", AsyncMock()):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.post("/api/agent/analyze", json={
                "goal": "g1", "media_ids": [str(m1), str(m2)],
                "user_id": str(uid), "max_rounds": 3})
    assert r.status_code == 202, r.text
    assert r.json()["max_rounds"] == 3


@pytest.mark.asyncio
async def test_analyze_rejects_max_rounds_4_still(pg_session) -> None:
    """max_rounds > 3 仍需拒绝（防止误用上限）。"""
    from videomind.interface.routes import agent as agent_mod

    uid, m1, m2, _nr = await _seed(pg_session)
    with patch.object(agent_mod, "_run_agent_loop", AsyncMock()):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.post("/api/agent/analyze", json={
                "goal": "g1", "media_ids": [str(m1), str(m2)],
                "user_id": str(uid), "max_rounds": 4})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_analyze_accepts_4_media_ids(pg_session) -> None:
    """media_ids 上限 限 2→4（多视频对比需求，2 不够覆盖三路视频）。

    回归：Pydantic max_length=2 硬限，跨多视频对比路由被 422 拒。
    """
    from videomind.interface.routes import agent as agent_mod

    uid = _uuid.uuid4()
    mids = [_uuid.uuid4() for _ in range(4)]
    pg_session.add(m.User(id=uid, username=f"u{uid.hex[:8]}",
        email=f"{uid.hex[:8]}@t.com", is_active=True, is_superuser=False,
        password_hash="x", full_name=None, phone=None,
        avatar_url=None, real_name=None))
    await pg_session.flush()
    for i, mid in enumerate(mids):
        pg_session.add(m.MediaFile(id=mid, user_id=uid, source_type="upload",
            content_hash=f"h{mid.hex[:8]}", filename=f"v{i}.mp4", mime_type="video/mp4",
            file_size=1, duration_ms=1000 * (i + 1), status="ready",
            minio_bucket="test-bucket", minio_object=f"videos/{mid.hex[:8]}/original.mp4"))
    await pg_session.commit()

    with patch.object(agent_mod, "_run_agent_loop", AsyncMock()):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.post("/api/agent/analyze", json={
                "goal": "g1",
                "media_ids": [str(x) for x in mids],
                "user_id": str(uid), "max_rounds": 3})
    assert r.status_code == 202, r.text