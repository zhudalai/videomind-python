"""L1 unit tests for task_orchestration/tasks.py - IngestionContext to_dict/from_dict."""

import uuid
from datetime import datetime

from videomind.application.task_orchestration.tasks import IngestionContext


class TestIngestionContextToDictFromDict:
    """Tests for IngestionContext serialization round-trip."""

    def test_full_context_roundtrip(self):
        """Complete IngestionContext with all fields round-trips correctly."""
        media_id = str(uuid.uuid4())
        source_url = "https://youtube.com/watch?v=abc123"
        user_id = str(uuid.uuid4())

        ctx = IngestionContext(
            media_id=media_id,
            source_url=source_url,
            user_id=user_id,
            download_result={
                "local_path": "/tmp/video.mp4",
                "content_hash": "abc123def456",
                "duration_ms": 120000,
                "width": 1920,
                "height": 1080,
                "fps": 30.0,
                "minio_object": "videos/abc123def456/original.mp4",
            },
            transcode_result={
                "audio_minio": "abc123def456/audio.ogg",
                "keyframes_minio": ["abc123def456/frames/1000.jpg", "abc123def456/frames/2000.jpg"],
                "scene_changes_ms": [5000, 15000, 25000],
                "duration_ms": 120000,
                "width": 1920,
                "height": 1080,
                "fps": 30.0,
            },
            transcription_id=str(uuid.uuid4()),
            asr_chunks_count=42,
            ocr_frames_count=15,
            chunk_count=30,
            current_stage="indexing",
            progress_pct=85,
            error=None,
        )

        # to_dict -> from_dict roundtrip
        data = ctx.to_dict()
        restored = IngestionContext.from_dict(data)

        # All fields match
        assert restored.media_id == ctx.media_id
        assert restored.source_url == ctx.source_url
        assert restored.user_id == ctx.user_id
        assert restored.download_result == ctx.download_result
        assert restored.transcode_result == ctx.transcode_result
        assert restored.transcription_id == ctx.transcription_id
        assert restored.asr_chunks_count == ctx.asr_chunks_count
        assert restored.ocr_frames_count == ctx.ocr_frames_count
        assert restored.chunk_count == ctx.chunk_count
        assert restored.current_stage == ctx.current_stage
        assert restored.progress_pct == ctx.progress_pct
        assert restored.error == ctx.error

    def test_minimal_required_fields_roundtrip(self):
        """Only required fields (media_id, source_url) round-trip correctly."""
        media_id = str(uuid.uuid4())
        source_url = "https://example.com/video.mp4"

        ctx = IngestionContext(media_id=media_id, source_url=source_url)

        data = ctx.to_dict()
        restored = IngestionContext.from_dict(data)

        assert restored.media_id == media_id
        assert restored.source_url == source_url
        assert restored.user_id is None
        assert restored.download_result is None
        assert restored.transcode_result is None
        assert restored.transcription_id is None
        assert restored.asr_chunks_count == 0
        assert restored.ocr_frames_count == 0
        assert restored.chunk_count == 0
        assert restored.current_stage == "claimed"
        assert restored.progress_pct == 5
        assert restored.error is None

    def test_nested_dicts_preserved(self):
        """Nested dicts in download_result/transcode_result preserved exactly."""
        media_id = str(uuid.uuid4())
        source_url = "https://example.com/video.mp4"

        nested_download = {
            "local_path": "/tmp/video.mp4",
            "meta": {"nested": {"deep": "value", "list": [1, 2, 3]}},
        }
        nested_transcode = {
            "audio_minio": "hash/audio.ogg",
            "keyframes_minio": ["hash/frames/1.jpg"],
            "meta": {"extra": "data"},
        }

        ctx = IngestionContext(
            media_id=media_id,
            source_url=source_url,
            download_result=nested_download,
            transcode_result=nested_transcode,
        )

        restored = IngestionContext.from_dict(ctx.to_dict())

        assert restored.download_result == nested_download
        assert restored.transcode_result == nested_transcode
        # Ensure deep equality, not just reference
        assert restored.download_result["meta"]["nested"]["deep"] == "value"
        assert restored.transcode_result["meta"]["extra"] == "data"

    def test_optional_none_fields_remain_none(self):
        """Optional fields that are None remain None after round-trip."""
        ctx = IngestionContext(
            media_id=str(uuid.uuid4()),
            source_url="https://example.com/video.mp4",
            user_id=None,
            download_result=None,
            transcode_result=None,
            transcription_id=None,
            error=None,
        )

        restored = IngestionContext.from_dict(ctx.to_dict())

        assert restored.user_id is None
        assert restored.download_result is None
        assert restored.transcode_result is None
        assert restored.transcription_id is None
        assert restored.error is None

    def test_error_field_roundtrip(self):
        """Error message string round-trips correctly."""
        ctx = IngestionContext(
            media_id=str(uuid.uuid4()),
            source_url="https://example.com/video.mp4",
            error="FFmpeg failed: exit code 1",
        )

        restored = IngestionContext.from_dict(ctx.to_dict())
        assert restored.error == "FFmpeg failed: exit code 1"

    def test_progress_and_stage_roundtrip(self):
        """current_stage and progress_pct round-trip correctly."""
        ctx = IngestionContext(
            media_id=str(uuid.uuid4()),
            source_url="https://example.com/video.mp4",
            current_stage="asr_done",
            progress_pct=60,
        )

        restored = IngestionContext.from_dict(ctx.to_dict())
        assert restored.current_stage == "asr_done"
        assert restored.progress_pct == 60


if __name__ == "__main__":
    pytest.main([__file__, "-v"])