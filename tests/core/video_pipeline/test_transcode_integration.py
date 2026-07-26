"""L3 真实集成测试：Transcoder 真跑 ffmpeg 子进程（小 fixture）。"""

import pytest
import uuid
import os

from videomind.core.video_pipeline.transcode import get_transcoder, TranscodeResult


@pytest.mark.infra
class TestTranscodeIntegration:
    """Transcoder 真实集成测试（依赖 ffmpeg + MinIO）。"""

    async def test_execute_produces_audio_and_frames(self, test_video_path, minio_client, temp_bucket):
        """execute() 产出音频 + 关键帧 + scene_changes，MinIO 上传成功。"""
        # 设置测试用的 minio bucket
        from videomind.config import get_settings
        from videomind.infrastructure.media.minio import get_minio_client
        s = get_settings()
        original_bucket = s.minio_bucket
        s.minio_bucket = temp_bucket

        try:
            transcoder = get_transcoder()
            content_hash = uuid.uuid4().hex[:32]

            result = await transcoder.execute(test_video_path, content_hash)

            # 断言结果结构
            assert isinstance(result, TranscodeResult)
            assert result.video_local == test_video_path
            assert os.path.exists(result.audio_local)
            assert os.path.getsize(result.audio_local) > 0

            # 关键帧产出（至少 1 帧，3s 视频 1fps 应该有 3 帧）
            assert len(result.keyframes_local) >= 1
            for kf in result.keyframes_local:
                assert os.path.exists(kf)
                assert os.path.getsize(kf) > 0

            # 音频 MinIO key 格式
            assert result.audio_minio == f"{content_hash}/audio.ogg"

            # 关键帧 MinIO keys 格式
            for kf_key in result.keyframes_minio:
                assert kf_key.startswith(f"{content_hash}/frames/")
                assert kf_key.endswith(".jpg")

            # 元数据
            assert result.duration_ms > 0
            assert result.width > 0
            assert result.height > 0
            assert result.fps > 0

            # 验证 MinIO 里真有文件（音频 + 至少一帧）
            audio_stat = minio_client.stat_object(temp_bucket, result.audio_minio)
            assert audio_stat.size > 0

            # 至少一帧在 MinIO
            found_frame = False
            for kf_key in result.keyframes_minio:
                try:
                    stat = minio_client.stat_object(temp_bucket, kf_key)
                    if stat.size > 0:
                        found_frame = True
                        break
                except Exception:
                    continue
            assert found_frame, "MinIO 里应至少有一帧上传成功"

            # scene_changes_ms 是 list[int]（可能为空，允许）
            assert isinstance(result.scene_changes_ms, list)
            for ms in result.scene_changes_ms:
                assert isinstance(ms, int)
                assert ms >= 0

        finally:
            s.minio_bucket = original_bucket

    async def test_execute_idempotent_same_hash_same_minio_keys(self, test_video_path, minio_client, temp_bucket):
        """同 content_hash 两次执行，MinIO key 一致（幂等 key 设计）。"""
        from videomind.config import get_settings
        s = get_settings()
        original_bucket = s.minio_bucket
        s.minio_bucket = temp_bucket

        try:
            transcoder = get_transcoder()
            content_hash = uuid.uuid4().hex[:32]

            r1 = await transcoder.execute(test_video_path, content_hash)
            r2 = await transcoder.execute(test_video_path, content_hash)

            assert r1.audio_minio == r2.audio_minio
            assert r1.keyframes_minio == r2.keyframes_minio
        finally:
            s.minio_bucket = original_bucket

    async def test_scene_changes_respect_min_interval(self, test_video_path, minio_client, temp_bucket):
        """场景切换检测遵守 SCENE_MIN_INTERVAL（5s）约束。"""
        from videomind.config import get_settings
        s = get_settings()
        original_bucket = s.minio_bucket
        s.minio_bucket = temp_bucket

        try:
            transcoder = get_transcoder()
            content_hash = uuid.uuid4().hex[:32]

            result = await transcoder.execute(test_video_path, content_hash)
            changes = result.scene_changes_ms

            # 相邻切换点至少间隔 5000ms（5s）
            for i in range(1, len(changes)):
                assert changes[i] - changes[i - 1] >= 5000, f"场景切换间隔 < 5s: {changes[i-1]} -> {changes[i]}"
        finally:
            s.minio_bucket = original_bucket