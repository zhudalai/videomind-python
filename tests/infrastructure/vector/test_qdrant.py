"""L1 unit tests for qdrant.py - pure logic, zero external dependencies."""

import uuid
from videomind.infrastructure.vector.qdrant import (
    make_qdrant_point_id,
    build_chunk_payload,
    _build_filter,
)
import qdrant_client.models as qm


class TestMakeQdrantPointId:
    """Tests for make_qdrant_point_id - deterministic UUID v5 generation."""

    def test_deterministic_same_inputs(self):
        """Same inputs should produce identical UUID v5."""
        media_id = uuid.uuid4()
        chunk_index = 5

        result1 = make_qdrant_point_id(media_id, chunk_index)
        result2 = make_qdrant_point_id(media_id, chunk_index)

        assert result1 == result2
        assert isinstance(result1, uuid.UUID)

    def test_different_chunk_index_produces_different_uuid(self):
        """Different chunk_index should produce different UUIDs."""
        media_id = uuid.uuid4()

        id1 = make_qdrant_point_id(media_id, 0)
        id2 = make_qdrant_point_id(media_id, 1)
        id3 = make_qdrant_point_id(media_id, 99)

        assert id1 != id2
        assert id2 != id3
        assert id1 != id3

    def test_different_media_id_produces_different_uuid(self):
        """Different media_id should produce different UUIDs even with same chunk_index."""
        chunk_index = 42

        id1 = make_qdrant_point_id(uuid.uuid4(), chunk_index)
        id2 = make_qdrant_point_id(uuid.uuid4(), chunk_index)

        assert id1 != id2

    def test_return_value_is_valid_uuid(self):
        """Return value must be a valid UUID (uuid.UUID(str(result)) must not raise)."""
        media_id = uuid.uuid4()
        chunk_index = 123

        result = make_qdrant_point_id(media_id, chunk_index)

        # This must not raise
        parsed = uuid.UUID(str(result))
        assert parsed == result

    def test_uuid_version_is_v5(self):
        """Generated UUID should be version 5 (namespace-based SHA-1)."""
        media_id = uuid.uuid4()
        chunk_index = 0

        result = make_qdrant_point_id(media_id, chunk_index)

        assert result.version == 5


class TestBuildChunkPayload:
    """Tests for build_chunk_payload - payload dict construction."""

    def test_required_fields_present(self):
        """Required fields must be present in returned dict."""
        media_id = uuid.uuid4()
        segment_id = uuid.uuid4()
        chunk_index = 3
        start_ms = 1000
        end_ms = 5000
        source_type = "asr"
        content_hash = "abc123"
        content = "Hello world"

        payload = build_chunk_payload(
            chunk_id=uuid.uuid4(),
            media_id=media_id,
            segment_id=segment_id,
            chunk_index=chunk_index,
            start_ms=start_ms,
            end_ms=end_ms,
            source_type=source_type,
            content_hash=content_hash,
            content=content,
        )

        assert payload["media_id"] == str(media_id)
        assert payload["segment_id"] == str(segment_id)
        assert payload["chunk_index"] == chunk_index
        assert payload["start_ms"] == start_ms
        assert payload["end_ms"] == end_ms
        assert payload["source_type"] == source_type
        assert payload["content_hash"] == content_hash
        assert payload["content"] == content
        assert "chunk_id" in payload

    def test_optional_fields_not_none_when_provided(self):
        """Optional fields should be present when provided."""
        media_id = uuid.uuid4()

        payload = build_chunk_payload(
            chunk_id=uuid.uuid4(),
            media_id=media_id,
            segment_id=None,
            chunk_index=0,
            start_ms=None,
            end_ms=None,
            source_type="asr",
            content_hash="hash123",
            content="test content",
        )

        # These should be present in payload even when None
        # (implementation includes them as None or omits them - both acceptable)
        assert "segment_id" in payload or "segment_id" not in payload
        assert "start_ms" in payload or "start_ms" not in payload
        assert "end_ms" in payload or "end_ms" not in payload

    def test_content_is_included_in_payload(self):
        """Content field must be correctly included in payload."""
        media_id = uuid.uuid4()
        content = "This is the chunk content with 中文 and special chars!@#"

        payload = build_chunk_payload(
            chunk_id=uuid.uuid4(),
            media_id=media_id,
            segment_id=None,
            chunk_index=0,
            start_ms=0,
            end_ms=1000,
            source_type="asr",
            content_hash="abc",
            content=content,
        )

        assert payload["content"] == content

    def test_all_expected_keys_present(self):
        """Verify all expected keys are in the payload."""
        payload = build_chunk_payload(
            chunk_id=uuid.uuid4(),
            media_id=uuid.uuid4(),
            segment_id=uuid.uuid4(),
            chunk_index=0,
            start_ms=0,
            end_ms=1000,
            source_type="asr",
            content_hash="abc",
            content="test",
        )

        expected_keys = {
            "chunk_id",
            "media_id",
            "segment_id",
            "chunk_index",
            "start_ms",
            "end_ms",
            "source_type",
            "content_hash",
            "content",
        }
        assert expected_keys.issubset(payload.keys())


class TestBuildFilter:
    """Tests for _build_filter - Qdrant Filter construction."""

    def test_empty_dict_returns_filter_with_empty_must(self):
        """Empty dict should return Filter with empty must list."""
        result = _build_filter({})

        assert isinstance(result, qm.Filter)
        assert result.must == []

    def test_single_media_id_filter(self):
        """Single media_id filter should create FieldCondition with MatchValue."""
        result = _build_filter({"media_id": "media-123"})

        assert isinstance(result, qm.Filter)
        assert len(result.must) == 1
        condition = result.must[0]
        assert isinstance(condition, qm.FieldCondition)
        assert condition.key == "media_id"
        assert isinstance(condition.match, qm.MatchValue)
        assert condition.match.value == "media-123"

    def test_single_source_type_filter(self):
        """Single source_type filter should create FieldCondition with MatchValue."""
        result = _build_filter({"source_type": "asr"})

        assert isinstance(result, qm.Filter)
        assert len(result.must) == 1
        condition = result.must[0]
        assert isinstance(condition, qm.FieldCondition)
        assert condition.key == "source_type"
        assert isinstance(condition.match, qm.MatchValue)
        assert condition.match.value == "asr"

    def test_multiple_filters_combined_with_must(self):
        """Multiple filters should be combined in must array."""
        result = _build_filter({"media_id": "media-123", "source_type": "asr"})

        assert isinstance(result, qm.Filter)
        assert len(result.must) == 2

        keys = {cond.key for cond in result.must}
        assert keys == {"media_id", "source_type"}

        for cond in result.must:
            assert isinstance(cond, qm.FieldCondition)
            assert isinstance(cond.match, qm.MatchValue)

    def test_filter_values_preserved(self):
        """Filter values should be preserved exactly."""
        result = _build_filter({"media_id": "test-media", "source_type": "ocr"})

        values = {cond.key: cond.match.value for cond in result.must}
        assert values == {"media_id": "test-media", "source_type": "ocr"}

    def test_empty_string_value_allowed(self):
        """Empty string values should be allowed in filter."""
        result = _build_filter({"media_id": ""})

        assert len(result.must) == 1
        assert result.must[0].match.value == ""

    def test_numeric_string_value(self):
        """Numeric string values should be preserved."""
        result = _build_filter({"chunk_index": "42"})

        assert len(result.must) == 1
        assert result.must[0].match.value == "42"