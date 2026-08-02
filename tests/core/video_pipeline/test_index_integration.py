"""L3 真实集成测试：Indexer 真跑双写 Qdrant + PG chunk 表。"""

import pytest
import uuid
from pathlib import Path
from datetime import datetime, timezone

from videomind.core.video_pipeline.index import get_indexer, IndexResult
from videomind.core.video_pipeline.transcode import get_transcoder
from videomind.core.video_pipeline.asr import get_asr, save_transcription
from videomind.core.video_pipeline.ocr import get_ocr, save_frame_ocr
from videomind.infrastructure.storage import models as m
from videomind.infrastructure.storage import repository as repo
from videomind.infrastructure.vector.qdrant import get_qdrant


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
    await pg_session.commit()
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


@pytest.mark.infra
class TestIndexerIntegration:
    """Indexer 真实集成测试（依赖 transcode + asr + ocr + embed + Qdrant + PG）。"""

    async def test_index_writes_chunks_to_qdrant_and_pg(
        self, test_video_path, minio_client, temp_bucket, pg_session
    ):
        """完整半链：transcode → asr → ocr → index → 验证 Qdrant + PG chunk 表。"""
        from videomind.config import get_settings
        s = get_settings()
        original_bucket = s.minio_bucket
        s.minio_bucket = temp_bucket

        try:
            # 1. 准备用户和 MediaFile
            user_id = await _create_test_user(pg_session)
            content_hash = uuid.uuid4().hex[:32]
            media = await _create_test_media(pg_session, user_id, content_hash)
            media_id = media.id

            # 2. Transcode
            transcoder = get_transcoder()
            tc_result = await transcoder.execute(Path(test_video_path), content_hash)

            # 3. ASR
            asr = get_asr()
            asr_result = await asr.transcribe(tc_result.audio_local, media_id)
            tr_orm, chunk_orms = await save_transcription(pg_session, media_id, asr_result)
            await pg_session.commit()

            # 4. OCR
            ocr = get_ocr()
            ocr_results = await ocr.recognize_frames(media_id, tc_result.keyframes_minio)
            ocr_orms = await save_frame_ocr(pg_session, media_id, ocr_results)
            await pg_session.commit()

            # 5. Index
            indexer = get_indexer()
            index_result = await indexer.index(
                db=pg_session,
                media_id=media_id,
                transcription=tr_orm,
                chunks=chunk_orms,
                ocr_results=ocr_orms,
            )

            # 6. 断言 IndexResult
            assert isinstance(index_result, IndexResult)
            assert index_result.chunk_count >= 0
            assert index_result.qdrant_count == index_result.chunk_count

            # 7. 验证 Qdrant 有数据
            qdrant = get_qdrant()
            from videomind.infrastructure.vector.qdrant import make_qdrant_point_id
            if index_result.first_chunk_id:
                point = await qdrant.retrieve(
                    collection_name=qdrant._collection,
                    ids=[str(index_result.first_chunk_id)],
                )
                assert len(point) == 1
                assert point[0]["payload"]["media_id"] == str(media_id)
                assert point[0]["payload"]["content"] is not None

            # 8. 验证 PG chunk 表有数据
            from sqlalchemy import select
            chunk_count = await pg_session.execute(
                select(m.Chunk).where(m.Chunk.media_id == media_id)
            )
            pg_chunks = chunk_count.scalars().all()
            assert len(pg_chunks) == index_result.chunk_count

            # 9. 验证 media_file.status = ready
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

    async def test_index_repeated_is_idempotent_no_pkey_collision(
        self, pg_session
    ):
        """对同一 media_id 二次跑 Indexer.index，不应撞 chunk pkey。

        语义：chunk.id = uuid5(media_id + chunk_index) 是确定性的，重跑就是覆盖
        同一索引快照，而不是叠加。原实现直接 db.add() 会撞 PK failure_scenario：
        第一次成功后第二次 db.flush() → IntegrityError(UniqueViolation chunk.pkey)。
        修复：先 DELETE 该 media 的旧 chunk 行 + Qdrant 旧 points 再插入。

        本测不依赖真 ASR/OCR 真出文本（静音小视频可能 0 chunk），直接构造
        非空 transcription_chunk 行喂给 Indexer，确保 chunk_count > 0 才能验证
        重跑的 pkey 真实碰撞路径。
        """
        user_id = await _create_test_user(pg_session)
        content_hash = uuid.uuid4().hex[:32]
        media = await _create_test_media(pg_session, user_id, content_hash)
        media_id = media.id

        # 手工构造 1 个 transcription + 2 个非空 transcription_chunk（不跑 ASR）
        tr_orm = m.Transcription(
            media_id=media_id,
            full_text="第一段文本 第二段文本",
            language="zh",
            model_name="stub",
            duration_sec=2.0,
            chunk_count=2,
        )
        pg_session.add(tr_orm)
        await pg_session.flush()

        tc_0 = m.TranscriptionChunk(
            media_id=media_id, chunk_index=0,
            start_ms=0, end_ms=1000, text="第一段文本用于验证索引幂等",
            status="completed",
        )
        tc_1 = m.TranscriptionChunk(
            media_id=media_id, chunk_index=1,
            start_ms=1000, end_ms=2000, text="第二段文本同样要进入 Qdrant 与 chunk 表",
            status="completed",
        )
        chunk_orms = [tc_0, tc_1]
        for c in chunk_orms:
            pg_session.add(c)
        await pg_session.commit()

        indexer = get_indexer()
        # 第一次：应成功写出 chunk 行
        first = await indexer.index(
            db=pg_session, media_id=media_id,
            transcription=tr_orm, chunks=chunk_orms, ocr_results=[],
        )
        await pg_session.commit()
        assert first.chunk_count > 0, "本测需 chunk_count>0 才能验证重跑 pkey 碰撞路径"

        # 第二次：pkey uuid5(media_id:chunk_index) 与前一次完全相同
        try:
            second = await indexer.index(
                db=pg_session, media_id=media_id,
                transcription=tr_orm, chunks=chunk_orms, ocr_results=[],
            )
            await pg_session.commit()
            assert second.chunk_count == first.chunk_count
        except Exception as e:
            if "chunk_pkey" in str(e) or "UniqueViolation" in str(e) or "duplicate key" in str(e).lower():
                pytest.fail(
                    f"Index 重跑不应撞 chunk pkey，但实际抛: {type(e).__name__}: {e}"
                )
            raise

        from sqlalchemy import select, func as sa_func
        chunk_total = await pg_session.execute(
            select(sa_func.count(m.Chunk.id)).where(m.Chunk.media_id == media_id)
        )
        assert chunk_total.scalar_one() == first.chunk_count, (
            "重复 index 后该 media 下 chunk 行数应仍等于单次 index 产出量（覆盖语义），"
            " 不应翻倍"
        )

    async def test_index_empty_transcription_sets_ready_status(
        self, test_video_path, minio_client, temp_bucket, pg_session
    ):
        """无可索引文本（ASR 空 + OCR 空）时，media_file 仍置 ready，chunk_count=0。"""
        from videomind.config import get_settings
        s = get_settings()
        original_bucket = s.minio_bucket
        s.minio_bucket = temp_bucket

        try:
            user_id = await _create_test_user(pg_session)
            content_hash = uuid.uuid4().hex[:32]
            media = await _create_test_media(pg_session, user_id, content_hash)
            media_id = media.id

            # 只跑 transcode（拿到音频/帧），不跑 ASR/OCR（模拟空结果）
            transcoder = get_transcoder()
            tc_result = await transcoder.execute(Path(test_video_path), content_hash)

            # 造空 transcription
            tr_orm = m.Transcription(
                media_id=media_id,
                full_text="",
                language="zh",
                model_name="tiny",
                duration_sec=0.0,
                chunk_count=0,
            )
            pg_session.add(tr_orm)
            await pg_session.flush()

            # 空 chunks, 空 ocr
            indexer = get_indexer()
            index_result = await indexer.index(
                db=pg_session,
                media_id=media_id,
                transcription=tr_orm,
                chunks=[],
                ocr_results=[],
            )

            assert index_result.chunk_count == 0
            assert index_result.qdrant_count == 0

            # media 仍应置 ready
            from sqlalchemy import select
            media_check = await pg_session.execute(
                select(m.MediaFile).where(m.MediaFile.id == media_id)
            )
            media_orm = media_check.scalar_one_or_none()
            assert media_orm.status == "ready"

        finally:
            s.minio_bucket = original_bucket