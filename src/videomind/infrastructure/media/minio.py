"""MinIO 异步客户端封装 —— 对象存储（视频/音频/关键帧）。

minio-py 本身是同步阻塞的，这里用 `anyio.to_thread.run_sync` 包成 async，
避免阻塞 asyncio 事件循环。对应 docs/ARCHITECTURE.md Infrastructure 层。
"""

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path
from typing import Any

import anyio
from minio import Minio
from minio.error import S3Error

from videomind.config import get_settings


@lru_cache
def get_minio_client() -> "MinioClient":
    """获取 MinIO 客户端单例。"""
    return MinioClient()


class MinioClient:
    """MinIO 客户端封装：bucket 初始化 + 上传 / 下载 / 删除。

    所有 IO 操作通过 anyio.to_thread 让出事件循环，不阻塞 asyncio。
    """

    def __init__(self) -> None:
        s = get_settings()
        self._client = Minio(
            s.minio_endpoint,
            access_key=s.minio_access_key,
            secret_key=s.minio_secret_key,
            secure=s.minio_secure,
        )
        self._bucket = s.minio_bucket

    async def ensure_bucket(self) -> None:
        """启动时确保 bucket 存在（幂等）。"""
        exists = await anyio.to_thread.run_sync(
            self._client.bucket_exists, self._bucket
        )
        if not exists:
            await anyio.to_thread.run_sync(
                self._client.make_bucket, self._bucket
            )

    async def upload_file(self, object_key: str, file_path: str | Path) -> str:
        """上传本地文件到 MinIO。返回 object_key。"""
        p = Path(file_path)
        if not p.is_file():
            raise FileNotFoundError(p)

        def _do() -> None:
            self._client.fput_object(
                self._bucket, object_key, str(p)
            )

        await anyio.to_thread.run_sync(_do)
        return object_key

    async def upload_bytes(self, object_key: str, data: bytes, mime_type: str) -> str:
        """上传内存 bytes 到 MinIO。"""
        stream = io.BytesIO(data)

        def _do() -> None:
            self._client.put_object(
                self._bucket, object_key, stream, length=len(data),
                content_type=mime_type,
            )

        await anyio.to_thread.run_sync(_do)
        return object_key

    async def download_file(self, object_key: str, target_path: str | Path) -> Path:
        """下载 object 到本地路径。返回本地路径。"""
        target = Path(target_path)
        target.parent.mkdir(parents=True, exist_ok=True)

        def _do() -> None:
            self._client.fget_object(self._bucket, object_key, str(target))

        await anyio.to_thread.run_sync(_do)
        return target

    async def get_object_stream(self, object_key: str) -> Any:
        """获取 object 的可读流（调用方负责 close）。"""
        return await anyio.to_thread.run_sync(
            self._client.get_object, self._bucket, object_key
        )

    async def delete(self, object_key: str) -> None:
        try:
            await anyio.to_thread.run_sync(
                self._client.remove_object, self._bucket, object_key
            )
        except S3Error:
            # 删除不存在的对象视为幂等成功
            pass

    async def stat(self, object_key: str) -> Any | None:
        """获取对象元信息（不存在返回 None）。"""
        try:
            return await anyio.to_thread.run_sync(
                self._client.stat_object, self._bucket, object_key
            )
        except S3Error:
            return None


def build_media_object_key(content_hash: str, filename: str) -> str:
    """构造标准对象键：videos/{content_hash}/{filename}。"""
    return f"videos/{content_hash}/{filename}"


def build_audio_object_key(content_hash: str, chunk_index: int | None = None) -> str:
    """构造音频对象键：{content_hash}/audio.mp4 或音频分片 audio_chunk_{i}.ogg。"""
    if chunk_index is None:
        return f"{content_hash}/audio.ogg"
    return f"{content_hash}/audio_chunk_{chunk_index}.ogg"


def build_frame_object_key(content_hash: str, frame_ms: int) -> str:
    """构造关键帧对象键：{content_hash}/frames/{frame_ms}.jpg。"""
    return f"{content_hash}/frames/{frame_ms}.jpg"
