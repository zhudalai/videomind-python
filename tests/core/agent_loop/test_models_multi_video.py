"""AnalysisTask / AnalysisTaskMedia ORM 扩展断言（纯 dataclass 构造，不连 DB）."""

from __future__ import annotations

import uuid
from datetime import datetime


def test_analysis_task_has_media_ids_hash_field() -> None:
    from videomind.infrastructure.storage.models import AnalysisTask
    cols = {c.name for c in AnalysisTask.__table__.columns}
    assert "media_ids_hash" in cols
    assert "media_id" in cols  # primary 仍在
    cons = {c.name for c in AnalysisTask.__table__.constraints}
    assert "uq_at_mediaids_goal" in cons
    assert "uq_at_media_goal" not in cons


def test_analysis_task_media_model_shape() -> None:
    from videomind.infrastructure.storage.models import AnalysisTaskMedia
    cols = {c.name for c in AnalysisTaskMedia.__table__.columns}
    assert cols >= {"task_id", "media_id", "position", "created_at"}
    t = AnalysisTaskMedia(task_id=uuid.uuid4(), media_id=uuid.uuid4(), position=0)
    assert t.position == 0
    cons = {c.name for c in AnalysisTaskMedia.__table__.constraints
            if hasattr(c, "name") and c.name}
    assert "uq_atm_task_media" in cons