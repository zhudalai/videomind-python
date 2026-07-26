"""L1 unit tests for task_orchestration/broadcast.py - pure logic, zero external deps."""

import pytest

from videomind.application.task_orchestration.broadcast import (
    ProgressEvent,
    stage_to_progress,
    _channel,
)


class TestStageToProgress:
    """Tests for stage_to_progress mapping."""

    def test_claimed_returns_5(self):
        assert stage_to_progress("claimed") == 5

    def test_downloading_returns_10(self):
        assert stage_to_progress("downloading") == 10

    def test_downloaded_returns_20(self):
        assert stage_to_progress("downloaded") == 20

    def test_transcoding_returns_30(self):
        assert stage_to_progress("transcoding") == 30

    def test_transcoded_returns_40(self):
        assert stage_to_progress("transcoded") == 40

    def test_asr_returns_60(self):
        assert stage_to_progress("asr") == 60

    def test_ocr_returns_75(self):
        assert stage_to_progress("ocr") == 75

    def test_indexing_returns_90(self):
        assert stage_to_progress("indexing") == 90

    def test_completed_returns_100(self):
        assert stage_to_progress("completed") == 100

    def test_failed_returns_minus_1(self):
        assert stage_to_progress("failed") == -1

    def test_unknown_stage_returns_0(self):
        assert stage_to_progress("unknown_stage") == 0
        assert stage_to_progress("") == 0
        assert stage_to_progress("random") == 0


class TestChannel:
    """Tests for _channel helper."""

    def test_channel_format(self):
        """Channel name follows media:{media_id}:progress pattern."""
        media_id = "abc123"
        assert _channel(media_id) == "media:abc123:progress"

    def test_channel_different_media_ids(self):
        """Different media_ids produce different channels."""
        assert _channel("id1") != _channel("id2")

    def test_channel_special_characters(self):
        """UUID with hyphens works correctly."""
        media_id = "123e4567-e89b-12d3-a456-426614174000"
        assert _channel(media_id) == f"media:{media_id}:progress"


class TestProgressEvent:
    """Tests for ProgressEvent dataclass."""

    def test_creation_with_required_fields(self):
        """Create ProgressEvent with required fields."""
        event = ProgressEvent(stage="asr", progress_pct=60, message="ASR done")
        assert event.stage == "asr"
        assert event.progress_pct == 60
        assert event.message == "ASR done"
        assert event.timestamp > 0  # auto-set

    def test_creation_with_explicit_timestamp(self):
        """Create with explicit timestamp."""
        event = ProgressEvent(stage="asr", progress_pct=60, message="done", timestamp=12345.0)
        assert event.timestamp == 12345.0

    def test_to_json_serializes_correctly(self):
        """to_json produces valid JSON with all fields."""
        import json
        event = ProgressEvent(stage="asr", progress_pct=60, message="done", timestamp=12345.0)
        data = json.loads(event.to_json())
        assert data["stage"] == "asr"
        assert data["progress_pct"] == 60
        assert data["message"] == "done"
        assert data["timestamp"] == 12345.0

    def test_from_json_deserializes_correctly(self):
        """from_json correctly reconstructs ProgressEvent."""
        json_str = '{"stage": "asr", "progress_pct": 60, "message": "done", "timestamp": 12345.0}'
        event = ProgressEvent.from_json(json_str)
        assert event.stage == "asr"
        assert event.progress_pct == 60
        assert event.message == "done"
        assert event.timestamp == 12345.0

    def test_roundtrip_json(self):
        """to_json -> from_json preserves all fields."""
        original = ProgressEvent(stage="ocr", progress_pct=75, message="OCR done", timestamp=999.0)
        restored = ProgressEvent.from_json(original.to_json())
        assert restored.stage == original.stage
        assert restored.progress_pct == original.progress_pct
        assert restored.message == original.message
        assert restored.timestamp == original.timestamp


if __name__ == "__main__":
    pytest.main([__file__, "-v"])