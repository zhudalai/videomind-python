"""L3 真实集成测试：PostgreSQL 数据库连接与 alembic 建表验证。"""

import pytest
from sqlalchemy import text

from videomind.infrastructure.storage.database import db_session, engine


class TestDatabaseConnection:
    """真实 PG 连通性测试。"""

    @pytest.mark.asyncio
    async def test_db_session_select_one(self, pg_session):
        """db_session 真连 PG，SELECT 1 成功。"""
        result = await pg_session.execute(text("SELECT 1"))
        assert result.scalar() == 1

    @pytest.mark.asyncio
    async def test_engine_connectivity(self):
        """engine 直接连通。"""
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 42"))
            assert result.scalar() == 42


class TestAlembicTablesExist:
    """alembic upgrade head 后 22 张核心表存在。"""

    @pytest.mark.asyncio
    async def test_core_tables_exist(self, pg_session):
        """pg_tables 中包含 22 核心表。"""
        result = await pg_session.execute(
            text(
                """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            """
            )
        )
        tables = {row[0] for row in result.fetchall()}

        # 核心 18 张 + 扩展 4 张 = 22
        expected_core = {
            "user",
            "user_ai_config",
            "membership",
            "media_file",
            "video_segment",
            "transcription",
            "transcription_chunk",
            "frame_ocr",
            "chunk",
            "knowledge_base",
            "document",
            "analysis_task",
            "agent_checkpoint",
            "agent_result",
            "celery_task",
            "ingestion_task",
            "rag_trace",
            "session",
            # 扩展 4 张
            "api_key",
            "role",
            "user_role",
            "ai_call_logs",
        }
        missing = expected_core - tables
        assert not missing, f"缺表: {missing}"
        assert len(tables) >= 22


class TestDbSessionContextManager:
    """db_session 上下文管理器：异常时自动回滚。"""

    @pytest.mark.asyncio
    async def test_rollback_on_exception(self, pg_session):
        """异常抛出时 session 自动回滚。"""
        # 先插一条正常记录
        from videomind.infrastructure.storage import models as m
        import uuid

        unique_suffix = uuid.uuid4().hex[:8]
        user = m.User(
            id=uuid.uuid4(),
            username=f"test_rollback_{unique_suffix}",
            email=f"rollback_{unique_suffix}@test.com",
            password_hash="x",
        )
        pg_session.add(user)
        await pg_session.commit()

        # 再在同一个 session 里故意违约束，触发异常
        user2 = m.User(
            id=uuid.uuid4(),
            username=f"test_rollback_{unique_suffix}",  # 重复 username
            email=f"rollback2_{unique_suffix}@test.com",
            password_hash="x",
        )
        pg_session.add(user2)

        try:
            await pg_session.commit()
            pytest.fail("应抛出唯一约束异常")
        except Exception:
            await pg_session.rollback()

        # 验证第一条还在（回滚只影响第二条）
        from sqlalchemy import select

        result = await pg_session.execute(
            select(m.User).where(m.User.username == f"test_rollback_{unique_suffix}")
        )
        assert result.scalar_one_or_none() is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])