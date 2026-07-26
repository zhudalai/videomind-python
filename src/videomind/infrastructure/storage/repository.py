"""仓储层 —— 对 SQLAlchemy session 封装 CRUD 操作。

对应 docs/DATA-MODEL.md §5 Pydantic↔ORM 映射规范。
职责：
    1. 隔离业务层与 ORM 细节（查询构造、连接关系）。
    2. 返回 ORM 实例，Pydantic schema 转换放 route/service 层。
    3. 写操作返回 ORM 对象（含自动生成的 id/timestamps），调用方 commit。
"""

from __future__ import annotations

import uuid
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from videomind.infrastructure.storage import models as m


# ──────────────────────────── Media ────────────────────────────


async def get_media_file(db: AsyncSession, media_id: uuid.UUID) -> m.MediaFile | None:
    result = await db.execute(select(m.MediaFile).where(m.MediaFile.id == media_id))
    return result.scalar_one_or_none()


async def get_media_by_hash(db: AsyncSession, content_hash: str) -> m.MediaFile | None:
    result = await db.execute(
        select(m.MediaFile).where(m.MediaFile.content_hash == content_hash)
    )
    return result.scalar_one_or_none()


async def get_media_by_url(db: AsyncSession, source_url: str) -> m.MediaFile | None:
    result = await db.execute(
        select(m.MediaFile).where(m.MediaFile.source_url == source_url)
    )
    return result.scalar_one_or_none()


async def create_media_file_pending(
    db: AsyncSession,
    *,
    source_url: str,
    user_id: uuid.UUID | None = None,
) -> m.MediaFile:
    """创建一条 pending 状态的 media_file 记录（占位，元数据在下载阶段填充）。

    如果已存在相同 content_hash 的记录，直接返回现有记录（幂等性）。
    使用 PostgreSQL ON CONFLICT 避免 SQLAlchemy prepared statement cache 问题。
    """
    import hashlib
    from sqlalchemy.dialects.postgresql import insert

    content_hash = hashlib.sha256(source_url.encode()).hexdigest()

    # 使用 PostgreSQL upsert (ON CONFLICT DO NOTHING) + 返回已存在行
    stmt = (
        insert(m.MediaFile)
        .values(
            user_id=user_id,
            source_type="url",
            source_url=source_url,
            content_hash=content_hash,
            filename=source_url.rsplit("/", 1)[-1][:256] or "untitled",
            mime_type="video/mp4",
            file_size=0,
            minio_bucket="videomind",
            minio_object=f"videos/{content_hash}/original.mp4",
            status="pending",
        )
        .on_conflict_do_nothing(index_elements=[m.MediaFile.content_hash])
        .returning(m.MediaFile)
    )
    result = await db.execute(stmt)
    media = result.scalar_one_or_none()

    if media is None:
        # 冲突时没有返回行，查已存在的记录
        existing = await get_media_by_hash(db, content_hash)
        if existing:
            return existing
        # 理论上不该走到这里，但兜底
        raise RuntimeError("Failed to create or fetch media_file")

    return media


async def create_media_file_from_upload(
    db: AsyncSession,
    *,
    content_hash: str,
    filename: str,
    mime_type: str,
    file_size: int,
    bucket: str,
    object_key: str,
    user_id: uuid.UUID | None = None,
) -> m.MediaFile:
    """为本地文件上传创建 media_file 记录（单条插入路径，幂等 by content_hash）。

    与 create_media_file_pending 的差别（URL 路径）：
    - source_type = 'upload'（而非 'url'）
    - source_url = None（上传场景无 URL）
    - content_hash 由上传字节算出（已是真 SHA256，不重算）
    - filename 来自客户端原始上传名
    - file_size 为真实上传字节数（非 0）
    - minio_bucket / minio_object 由调用方指定（已落到对象存储）

    返回：ORM 实例。commit 由调用方负责。
    """
    from sqlalchemy.dialects.postgresql import insert

    safe_filename = (filename or "upload.bin")[:256]

    stmt = (
        insert(m.MediaFile)
        .values(
            user_id=user_id,
            source_type="upload",
            source_url=None,
            content_hash=content_hash,
            filename=safe_filename,
            mime_type=mime_type,
            file_size=file_size,
            minio_bucket=bucket,
            minio_object=object_key,
            status="pending",
        )
        .on_conflict_do_nothing(index_elements=[m.MediaFile.content_hash])
        .returning(m.MediaFile)
    )
    result = await db.execute(stmt)
    media = result.scalar_one_or_none()

    if media is None:
        # 冲突已存在行 → 取回已有记录
        existing = await get_media_by_hash(db, content_hash)
        if existing:
            return existing
        raise RuntimeError("Failed to create or fetch media_file")

    return media


async def list_media_by_user(
    db: AsyncSession, user_id: uuid.UUID, *, limit: int = 20, offset: int = 0
) -> Sequence[m.MediaFile]:
    result = await db.execute(
        select(m.MediaFile)
        .where(m.MediaFile.user_id == user_id)
        .order_by(m.MediaFile.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    return result.scalars().all()


# ──────────────────────────── User ────────────────────────────


async def get_user_by_email(db: AsyncSession, email: str) -> m.User | None:
    result = await db.execute(select(m.User).where(m.User.email == email))
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> m.User | None:
    result = await db.execute(select(m.User).where(m.User.id == user_id))
    return result.scalar_one_or_none()