"""L4 E2E 冒烟测试：半链路小视频（主线必跑）。

流程：本地小视频 → transcode → asr → ocr → index → 验证整链 ready + Qdrant 可检索。

使用 @pytest.mark.e2e 标记，依赖全部基础设施在线。
"""

import pytest
import uuid
from pathlib import Path

from videomind.core.video_pipeline.transcode import get_transcoder
from videomind.core.video_pipeline.asr import get_asr, save_transcription
from videomind.core.video_pipeline.ocr import get_ocr, save_frame_ocr
from videomind.core.video_pipeline.index import get_indexer
from videomind.core.video_pipeline.embed import get_embedding_backend
from videomind.infrastructure.storage import models as m
from videomind.infrastructure.storage import repository as repo
from videomind.infrastructure.vector.qdrant import get_qdrant
from videomind.config import get_settings


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
    await pg_session.flush()
    return media


@pytest.mark.e2e
class TestE2EHalfChain:
    """半链路 E2E 冒烟测试（小 fixture 视频）。"""

    async def test_full_pipeline_half_chain_ready_and_searchable(
        self, test_video_path, minio_client, temp_bucket, pg_session
    ):
        """完整半链：transcode → asr → ocr → embed → index → 验证 ready + 可检索。"""
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
            await pg_session.flush()

            # 4. OCR
            ocr = get_ocr()
            ocr_results = await ocr.recognize_frames(media_id, tc_result.keyframes_minio)
            ocr_orms = await save_frame_ocr(pg_session, media_id, ocr_results)
            await pg_session.flush()

            # 5. Embedding（为 chunks 生成向量）
            embedder = get_embedding_backend()
            chunk_texts = [c.content for c in chunk_orms]
            if chunk_texts:
                embed_result = await embedder.embed(chunk_texts)
                # 回填向量到 chunk ORM（indexer 会用到）
                for i, vec in enumerate(embed_result.vectors):
                    chunk_orms[i].embedding = vec

            # 6. Index
            indexer = get_indexer()
            index_result = await indexer.index(
                db=pg_session,
                media_id=media_id,
                transcription=tr_orm,
                chunks=chunk_orms,
                ocr_results=ocr_orms,
            )

            # 7. 断言 IndexResult
            assert index_result.chunk_count >= 0
            assert index_result.qdrant_count == index_result.chunk_count

            # 8. 验证 Qdrant 有数据
            qdrant = get_qdrant()
            from videomind.infrastructure.vector.qdrant import make_qdrant_point_id
            if index_result.first_chunk_id:
                point = await qdrant.retrieve(
                    collection_name=qdrant._collection,
                    ids=[str(index_result.first_chunk_id)],
                )
                assert len(point) == 1
                assert point[0].payload["media_id"] == str(media_id)
                assert point[0].payload["content"] is not None

            # 9. 验证 PG chunk 表
            from sqlalchemy import select
            chunk_count = await pg_session.execute(
                select(m.Chunk).where(m.Chunk.media_id == media_id)
            )
            pg_chunks = chunk_count.scalars().all()
            assert len(pg_chunks) == index_result.chunk_count

            # 10. 验证 media_file.status = ready
            media_check = await pg_session.execute(
                select(m.MediaFile).where(m.MediaFile.id == media_id)
            )
            media_orm = media_check.scalar_one_or_none()
            assert media_orm is not None
            assert media_orm.status == "ready"
            assert media_orm.completed_at is not None

            # 11. 语义检索 smoke test（若有文本）
            if chunk_texts:
                query_vec = (await embedder.embed(["视频内容检索"])).vectors[0]
                hits = await qdrant.search(
                    collection_name=qdrant._collection,
                    query_vector=query_vec,
                    limit=5,
                    with_payload=True,
                )
                # 至少命中自己写入的
                assert len(hits) >= 1
                for h in hits:
                    assert h.payload["media_id"] == str(media_id)

        finally:
            s.minio_bucket = original_bucket

    async def test_empty_transcription_sets_ready_status(
        self, test_video_path, minio_client, temp_bucket, pg_session
    ):
        """无可索引文本（ASR 空 + OCR 空）时，media_file 仍置 ready，chunk_count=0。"""
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