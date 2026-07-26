"""L3 真实集成测试：Repository MediaFile CRUD。"""

import pytest
import uuid

from videomind.infrastructure.storage import repository as repo


@pytest.mark.infra
class TestMediaFileRepository:
    """MediaFile 真实 DB 操作。"""

    async def _create_test_user(self, pg_session):
        """创建一个测试用户。"""
        from videomind.infrastructure.storage import models as m
        import uuid

        unique_suffix = uuid.uuid4().hex[:8]
        user = m.User(
            id=uuid.uuid4(),
            username=f"test_user_{unique_suffix}",
            email=f"test_{unique_suffix}@test.com",
            password_hash="test_hash",
        )
        pg_session.add(user)
        await pg_session.commit()
        return user.id

    async def test_get_or_create_by_hash_idempotent(self, pg_session):
        """同 content_hash 二次写入返回同一 id（ON CONFLICT 幂等）。"""
        user_id = await self._create_test_user(pg_session)
        source_url = f"https://example.com/video_{uuid.uuid4().hex[:8]}.mp4"

        # 第一次创建
        m1 = await repo.create_media_file_pending(
            pg_session,
            source_url=source_url,
            user_id=user_id,
        )
        await pg_session.commit()

        # 第二次同 hash 创建
        m2 = await repo.create_media_file_pending(
            pg_session,
            source_url=source_url,
            user_id=user_id,
        )
        await pg_session.commit()

        assert m1.id == m2.id
        assert m1.content_hash == m2.content_hash

    async def test_get_media_by_hash(self, pg_session):
        """按 hash 查询命中。"""
        user_id = await self._create_test_user(pg_session)
        source_url = f"https://example.com/v2_{uuid.uuid4().hex[:8]}.mp4"
        m = await repo.create_media_file_pending(
            pg_session,
            source_url=source_url,
            user_id=user_id,
        )
        await pg_session.commit()

        found = await repo.get_media_by_hash(pg_session, m.content_hash)
        assert found is not None
        assert found.id == m.id
        assert found.content_hash == m.content_hash

    async def test_get_media_by_url(self, pg_session):
        """按 source_url 查询命中。"""
        user_id = await self._create_test_user(pg_session)
        source_url = f"https://unique.example.com/video_{uuid.uuid4().hex[:8]}.mp4"
        m = await repo.create_media_file_pending(
            pg_session,
            source_url=source_url,
            user_id=user_id,
        )
        await pg_session.commit()

        found = await repo.get_media_by_url(pg_session, source_url)
        assert found is not None
        assert found.id == m.id
        assert found.source_url == source_url

    async def test_get_media_file_by_id(self, pg_session):
        """按主键查询。"""
        user_id = await self._create_test_user(pg_session)
        source_url = f"https://example.com/v3_{uuid.uuid4().hex[:8]}.mp4"
        m = await repo.create_media_file_pending(
            pg_session,
            source_url=source_url,
            user_id=user_id,
        )
        await pg_session.commit()

        found = await repo.get_media_file(pg_session, m.id)
        assert found is not None
        assert found.id == m.id


@pytest.mark.infra
class TestDirectAsyncSessionWrites:
    """直接用 AsyncSession 写 Chunk / Transcription（Repository 未覆盖模型）。"""

    async def _create_test_user(self, pg_session):
        from videomind.infrastructure.storage import models as m
        import uuid

        unique_suffix = uuid.uuid4().hex[:8]
        user = m.User(
            id=uuid.uuid4(),
            username=f"test_user_{unique_suffix}",
            email=f"test_{unique_suffix}@test.com",
            password_hash="x",
        )
        pg_session.add(user)
        await pg_session.commit()
        return user.id

    async def test_write_chunk_and_transcription(self, pg_session):
        """Chunk + Transcription 直接写入 smoke test。"""
        user_id = await self._create_test_user(pg_session)

        from videomind.infrastructure.storage import models as m

        # 准备 MediaFile
        source_url = f"https://example.com/v4_{uuid.uuid4().hex[:8]}.mp4"
        media = await repo.create_media_file_pending(
            pg_session,
            source_url=source_url,
            user_id=user_id,
        )
        await pg_session.commit()

        # 写 Chunk
        chunk = m.Chunk(
            media_id=media.id,
            chunk_index=0,
            content="测试文本内容",
            content_hash=f"f{uuid.uuid4().hex[:31]}",
            token_count=10,
            source_type="asr",
            qdrant_point_id=uuid.uuid4(),
        )
        pg_session.add(chunk)

        # 写 Transcription
        transcription = m.Transcription(
            media_id=media.id,
            full_text="完整转写文本",
            language="zh",
            model_name="whisper-large-v3",
            duration_sec=10.0,
            chunk_count=1,
        )
        pg_session.add(transcription)

        await pg_session.commit()

        # 验证回查
        from sqlalchemy import select

        chunks = await pg_session.execute(
            select(m.Chunk).where(m.Chunk.media_id == media.id)
        )
        assert len(chunks.scalars().all()) == 1

        trans = await pg_session.execute(
            select(m.Transcription).where(m.Transcription.media_id == media.id)
        )
        assert trans.scalar_one_or_none() is not None