"""L3 真实集成测试：Repository CRUD 与幂等性。"""

import pytest
import uuid

from videomind.infrastructure.storage import models as m
from videomind.infrastructure.storage.repository import (
    get_media_by_hash,
    get_media_by_url,
    create_media_file_pending,
    get_user_by_email,
    get_user_by_id,
)
from videomind.infrastructure.storage.database import db_session


class TestRepositoryMedia:
    """MediaFile 仓库真实写入/查询。"""

    @pytest.mark.asyncio
    async def test_create_media_pending_idempotent_same_hash(self, pg_session):
        """同 content_hash 二次创建返回同一记录（ON CONFLICT 幂等）。"""
        unique_id = uuid.uuid4().hex[:8]
        source_url = f"https://example.com/video_abc123_{unique_id}.mp4"

        # 第一次创建
        media1 = await create_media_file_pending(pg_session, source_url=source_url, user_id=None)
        await pg_session.commit()

        # 第二次创建（同 URL -> 同 content_hash）
        media2 = await create_media_file_pending(pg_session, source_url=source_url, user_id=None)
        await pg_session.commit()

        # 应返回同一行
        assert media2.id == media1.id
        assert media2.content_hash == media1.content_hash

    @pytest.mark.asyncio
    async def test_get_media_by_hash_returns_created(self, pg_session):
        """get_media_by_hash 能查到刚创建的记录。"""
        unique_id = uuid.uuid4().hex[:8]
        source_url = f"https://example.com/video_hash_test_{unique_id}.mp4"
        media = await create_media_file_pending(pg_session, source_url=source_url, user_id=None)
        await pg_session.commit()

        found = await get_media_by_hash(pg_session, media.content_hash)
        assert found is not None
        assert found.id == media.id
        assert found.content_hash == media.content_hash

    @pytest.mark.asyncio
    async def test_get_media_by_url_returns_created(self, pg_session):
        """get_media_by_url 能查到刚创建的记录。"""
        unique_id = uuid.uuid4().hex[:8]
        source_url = f"https://example.com/video_url_test_{unique_id}.mp4"
        media = await create_media_file_pending(pg_session, source_url=source_url, user_id=None)
        await pg_session.commit()

        found = await get_media_by_url(pg_session, source_url)
        assert found is not None
        assert found.id == media.id
        assert found.source_url == source_url

    @pytest.mark.asyncio
    async def test_create_media_with_user_id(self, pg_session):
        """创建时指定 user_id 正确写入（需先创建 User）。"""
        # 先创建 User
        import uuid
        from videomind.infrastructure.storage import models as m
        user_id = uuid.uuid4()
        unique_suffix = uuid.uuid4().hex[:8]
        unique_email = f"test_media_{unique_suffix}@example.com"
        unique_username = f"test_user_media_{unique_suffix}"
        user = m.User(
            id=user_id,
            username=unique_username,
            email=unique_email,
            password_hash="hashed",
        )
        pg_session.add(user)
        await pg_session.commit()

        source_url = f"https://example.com/video_user_{unique_suffix}.mp4"
        media = await create_media_file_pending(pg_session, source_url=source_url, user_id=user_id)
        await pg_session.commit()

        assert media.user_id == user_id


class TestRepositoryUser:
    """User 仓库真实写入/查询。"""

    @pytest.mark.asyncio
    async def test_create_and_get_user_by_email(self, pg_session):
        """创建 User 后可通过 email 查回。"""
        import uuid
        unique_suffix = uuid.uuid4().hex[:8]
        unique_email = f"test_user_{unique_suffix}@example.com"
        unique_username = f"test_user_{unique_suffix}"
        user = m.User(
            id=uuid.uuid4(),
            username=unique_username,
            email=unique_email,
            password_hash="hashed",
        )
        pg_session.add(user)
        await pg_session.commit()

        found = await get_user_by_email(pg_session, unique_email)
        assert found is not None
        assert found.id == user.id
        assert found.username == unique_username

    @pytest.mark.asyncio
    async def test_get_user_by_id(self, pg_session):
        """创建 User 后可通过 id 查回。"""
        import uuid
        unique_suffix = uuid.uuid4().hex[:8]
        unique_email = f"test_user2_{unique_suffix}@example.com"
        unique_username = f"test_user2_{unique_suffix}"
        user = m.User(
            id=uuid.uuid4(),
            username=unique_username,
            email=unique_email,
            password_hash="hashed",
        )
        pg_session.add(user)
        await pg_session.commit()

        found = await get_user_by_id(pg_session, user.id)
        assert found is not None
        assert found.email == unique_email


class TestRepositoryDirectSessionWrite:
    """直接用 AsyncSession 写 Chunk / Transcription（补 repository 未覆盖模型）。"""

    @pytest.mark.asyncio
    async def test_write_chunk_direct(self, pg_session):
        """直接写 Chunk 记录，验证字段持久化。"""
        import hashlib
        import uuid

        # 先创建 media_file（需要 content_hash）
        from videomind.infrastructure.storage.repository import create_media_file_pending
        unique_id = uuid.uuid4().hex[:8]
        source_url = f"https://example.com/video_chunk_{unique_id}.mp4"
        media = await create_media_file_pending(pg_session, source_url=source_url, user_id=None)
        await pg_session.commit()
        media_id = media.id

        content = "这是一段测试内容用于向量检索"
        content_hash = hashlib.md5(content.encode()).hexdigest()

        chunk = m.Chunk(
            id=uuid.uuid4(),
            media_id=media_id,
            chunk_index=0,
            content=content,
            content_hash=content_hash,
            token_count=len(content),
            start_ms=0,
            end_ms=5000,
            source_type="asr",
        )
        pg_session.add(chunk)
        await pg_session.commit()

        # 查回验证
        from sqlalchemy import select

        result = await pg_session.execute(
            select(m.Chunk).where(m.Chunk.content_hash == content_hash)
        )
        found = result.scalars().first()
        assert found is not None
        assert found.content == content
        assert found.token_count == len(content)
        assert found.source_type == "asr"

    @pytest.mark.asyncio
    async def test_write_transcription_direct(self, pg_session):
        """直接写 Transcription 记录。"""
        import uuid

        # 先创建 media_file
        from videomind.infrastructure.storage.repository import create_media_file_pending
        unique_id = uuid.uuid4().hex[:8]
        source_url = f"https://example.com/video_trans_{unique_id}.mp4"
        media = await create_media_file_pending(pg_session, source_url=source_url, user_id=None)
        await pg_session.commit()
        media_id = media.id

        trans = m.Transcription(
            id=uuid.uuid4(),
            media_id=media_id,
            full_text="完整转写文本",
            language="zh",
            model_name="whisper-large-v3",
            duration_sec=120.5,
            chunk_count=3,
        )
        pg_session.add(trans)
        await pg_session.commit()

        from sqlalchemy import select

        result = await pg_session.execute(
            select(m.Transcription).where(m.Transcription.media_id == media_id)
        )
        found = result.scalar_one_or_none()
        assert found is not None
        assert found.full_text == "完整转写文本"
        assert found.chunk_count == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])