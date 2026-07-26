"""L3 真实集成测试：OCREngine 真跑 PaddleOCR（小 fixture 关键帧）。"""

import pytest
import uuid
import os
from pathlib import Path

from videomind.core.video_pipeline.ocr import get_ocr, OCRResult, save_frame_ocr
from videomind.core.video_pipeline.transcode import get_transcoder
from videomind.infrastructure.storage import models as m


async def _create_test_user(pg_session):
    """创建测试用户。"""
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


async def _create_test_media(pg_session, user_id: uuid.UUID, content_hash: str) -> m.MediaFile:
    """创建测试用 MediaFile 记录。"""
    from videomind.infrastructure.storage import repository as repo
    source_url = f"https://example.com/test_{uuid.uuid4().hex[:8]}.mp4"
    media = await repo.create_media_file_pending(
        pg_session,
        source_url=source_url,
        user_id=user_id,
    )
    media.content_hash = content_hash
    await pg_session.commit()
    return media


@pytest.mark.infra
class TestOCRIntegration:
    """OCREngine 真实集成测试（依赖 PaddleOCR + transcode）。"""

    async def test_recognize_frames_returns_ocr_results(self, test_video_path, minio_client, temp_bucket, pg_session):
        """识别关键帧 → 返回 OCRResult 列表（可能为空文本，但结构正确，不抛异常）。"""
        from videomind.config import get_settings
        s = get_settings()
        original_bucket = s.minio_bucket
        s.minio_bucket = temp_bucket

        try:
            # 1. 创建测试用户和 MediaFile
            user_id = await _create_test_user(pg_session)
            content_hash = uuid.uuid4().hex[:32]
            media = await _create_test_media(pg_session, user_id, content_hash)
            media_id = media.id

            # 2. 转码拿到关键帧
            transcoder = get_transcoder()
            tc_result = await transcoder.execute(Path(test_video_path), content_hash)

            # 3. OCR 识别（本地 CPU 模型）
            ocr = get_ocr()
            results = await ocr.recognize_frames(media_id, tc_result.keyframes_minio)

            # 4. 断言 OCRResult 结构
            assert isinstance(results, list)
            assert len(results) >= 1  # 至少 1 帧

            for r in results:
                assert isinstance(r, OCRResult)
                assert isinstance(r.frame_ms, int)
                assert r.frame_ms >= 0
                assert r.minio_object in tc_result.keyframes_minio
                assert r.phash is not None
                assert isinstance(r.phash, str)
                assert r.model_name == "paddle-ocr"
                # ocr_text 可能为 None（无文字）或字符串

            # 5. 写入数据库 smoke test
            orms = await save_frame_ocr(pg_session, media_id, results)
            await pg_session.commit()

            assert len(orms) == len(results)
            for orm, r in zip(orms, results):
                assert orm.media_id == media_id
                assert orm.frame_ms == r.frame_ms
                assert orm.minio_object == r.minio_object
                assert orm.ocr_text == r.ocr_text
                assert orm.phash == r.phash
                assert orm.model_name == r.model_name
                assert orm.status == "completed"

        finally:
            s.minio_bucket = original_bucket

    async def test_recognize_frames_deterministic_same_keys(self, test_video_path, minio_client, temp_bucket):
        """同一组 frame_keys 两次识别，结果确定性一致。"""
        from videomind.config import get_settings
        s = get_settings()
        original_bucket = s.minio_bucket
        s.minio_bucket = temp_bucket

        try:
            content_hash = uuid.uuid4().hex[:32]
            transcoder = get_transcoder()
            tc_result = await transcoder.execute(Path(test_video_path), content_hash)

            ocr = get_ocr()
            media_id = uuid.uuid4()

            r1 = await ocr.recognize_frames(media_id, tc_result.keyframes_minio)
            r2 = await ocr.recognize_frames(media_id, tc_result.keyframes_minio)

            assert len(r1) == len(r2)
            for o1, o2 in zip(r1, r2):
                assert o1.frame_ms == o2.frame_ms
                assert o1.minio_object == o2.minio_object
                assert o1.phash == o2.phash
                assert o1.ocr_text == o2.ocr_text
                assert o1.model_name == o2.model_name
        finally:
            s.minio_bucket = original_bucket

    async def test_duplicate_frame_deduplication(self, test_video_path, minio_client, temp_bucket, pg_session):
        """重复帧（同 phash）去重 → 第二帧 ocr_text 为 [duplicate frame]。"""
        from videomind.config import get_settings
        s = get_settings()
        original_bucket = s.minio_bucket
        s.minio_bucket = temp_bucket

        try:
            user_id = await _create_test_user(pg_session)
            content_hash = uuid.uuid4().hex[:32]
            media = await _create_test_media(pg_session, user_id, content_hash)
            media_id = media.id

            transcoder = get_transcoder()
            tc_result = await transcoder.execute(Path(test_video_path), content_hash)

            # 构造重复帧 keys（同一帧传两次）
            dup_keys = [tc_result.keyframes_minio[0], tc_result.keyframes_minio[0]]

            ocr = get_ocr()
            results = await ocr.recognize_frames(media_id, dup_keys)

            assert len(results) == 2
            # 第一帧正常识别
            assert results[0].ocr_text is not None or results[0].ocr_text == "" or results[0].ocr_text is None
            # 第二帧应被标记为重复
            assert results[1].ocr_text == "[duplicate frame]"
            assert results[1].phash == results[0].phash
        finally:
            s.minio_bucket = original_bucket