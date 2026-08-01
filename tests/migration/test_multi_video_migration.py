"""multi_video_agent 迁移应用状态断言（依赖 pg_session fixture 升级 head）。"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_migration_added_media_ids_hash_and_association_table(pg_session) -> None:
    """alembic upgrade head 后：analysis_task.media_ids_hash 列 + analysis_task_media 表 + 约束齐."""
    from videomind.infrastructure.storage.database import AsyncSessionLocal
    from sqlalchemy import text

    async with AsyncSessionLocal() as s:
        col = (await s.execute(text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='analysis_task' AND column_name='media_ids_hash'"))).all()
        assert len(col) == 1

        tab = (await s.execute(text("SELECT to_regclass('analysis_task_media')"))).scalar()
        assert tab == "analysis_task_media"

        idx = {r[0] for r in (await s.execute(text(
            "SELECT indexname FROM pg_indexes WHERE tablename='analysis_task_media'"))).all()}
        assert "ix_analysis_task_media_task_id" in idx
        assert "ix_analysis_task_media_media_id" in idx
        assert "uq_atm_task_media" in idx

        at_idx = {r[0] for r in (await s.execute(text(
            "SELECT indexname FROM pg_indexes WHERE tablename='analysis_task'"))).all()}
        assert "ix_analysis_task_media_ids_hash" in at_idx
        # 旧约束已去：uq_at_media_goal 不再存在
        old = (await s.execute(text(
            "SELECT conname FROM pg_constraint WHERE conname='uq_at_media_goal'"))).all()
        assert len(old) == 0
        new = (await s.execute(text(
            "SELECT conname FROM pg_constraint WHERE conname='uq_at_mediaids_goal'"))).all()
        assert len(new) == 1