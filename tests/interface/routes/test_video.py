"""L1 unit tests for interface/routes/video.py - Pydantic schema validation."""

import uuid

import pytest
from pydantic import ValidationError

from videomind.interface.routes.video import (
    PipelineSubmitRequest,
    PipelineSubmitResponse,
    PipelineStatusResponse,
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])