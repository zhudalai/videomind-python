"""L1 unit tests for interface/routes/video.py - Pydantic schema validation."""

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from videomind.interface.routes.video import (
    PipelineSubmitRequest,
    PipelineSubmitResponse,
    PipelineStatusResponse,
    OCRResultResponse,
)


class TestPipelineSubmitRequest:
    """Tests for PipelineSubmitRequest schema."""

    def test_valid_source_url_and_user_id(self):
        """Valid source_url and user_id creates request."""
        user_id = uuid.uuid4()
        req = PipelineSubmitRequest(source_url="https://youtube.com/watch?v=abc123", user_id=user_id)
        assert req.source_url == "https://youtube.com/watch?v=abc123"
        assert req.user_id == user_id

    def test_valid_source_url_no_user_id(self):
        """Valid source_url without user_id creates request (user_id optional)."""
        req = PipelineSubmitRequest(source_url="https://example.com/video.mp4")
        assert req.source_url == "https://example.com/video.mp4"
        assert req.user_id is None

    def test_missing_source_url_raises_validation_error(self):
        """Missing source_url raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            PipelineSubmitRequest(user_id=uuid.uuid4())
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("source_url",) for e in errors)

    def test_source_url_too_short_raises(self):
        """source_url shorter than min_length=5 raises ValidationError."""
        with pytest.raises(ValidationError):
            PipelineSubmitRequest(source_url="http")

    def test_source_url_too_long_raises(self):
        """source_url longer than max_length=2048 raises ValidationError."""
        long_url = "https://example.com/" + "x" * 2050
        with pytest.raises(ValidationError):
            PipelineSubmitRequest(source_url=long_url)

    def test_source_url_empty_string_raises(self):
        """Empty source_url raises ValidationError."""
        with pytest.raises(ValidationError):
            PipelineSubmitRequest(source_url="")


class TestPipelineSubmitResponse:
    """Tests for PipelineSubmitResponse schema."""

    def test_valid_response_creation(self):
        """Valid media_id and status creates response."""
        media_id = uuid.uuid4()
        resp = PipelineSubmitResponse(media_id=media_id, status="pending")
        assert resp.media_id == media_id
        assert resp.status == "pending"

    def test_media_id_required(self):
        """Missing media_id raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            PipelineSubmitResponse(status="pending")
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("media_id",) for e in errors)

    def test_status_required(self):
        """Missing status raises ValidationError."""
        with pytest.raises(ValidationError) as exc_info:
            PipelineSubmitResponse(media_id=uuid.uuid4())
        errors = exc_info.value.errors()
        assert any(e["loc"] == ("status",) for e in errors)


class TestPipelineStatusResponse:
    """Tests for PipelineStatusResponse schema."""

    def test_full_response_creation(self):
        """All fields provided creates response."""
        media_id = uuid.uuid4()
        resp = PipelineStatusResponse(
            media_id=media_id,
            source_url="https://example.com/video.mp4",
            status="downloading",
            stage_progress={"downloading": "50%"},
            error_message=None,
        )
        assert resp.media_id == media_id
        assert resp.source_url == "https://example.com/video.mp4"
        assert resp.status == "downloading"
        assert resp.stage_progress == {"downloading": "50%"}
        assert resp.error_message is None

    def test_minimal_response(self):
        """Only required fields creates response."""
        media_id = uuid.uuid4()
        resp = PipelineStatusResponse(media_id=media_id, source_url=None, status="pending")
        assert resp.media_id == media_id
        assert resp.source_url is None
        assert resp.status == "pending"
        assert resp.stage_progress == {}  # default_factory
        assert resp.error_message is None

    def test_stage_progress_default_empty_dict(self):
        """stage_progress defaults to empty dict."""
        resp = PipelineStatusResponse(media_id=uuid.uuid4(), source_url=None, status="pending")
        assert resp.stage_progress == {}

    def test_error_message_can_be_string(self):
        """error_message accepts string."""
        resp = PipelineStatusResponse(
            media_id=uuid.uuid4(),
            source_url=None,
            status="failed",
            error_message="Download failed: 404",
        )
        assert resp.error_message == "Download failed: 404"


class TestOCRResultResponse:
    """OCRResultResponse must match the real FrameOCR ORM shape (not a fictional schema)."""

    @staticmethod
    def _fake_frame_ocr() -> SimpleNamespace:
        """Mirror infrastructure/storage/models.py FrameOCR columns exactly:
        id, media_id, frame_ms, minio_object, ocr_text (nullable), phash (nullable),
        model_name (nullable), status, created_at. ocr_text=None is a legal
        'frame had no detectable text' state, observed in production OCR rows.
        """
        return SimpleNamespace(
            id=uuid.uuid4(),
            media_id=uuid.uuid4(),
            frame_ms=1000,
            minio_object="media/abc/keyframes/000001.jpg",
            ocr_text=None,
            phash="ccf30555ad57b3e7",
            model_name="paddle-ocr",
            status="completed",
            created_at=datetime(2026, 7, 27, 12, 7, 33, tzinfo=timezone.utc),
        )

    def test_validates_real_frame_ocr_row_without_ocr_text(self):
        """OCRResultResponse.model_validate accepts a real FrameOCR row whose
        ocr_text is None (no detectable text) — the case that 500s the detail route today."""
        row = self._fake_frame_ocr()
        resp = OCRResultResponse.model_validate(row)
        # Aligned fields surfaced from the real ORM column set
        assert resp.frame_ms == 1000
        assert resp.ocr_text is None
        assert resp.phash == "ccf30555ad57b3e7"
        assert resp.model_name == "paddle-ocr"
        assert resp.status == "completed"

    def test_validates_real_frame_ocr_row_with_text(self):
        """OCRResultResponse.model_validate accepts a real FrameOCR row with text."""
        row = self._fake_frame_ocr()
        row.ocr_text = "Detected caption"
        resp = OCRResultResponse.model_validate(row)
        assert resp.ocr_text == "Detected caption"
        assert resp.frame_ms == 1000

    def test_no_fictional_ocr_fields_remain(self):
        """The fictional frame_index / timestamp_ms / confidence / bbox that don't exist
        on FrameOCR must NOT remain on the schema — they caused the detail 500."""
        field_names = set(OCRResultResponse.model_fields.keys())
        assert "frame_index" not in field_names
        assert "timestamp_ms" not in field_names
        assert "confidence" not in field_names
        assert "bbox" not in field_names
        # Real ORM columns that must be present
        assert {"frame_ms", "ocr_text"}.issubset(field_names)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])