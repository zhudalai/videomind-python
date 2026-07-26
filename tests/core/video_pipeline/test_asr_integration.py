"""L3 真实集成测试：ASREngine 真跑 faster-whisper（小 fixture 音频）。"""

import pytest
import uuid
import os
from pathlib import Path

from videomind.core.video_pipeline.asr import get_asr, ASRResult, save_transcription
from videomind.core.video_pipeline.transcode import get_transcoder
from videomind.infrastructure.storage import repository as repo
from videomind.infrastructure.storage import models as m


async def _create_test_media(pg_session, user_id: uuid.UUID, content_hash: str) -> m.MediaFile:
    """创建测试用 MediaFile 记录。"""
    source_url = f"https://example.com/test_{uuid.uuid4().hex[:8]}.mp4"
    media = await repo.create_media_file_pending(
        pg_session,
        source_url=source_url,
        user_id=user_id,
    )
    # 手动设置 content_hash（正常由下载/转码阶段填）
    media.content_hash = content_hash
    await pg_session.commit()
    return media


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


@pytest.mark.infra
class TestASRIntegration:
    """ASREngine 真实集成测试（依赖 faster-whisper + transcode）。"""

    async def test_transcribe_sine_audio_returns_empty_text(self, test_video_path, minio_client, temp_bucket, pg_session):
        """纯正弦波音频无语音 → full_text 为空串可接受，关键是不抛异常且结构正确。"""
        from videomind.config import get_settings
        s = get_settings()
        original_bucket = s.minio_bucket
        s.minio_bucket = temp_bucket

        try:
            # 1. 先创建测试用户和 MediaFile
            user_id = await _create_test_user(pg_session)
            content_hash = uuid.uuid4().hex[:32]
            media = await _create_test_media(pg_session, user_id, content_hash)
            media_id = media.id

            # 2. 转码拿到音频
            transcoder = get_transcoder()
            tc_result = await transcoder.execute(Path(test_video_path), content_hash)

            # 3. ASR 识别（本地 tiny 模型，CPU）
            asr = get_asr()
            result = await asr.transcribe(tc_result.audio_local, media_id)

            # 4. 断言 ASRResult 结构
            assert isinstance(result, ASRResult)
            assert isinstance(result.full_text, str)
            assert isinstance(result.language, str)
            assert result.model_name == "tiny"  # .env 里 ASR_MODEL=tiny
            assert result.duration_sec >= 0
            assert isinstance(result.chunks, list)

            # 纯正弦波无语音，full_text 可能为空，这是预期行为（不抛异常即可）
            # chunks 也可能为空

            # 5. 写入数据库 smoke test
            tr_orm, chunk_orms = await save_transcription(pg_session, media_id, result)
            await pg_session.commit()

            assert tr_orm.media_id == media_id
            assert tr_orm.full_text == result.full_text
            assert tr_orm.language == result.language
            assert tr_orm.chunk_count == len(result.chunks)

        finally:
            s.minio_bucket = original_bucket

    async def test_transcribe_deterministic_for_same_audio(self, test_video_path, minio_client, temp_bucket):
        """同音频两次识别，结果一致（去除时间戳微小浮动）。"""
        from videomind.config import get_settings
        s = get_settings()
        original_bucket = s.minio_bucket
        s.minio_bucket = temp_bucket

        try:
            content_hash = uuid.uuid4().hex[:32]
            transcoder = get_transcoder()
            tc_result = await transcoder.execute(Path(test_video_path), content_hash)

            asr = get_asr()
            media_id = uuid.uuid4()

            r1 = await asr.transcribe(tc_result.audio_local, media_id)
            r2 = await asr.transcribe(tc_result.audio_local, media_id)

            # full_text 一致（纯正弦波通常都是空）
            assert r1.full_text == r2.full_text
            assert r1.language == r2.language
            assert r1.model_name == r2.model_name
            # chunks 结构一致（索引、时间戳、文本）
            assert len(r1.chunks) == len(r2.chunks)
            for c1, c2 in zip(r1.chunks, r2.chunks):
                assert c1["index"] == c2["index"]
                assert c1["start_ms"] == c2["start_ms"]
                assert c1["end_ms"] == c2["end_ms"]
                assert c1["text"] == c2["text"]
        finally:
            s.minio_bucket = original_bucket