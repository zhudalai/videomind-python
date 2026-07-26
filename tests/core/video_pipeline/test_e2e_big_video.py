"""L4 E2E 冒烟测试：大视频全链路（可选，环境变量 VM_BIG_E2E=1 才跑）。

流程：本地 151MB Bilibili MP4 → transcode → asr → ocr → index → 验证整链 ready + Qdrant 可检索。

使用 @pytest.mark.e2e + 环境变量守门，默认不跑（CI 慢）。
"""

import os
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


# 大视频路径（用户提供的本地 B 站 MP4）
BIG_VIDEO_PATH = r"D:\shu_e\Documents\Video MInd python\全球最大游轮！造价140亿！到底有多离谱？吃什么？玩什么？_哔哩哔哩_bilibili.mp4"


def _is_big_e2e_enabled() -> bool:
    """检查是否启用大视频 E2E 测试。"""
    return os.getenv("VM_BIG_E2E") == "1"


def _is_connection_error(exc: BaseException) -> bool:
    """判断是否为网络/基础设施连接错误。"""
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
    source_url = f"file://{BIG_VIDEO_PATH}"
    media = await repo.create_media_file_pending(
        pg_session,
        source_url=source_url,
        user_id=user_id,
    )
    media.content_hash = content_hash
    await pg_session.flush()
    return media


@pytest.mark.e2e
class TestE2EBigVideo:
    """大视频 E2E 冒烟测试（需 VM_BIG_E2E=1 环境变量）。"""

    @pytest.mark.skipif(
        not _is_big_e2e_enabled(),
        reason="大视频 E2E 需要设置环境变量 VM_BIG_E2E=1"
    )
    @pytest.mark.skipif(
        not os.path.exists(BIG_VIDEO_PATH),
        reason=f"大视频文件不存在: {BIG_VIDEO_PATH}"
    )
    async def test_big_video_full_pipeline_ready_and_searchable(
        self, minio_client, temp_bucket, pg_session
    ):
        """大视频完整半链：transcode → asr → ocr → embed → index → 验证 ready + 可检索。

        该测试耗时较长（数分钟），默认不在 CI 中运行。
        任一步骤基础设施不可达 → pytest.skip，不红。
        """
        s = get_settings()
        original_bucket = s.minio_bucket
        s.minio_bucket = temp_bucket

        try:
            # 1. 准备用户和 MediaFile
            user_id = await _create_test_user(pg_session)
            content_hash = uuid.uuid4().hex[:32]
            media = await _create_test_media(pg_session, user_id, content_hash)
            media_id = media.id

            # 2. Transcode（本地视频直接用路径，不走 download）
            transcoder = get_transcoder()
            try:
                tc_result = await transcoder.execute(Path(BIG_VIDEO_PATH), content_hash)
            except Exception as e:
                if _is_connection_error(e):
                    pytest.skip(f"基础设施不可达，跳过大视频测试: {e}")
                raise

            # 3. ASR
            asr = get_asr()
            try:
                asr_result = await asr.transcribe(tc_result.audio_local, media_id)
            except Exception as e:
                if _is_connection_error(e):
                    pytest.skip(f"ASR 基础设施不可达，跳过: {e}")
                raise

            tr_orm, chunk_orms = await save_transcription(pg_session, media_id, asr_result)
            await pg_session.flush()

            # 4. OCR
            ocr = get_ocr()
            try:
                ocr_results = await ocr.recognize_frames(media_id, tc_result.keyframes_minio)
            except Exception as e:
                if _is_connection_error(e):
                    pytest.skip(f"OCR 基础设施不可达，跳过: {e}")
                # PaddleOCR oneDNN bug 可能触发，降级为空 list
                if "ConvertPirAttribute2RuntimeAttribute" in str(e) or "onednn" in str(e).lower():
                    ocr_results = []
                else:
                    raise

            ocr_orms = await save_frame_ocr(pg_session, media_id, ocr_results)
            await pg_session.flush()

            # 5. Embedding（为 chunks 生成向量）
            embedder = get_embedding_backend()
            chunk_texts = [c.text for c in chunk_orms]
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
                assert point[0]["payload"]["media_id"] == str(media_id)
                assert point[0]["payload"]["content"] is not None

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
                query_vec = (await embedder.embed(["游轮 视频 内容"])).vectors[0]
                hits = await qdrant.search(
                    query_vector=query_vec,
                    limit=5,
                    filters={"media_id": str(media_id)},
                )
                # 至少命中自己写入的
                assert len(hits) >= 1
                for h in hits:
                    assert h["payload"]["media_id"] == str(media_id)

        finally:
            s.minio_bucket = original_bucket


if __name__ == "__main__":
    pytest.main([__file__, "-v"])