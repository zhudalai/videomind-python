"""L4 E2E 冒烟测试：真实下载 + 全链路（YouTube URL，可选，无网自动 skip）。

对应 docs/VIDEO-PIPELINE.md §2.1 下载阶段 + §2.2-2.5 半链。
仅在网络可达、yt-dlp 可用时运行；否则 pytest.skip。
"""

import pytest
import uuid
from pathlib import Path

from videomind.core.video_pipeline.download import get_downloader, DownloadResult
from videomind.core.video_pipeline.transcode import get_transcoder
from videomind.core.video_pipeline.asr import get_asr, save_transcription
from videomind.core.video_pipeline.ocr import get_ocr, save_frame_ocr
from videomind.core.video_pipeline.index import get_indexer
from videomind.infrastructure.storage import models as m
from videomind.infrastructure.storage import repository as repo
from videomind.config import get_settings


def _is_connection_error(exc: BaseException) -> bool:
    """判断是否为网络连接错误（守门用）。"""
    msg = str(exc).lower()
    return any(k in msg for k in (
        "connection refused", "connect call failed", "timeout",
        "name or service not known", "no route to host",
        "connection reset", "broken pipe", "eof",
        "asyncpg.exceptions", "qdrant_client.http.exceptions",
        "redis.exceptions.connectionerror", "minio.error",
        "httpx", "aiohttp", "ssl", "certificate verify failed"
    ))


async def _create_test_user(pg_session):
    """创建测试用户。"""
    unique_suffix = uuid.uuid4().hex[:8]
    user = m.User(
        id=uuid.uuid4(),
        username=f"test_user_{unique_suffix}",
        email=f"test_{unique_suffix}@test.com",
        password_hash="x",
    )
    pg_session.add(user)
    await pg_session.flush()
    return user.id


async def _create_test_media(pg_session, user_id: uuid.UUID, content_hash: str) -> m.MediaFile:
    """创建测试用 MediaFile 记录。"""
    source_url = f"https://example.com/test_{uuid.uuid4().hex[:8]}.mp4"
    media = await repo.create_media_file_pending(
        pg_session,
        source_url=source_url,
        user_id=user_id,
    )
    media.content_hash = content_hash
    await pg_session.commit()
    return media


@pytest.mark.e2e
class TestE2EDownloadAndPipeline:
    """E2E 真实下载 + 全链路冒烟（可选，skip 守门）。"""

    YOUTUBE_URL = "https://www.youtube.com/watch?v=U7QnRHJCBso"  # 用户提供的真实 URL

    async def test_download_youtube_then_half_chain(
        self, test_video_path, minio_client, temp_bucket, pg_session
    ):
        """下载 YouTube 视频 → transcode → asr → ocr → index → 验证 ready + 可检索。

        任一步骤网络不可达 → pytest.skip，不红。
        """
        # 设置测试用的 minio bucket
        from videomind.config import get_settings
        s = get_settings()
        original_bucket = s.minio_bucket
        s.minio_bucket = temp_bucket

        try:
            # 1. 创建用户
            user_id = await _create_test_user(pg_session)

            # 2. 下载 YouTube 视频（真实网络请求，守门）
            downloader = get_downloader()
            try:
                # 这里会走 yt-dlp，可能耗时 10-30s
                download_result: DownloadResult = await downloader.download(self.YOUTUBE_URL)
            except Exception as e:
                if _is_connection_error(e):
                    pytest.skip(f"网络不可达或 yt-dlp 失败，跳过真实下载测试: {e}")
                raise

            # 验证下载结果
            assert isinstance(download_result, DownloadResult)
            assert download_result.local_path.exists()
            assert download_result.local_path.stat().st_size > 0
            assert download_result.content_hash is not None
            assert len(download_result.content_hash) == 64  # SHA256 hex

            content_hash = download_result.content_hash
            # Use a unique test hash to avoid conflicts with previous test runs
            # (database may have committed data from prior runs that wasn't rolled back)
            # Replace last 8 chars of original hash with random hex to keep 64 chars but make unique
            unique_suffix = uuid.uuid4().hex[:8]
            test_content_hash = content_hash[:56] + unique_suffix  # 56 + 8 = 64

            print(f"DEBUG: original content_hash={content_hash}")
            print(f"DEBUG: test_content_hash={test_content_hash}")

            # Create MediaFile directly with the test content_hash
            media = m.MediaFile(
                user_id=user_id,
                source_type="url",
                source_url=self.YOUTUBE_URL,
                content_hash=test_content_hash,
                filename=download_result.filename,
                mime_type="video/mp4",
                file_size=download_result.local_path.stat().st_size,
                minio_bucket=temp_bucket,
                minio_object=f"videos/{test_content_hash}/original.mp4",
                status="pending",
            )
            pg_session.add(media)
            await pg_session.flush()
            media_id = media.id

            # 3. Transcode - use test_content_hash for MinIO keys
            transcoder = get_transcoder()
            tc_result = await transcoder.execute(download_result.local_path, test_content_hash)

            # 4. ASR
            asr = get_asr()
            asr_result = await asr.transcribe(tc_result.audio_local, media_id)
            tr_orm, chunk_orms = await save_transcription(pg_session, media_id, asr_result)
            await pg_session.flush()

            # 5. OCR
            ocr = get_ocr()
            ocr_results = await ocr.recognize_frames(media_id, tc_result.keyframes_minio)
            ocr_orms = await save_frame_ocr(pg_session, media_id, ocr_results)
            await pg_session.flush()

            # 6. Index
            indexer = get_indexer()
            index_result = await indexer.index(
                db=pg_session,
                media_id=media_id,
                transcription=tr_orm,
                chunks=chunk_orms,
                ocr_results=ocr_orms,
            )

            # 7. 断言全链路产出
            assert index_result.chunk_count >= 0
            assert index_result.qdrant_count == index_result.chunk_count

            # 验证 media_file.status = ready
            from sqlalchemy import select
            media_check = await pg_session.execute(
                select(m.MediaFile).where(m.MediaFile.id == media_id)
            )
            media_orm = media_check.scalar_one_or_none()
            assert media_orm is not None
            assert media_orm.status == "ready"
            assert media_orm.completed_at is not None

        finally:
            s.minio_bucket = original_bucket


@pytest.mark.e2e
class TestDownloaderReal:
    """Downloader 单独真实下载测试（可选）。"""

    YOUTUBE_URL = "https://www.youtube.com/watch?v=U7QnRHJCBso"

    async def test_download_youtube_returns_valid_result(
        self, test_video_path, minio_client, temp_bucket, pg_session
    ):
        """下载 YouTube → DownloadResult 结构正确，文件存在。"""
        from videomind.config import get_settings
        s = get_settings()
        original_bucket = s.minio_bucket
        s.minio_bucket = temp_bucket

        try:
            downloader = get_downloader()
            try:
                result: DownloadResult = await downloader.download(self.YOUTUBE_URL)
            except Exception as e:
                if _is_connection_error(e):
                    pytest.skip(f"网络不可达，跳过真实下载测试: {e}")
                raise

            assert isinstance(result, DownloadResult)
            assert result.local_path.exists()
            assert result.local_path.stat().st_size > 0
            assert result.content_hash is not None
            assert len(result.content_hash) == 64
            assert result.title is not None
            assert result.duration_ms is not None
            assert result.duration_ms > 0
        finally:
            s.minio_bucket = original_bucket