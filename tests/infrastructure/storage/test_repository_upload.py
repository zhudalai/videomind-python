"""L1+L3 for repository.create_media_file_from_upload — idempotent upload path."""

from __future__ import annotations

import hashlib
import uuid as _uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession


# ──────────────────────────── 纯逻辑（无 infra） ────────────────────────────
class TestUploadContentHashDerivation:
    """SHA256(content_hex) 必须稳定，相同 bytes → 同 hash。"""

    def test_sha256_stable_hex(self):
        data = b"hello-world" * 16
        h = hashlib.sha256(data).hexdigest()
        assert len(h) == 64
        assert h == hashlib.sha256(data).hexdigest()  # idempotent

    def test_sha256_changes_with_byte(self):
        h1 = hashlib.sha256(b"a").hexdigest()
        h2 = hashlib.sha256(b"b").hexdigest()
        assert h1 != h2


# ──────────────────────────── 真实集成（需 PG） ────────────────────────────
@pytest.mark.asyncio
class TestRepositoryCreateFromUpload:
    """create_media_file_from_upload 必须 idempotent on content_hash + 支持 upload 类型。"""

    async def test_create_returns_media_with_upload_source_type(self, pg_session: AsyncSession):
        from videomind.infrastructure.storage.repository import (
            create_media_file_from_upload,
        )

        content_hash = "h" * 64  # dummy; 真实场景 calculator
        filename = "demo.mp4"
        bucket = "videomind"
        key = f"videos/{content_hash}/original.mp4"

        try:
            media = await create_media_file_from_upload(
                pg_session,
                content_hash=content_hash,
                filename=filename,
                mime_type="video/mp4",
                file_size=12345,
                bucket=bucket,
                object_key=key,
                # user_id 留空 —— 测 user_id 非必需时不引入 user 表 FK 噪音
            )
            await pg_session.commit()

            assert media.source_type == "upload"
            assert media.content_hash == content_hash
            assert media.filename == filename
            assert media.mime_type == "video/mp4"
            assert media.file_size == 12345
            assert media.minio_bucket == bucket
            assert media.minio_object == key
            assert media.status == "pending"
            # source_url 应保持空（upload 场景无 URL）
            assert media.source_url is None
        finally:
            # 清理：避免污染后续测试
            from sqlalchemy import delete
            from videomind.infrastructure.storage import models as mm

            await pg_session.execute(
                delete(mm.MediaFile).where(mm.MediaFile.content_hash == content_hash)
            )
            await pg_session.commit()

    async def test_create_idempotent_on_content_hash(self, pg_session: AsyncSession):
        from videomind.infrastructure.storage.repository import (
            create_media_file_from_upload,
        )
        from sqlalchemy import delete
        from videomind.infrastructure.storage import models as mm

        content_hash = "idempot-" + "h" * (64 - 9)

        try:
            m1 = await create_media_file_from_upload(
                pg_session,
                content_hash=content_hash,
                filename="first.mp4",
                mime_type="video/mp4",
                file_size=100,
                bucket="videomind",
                object_key=f"videos/{content_hash}/original.mp4",
            )
            await pg_session.commit()

            # 相同 hash 不同 filename → 仍应返回同一行（不创建副本）
            m2 = await create_media_file_from_upload(
                pg_session,
                content_hash=content_hash,
                filename="different.mp4",
                mime_type="video/mp4",
                file_size=200,
                bucket="videomind",
                object_key=f"videos/{content_hash}/original.mp4",
            )
            await pg_session.commit()

            assert m1.id == m2.id, "相同 content_hash 必须返回同一行（同 id）"
        finally:
            await pg_session.execute(
                delete(mm.MediaFile).where(mm.MediaFile.content_hash == content_hash)
            )
            await pg_session.commit()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
