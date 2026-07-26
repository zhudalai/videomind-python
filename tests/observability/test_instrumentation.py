"""L1 unit tests for observability/instrumentation.py - trace_stage context manager."""

from unittest.mock import MagicMock, patch

import pytest

from videomind.observability.instrumentation import trace_stage
from videomind.observability.metrics import PIPELINE_STAGE_DURATION, PIPELINE_STAGE_TOTAL


class TestTraceStage:
    """Tests for trace_stage context manager."""

    def setup_method(self):
        """Reset metrics before each test."""
        # Clear any existing metric state if needed
        pass

    @patch("videomind.observability.instrumentation.PIPELINE_STAGE_TOTAL")
    @patch("videomind.observability.instrumentation.PIPELINE_STAGE_DURATION")
    @patch("videomind.observability.instrumentation.traced_span")
    def test_success_increments_started_and_succeeded(self, mock_traced_span, mock_duration, mock_total):
        """On success, increments started and succeeded counters, observes duration."""
        mock_span = MagicMock()
        mock_traced_span.return_value.__enter__ = MagicMock(return_value=mock_span)
        mock_traced_span.return_value.__exit__ = MagicMock(return_value=None)
        mock_total.labels.return_value.inc = MagicMock()
        mock_duration.labels.return_value.observe = MagicMock()

        with trace_stage("asr", "media-123"):
            pass  # success

        # Verify started counter
        mock_total.labels.assert_any_call(stage="asr", status="started")
        # Verify succeeded counter
        mock_total.labels.assert_any_call(stage="asr", status="succeeded")
        # Verify duration observed
        mock_duration.labels.assert_called_with(stage="asr")
        mock_duration.labels.return_value.observe.assert_called()

    @patch("videomind.observability.instrumentation.PIPELINE_STAGE_TOTAL")
    @patch("videomind.observability.instrumentation.PIPELINE_STAGE_DURATION")
    @patch("videomind.observability.instrumentation.traced_span")
    def test_exception_increments_failed_and_observes_duration(self, mock_traced_span, mock_duration, mock_total):
        """On exception, increments failed counter, observes duration, records exception."""
        mock_span = MagicMock()
        mock_span.is_recording.return_value = True
        mock_traced_span.return_value.__enter__ = MagicMock(return_value=mock_span)
        mock_traced_span.return_value.__exit__ = MagicMock(return_value=None)
        mock_total.labels.return_value.inc = MagicMock()
        mock_duration.labels.return_value.observe = MagicMock()

        with pytest.raises(ValueError):
            with trace_stage("ocr", "media-456"):
                raise ValueError("OCR failed")

        # Verify started counter
        mock_total.labels.assert_any_call(stage="ocr", status="started")
        # Verify failed counter
        mock_total.labels.assert_any_call(stage="ocr", status="failed")
        # Verify duration observed even on failure
        mock_duration.labels.assert_called_with(stage="ocr")
        mock_duration.labels.return_value.observe.assert_called()
        # Verify exception recorded on span
        mock_span.record_exception.assert_called()

    @patch("videomind.observability.instrumentation.PIPELINE_STAGE_TOTAL")
    @patch("videomind.observability.instrumentation.PIPELINE_STAGE_DURATION")
    @patch("videomind.observability.instrumentation.traced_span")
    def test_span_has_correct_attributes(self, mock_traced_span, mock_duration, mock_total):
        """Span created with media_id and stage attributes."""
        mock_span = MagicMock()
        mock_traced_span.return_value.__enter__ = MagicMock(return_value=mock_span)
        mock_traced_span.return_value.__exit__ = MagicMock(return_value=None)
        mock_total.labels.return_value.inc = MagicMock()
        mock_duration.labels.return_value.observe = MagicMock()

        with trace_stage("indexing", "media-789"):
            pass

        # traced_span called with pipeline.{stage} name
        mock_traced_span.assert_called_once()
        call_args = mock_traced_span.call_args
        assert "pipeline.indexing" in call_args[0][0]
        attrs = call_args[1].get("attributes", {})
        assert attrs.get("media_id") == "media-789"
        assert attrs.get("stage") == "indexing"

    @patch("videomind.observability.instrumentation.PIPELINE_STAGE_TOTAL")
    @patch("videomind.observability.instrumentation.PIPELINE_STAGE_DURATION")
    @patch("videomind.observability.instrumentation.traced_span")
    def test_multiple_stages_independent(self, mock_traced_span, mock_duration, mock_total):
        """Multiple trace_stage calls are independent."""
        mock_span = MagicMock()
        mock_traced_span.return_value.__enter__ = MagicMock(return_value=mock_span)
        mock_traced_span.return_value.__exit__ = MagicMock(return_value=None)
        mock_total.labels.return_value.inc = MagicMock()
        mock_duration.labels.return_value.observe = MagicMock()

        with trace_stage("download", "media-1"):
            pass
        with trace_stage("transcode", "media-1"):
            pass

        # Each stage gets its own counters
        assert mock_total.labels.call_count >= 4  # started + succeeded for each

    @patch("videomind.observability.instrumentation.PIPELINE_STAGE_TOTAL")
    @patch("videomind.observability.instrumentation.PIPELINE_STAGE_DURATION")
    @patch("videomind.observability.instrumentation.traced_span")
    def test_exception_recorded_on_span_if_recording(self, mock_traced_span, mock_duration, mock_total):
        """Exception recorded on span when span.is_recording() is True."""
        mock_span = MagicMock()
        mock_span.is_recording.return_value = True
        mock_traced_span.return_value.__enter__ = MagicMock(return_value=mock_span)
        mock_traced_span.return_value.__exit__ = MagicMock(return_value=None)
        mock_total.labels.return_value.inc = MagicMock()
        mock_duration.labels.return_value.observe = MagicMock()

        with pytest.raises(RuntimeError):
            with trace_stage("asr", "media-999"):
                raise RuntimeError("ASR error")

        mock_span.record_exception.assert_called_once()
        arg = mock_span.record_exception.call_args[0][0]
        assert isinstance(arg, RuntimeError)
        assert str(arg) == "ASR error"

    @patch("videomind.observability.instrumentation.PIPELINE_STAGE_TOTAL")
    @patch("videomind.observability.instrumentation.PIPELINE_STAGE_DURATION")
    @patch("videomind.observability.instrumentation.traced_span")
    def test_exception_not_recorded_if_span_not_recording(self, mock_traced_span, mock_duration, mock_total):
        """Exception not recorded if span.is_recording() is False."""
        mock_span = MagicMock()
        mock_span.is_recording.return_value = False
        mock_traced_span.return_value.__enter__ = MagicMock(return_value=mock_span)
        mock_traced_span.return_value.__exit__ = MagicMock(return_value=None)
        mock_total.labels.return_value.inc = MagicMock()
        mock_duration.labels.return_value.observe = MagicMock()

        with pytest.raises(RuntimeError):
            with trace_stage("ocr", "media-000"):
                raise RuntimeError("OCR error")

        mock_span.record_exception.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])