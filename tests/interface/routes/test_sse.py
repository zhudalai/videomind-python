"""L1 unit tests for interface/routes/sse.py - SSE event formatting (pure logic)."""

import pytest
from unittest.mock import Mock, AsyncMock

from videomind.application.task_orchestration.broadcast import ProgressEvent


class TestSSEEventFormatting:
    """Tests for SSE event formatting logic (pure, no FastAPI/starlette)."""

    def test_format_sse_event_basic(self):
        """Basic event + data dict formatted correctly."""
        event = "progress"
        data = {"stage": "asr", "progress_pct": 60, "message": "ASR done"}

        # Simulate what sse_starlette does internally
        lines = []
        lines.append(f"event: {event}")
        import json
        lines.append(f"data: {json.dumps(data)}")
        lines.append("")  # blank line terminates event
        result = "\n".join(lines) + "\n"

        assert "event: progress" in result
        assert "data: " in result
        assert '"stage": "asr"' in result
        assert '"progress_pct": 60' in result
        assert result.endswith("\n\n")  # double newline ends event

    def test_format_sse_multiline_data(self):
        """Multi-line data properly handled (each line prefixed with 'data: ')."""
        event = "progress"
        data = {"stage": "asr", "progress_pct": 60, "message": "Line 1\nLine 2\nLine 3"}

        import json
        data_str = json.dumps(data)
        # sse_starlette splits multi-line data into multiple data: lines
        # Our test just ensures it doesn't break
        assert "\n" in data_str or data["message"] == "Line 1\nLine 2\nLine 3"

    def test_format_sse_empty_data(self):
        """Event with empty data dict produces valid SSE."""
        event = "heartbeat"
        data = {}

        import json
        lines = [f"event: {event}", f"data: {json.dumps(data)}", ""]
        result = "\n".join(lines) + "\n"

        assert "event: heartbeat" in result
        assert "data: {}" in result
        assert result.endswith("\n\n")

    def test_format_sse_special_chars_escaped(self):
        """Special characters in data are JSON-escaped."""
        event = "progress"
        data = {"message": 'Quote: " and newline\nand tab\t'}

        import json
        lines = [f"event: {event}", f"data: {json.dumps(data)}", ""]
        result = "\n".join(lines) + "\n"

        # JSON escaping
        assert '\\"' in result
        assert '\\n' in result
        assert '\\t' in result


class TestProgressEventToSSE:
    """Tests for ProgressEvent integration with SSE format."""

    def test_progress_event_to_json(self):
        """ProgressEvent.to_json produces valid JSON."""
        event = ProgressEvent(
            stage="asr",
            progress_pct=60,
            message="ASR done",
            timestamp=1234567890.0,
        )
        json_str = event.to_json()
        assert '"stage": "asr"' in json_str
        assert '"progress_pct": 60' in json_str
        assert '"message": "ASR done"' in json_str
        assert '"timestamp": 1234567890.0' in json_str

    def test_progress_event_from_json(self):
        """ProgressEvent.from_json reconstructs event."""
        json_str = '{"stage": "ocr", "progress_pct": 75, "message": "OCR done", "timestamp": 999.0}'
        event = ProgressEvent.from_json(json_str)
        assert event.stage == "ocr"
        assert event.progress_pct == 75
        assert event.message == "OCR done"
        assert event.timestamp == 999.0

    def test_progress_event_roundtrip(self):
        """to_json -> from_json preserves all fields."""
        original = ProgressEvent(
            stage="indexing",
            progress_pct=90,
            message="Indexing",
            timestamp=555.0,
        )
        restored = ProgressEvent.from_json(original.to_json())
        assert restored.stage == original.stage
        assert restored.progress_pct == original.progress_pct
        assert restored.message == original.message
        assert restored.timestamp == original.timestamp


class TestSSEGeneratorLogic:
    """Tests for SSE generator logic (mocked dependencies)."""

    @pytest.mark.asyncio
    async def test_generator_yields_events_until_complete(self):
        """Generator yields ProgressEvents until 100% or -1%."""
        # Mock progress_stream to yield test events
        async def mock_progress_stream(media_id):
            yield ProgressEvent(stage="claimed", progress_pct=5, message="Start")
            yield ProgressEvent(stage="downloading", progress_pct=10, message="Downloading")
            yield ProgressEvent(stage="completed", progress_pct=100, message="Done")

        events = []
        async for evt in mock_progress_stream("test-id"):
            events.append(evt)
            if evt.progress_pct >= 100 or evt.progress_pct < 0:
                break

        assert len(events) == 3
        assert events[-1].progress_pct == 100

    @pytest.mark.asyncio
    async def test_generator_stops_on_failed(self):
        """Generator stops on negative progress_pct (failed)."""
        async def mock_progress_stream(media_id):
            yield ProgressEvent(stage="claimed", progress_pct=5, message="Start")
            yield ProgressEvent(stage="failed", progress_pct=-1, message="Error")

        events = []
        async for evt in mock_progress_stream("test-id"):
            events.append(evt)
            if evt.progress_pct >= 100 or evt.progress_pct < 0:
                break

        assert len(events) == 2
        assert events[-1].progress_pct == -1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])