"""L3 真实集成测试：数据库连接 + alembic 建表验证。"""

import pytest
from sqlalchemy import text


@pytest.mark.infra
class TestDatabaseConnection:
    """PostgreSQL 真连接测试（依赖 conftest.pg_session fixture）。"""

    async def test_select_one(self, pg_session):
        """最基础连通性：SELECT 1。"""
        result = await pg_session.execute(text("SELECT 1"))
        assert result.scalar_one() == 1

    async def test_pg_tables_exist(self, pg_session):
        """alembic upgrade head 后应建出 22 张表。"""
        result = await pg_session.execute(
            text(
                """
            SELECT count(*) FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        """
            )
        )
        count = result.scalar_one()
        # 核心 18 表 + 扩展 4 表 + user_config 1 + analysis_task_media 1 + alembic_version = 25
        assert count == 25, f"期望 25 张表，实际 {count}"


@pytest.mark.infra
class TestAlembicMigration:
    """Alembic 迁移验证。"""

    async def test_alembic_version_table_exists(self, pg_session):
        """alembic_version 表存在且有记录。"""
        result = await pg_session.execute(
            text("SELECT version_num FROM alembic_version")
        )
        version = result.scalar_one_or_none()
        assert version is not None, "alembic_version 表应有当前版本号"