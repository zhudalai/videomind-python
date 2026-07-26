"""L1 unit tests for minio.py - pure logic key building functions, zero external dependencies."""

from videomind.infrastructure.media.minio import (
    build_media_object_key,
    build_audio_object_key,
    build_frame_object_key,
)


class TestBuildMediaObjectKey:
    """Tests for build_media_object_key function."""

    def test_standard_format(self):
        """Format should be 'videos/{content_hash}/{filename}'."""
        content_hash = "abc123"
        filename = "original.mp4"

        result = build_media_object_key(content_hash, filename)

        assert result == f"videos/{content_hash}/{filename}"

    def test_same_hash_different_filenames(self):
        """Same hash with different filenames should produce different keys."""
        content_hash = "samehash"

        key1 = build_media_object_key(content_hash, "video.mp4")
        key2 = build_media_object_key(content_hash, "video.mov")

        assert key1 != key2
        assert key1 == "videos/samehash/video.mp4"
        assert key2 == "videos/samehash/video.mov"

    def test_different_hash_same_filename(self):
        """Different hash with same filename should produce different keys."""
        key1 = build_media_object_key("hash1", "video.mp4")
        key2 = build_media_object_key("hash2", "video.mp4")

        assert key1 != key2
        assert key1 == "videos/hash1/video.mp4"
        assert key2 == "videos/hash2/video.mp4"

    def test_no_extra_slashes_or_spaces(self):
        """String concatenation should have no extra slashes or spaces."""
        content_hash = "testhash"
        filename = "video.mp4"

        result = build_media_object_key(content_hash, filename)

        assert "//" not in result
        assert not result.startswith("/")
        assert not result.endswith("/")
        assert " " not in result

    def test_special_characters_in_hash_and_filename(self):
        """Should handle special characters."""
        content_hash = "hash-with.dashes_and_underscores"
        filename = "my video (1).mp4"

        result = build_media_object_key(content_hash, filename)

        assert result == f"videos/{content_hash}/{filename}"


class TestBuildAudioObjectKey:
    """Tests for build_audio_object_key function."""

    def test_standard_format_without_chunk_index(self):
        """Without chunk_index: '{content_hash}/audio.ogg'."""
        content_hash = "abc123"

        result = build_audio_object_key(content_hash)

        assert result == f"{content_hash}/audio.ogg"

    def test_standard_format_with_chunk_index(self):
        """With chunk_index: '{content_hash}/audio_chunk_{chunk_index}.ogg'."""
        content_hash = "abc123"
        chunk_index = 5

        result = build_audio_object_key(content_hash, chunk_index)

        assert result == f"{content_hash}/audio_chunk_{chunk_index}.ogg"

    def test_same_hash_different_chunk_index(self):
        """Same hash with different chunk_index should produce different keys."""
        content_hash = "samehash"

        key1 = build_audio_object_key(content_hash, 0)
        key2 = build_audio_object_key(content_hash, 1)
        key3 = build_audio_object_key(content_hash, 99)

        assert key1 != key2 != key3
        assert key1 == "samehash/audio_chunk_0.ogg"
        assert key2 == "samehash/audio_chunk_1.ogg"
        assert key3 == "samehash/audio_chunk_99.ogg"

    def test_no_extra_slashes_or_spaces(self):
        """String concatenation should have no extra slashes or spaces."""
        result = build_audio_object_key("hash", 0)

        assert "//" not in result
        assert not result.startswith("/")
        assert not result.endswith("/")
        assert " " not in result

    def test_chunk_index_none_vs_zero(self):
        """None and 0 should produce different keys."""
        key_none = build_audio_object_key("hash", None)
        key_zero = build_audio_object_key("hash", 0)

        assert key_none != key_zero
        assert key_none == "hash/audio.ogg"
        assert key_zero == "hash/audio_chunk_0.ogg"


class TestBuildFrameObjectKey:
    """Tests for build_frame_object_key function."""

    def test_standard_format(self):
        """Format should be '{content_hash}/frames/{frame_ms}.jpg'."""
        content_hash = "abc123"
        frame_ms = 1500

        result = build_frame_object_key(content_hash, frame_ms)

        assert result == f"{content_hash}/frames/{frame_ms}.jpg"

    def test_zero_padding_not_applied(self):
        """Frame_ms is used as-is, no zero-padding applied."""
        content_hash = "hash"

        result = build_frame_object_key(content_hash, 1)
        assert result == "hash/frames/1.jpg"

        result = build_frame_object_key(content_hash, 123)
        assert result == "hash/frames/123.jpg"

    def test_same_hash_different_frame_ms(self):
        """Same hash with different frame_ms should produce different keys."""
        content_hash = "samehash"

        key1 = build_frame_object_key(content_hash, 1000)
        key2 = build_frame_object_key(content_hash, 2000)

        assert key1 != key2
        assert key1 == "samehash/frames/1000.jpg"
        assert key2 == "samehash/frames/2000.jpg"

    def test_no_extra_slashes_or_spaces(self):
        """String concatenation should have no extra slashes or spaces."""
        result = build_frame_object_key("hash", 1000)

        assert "//" not in result
        assert not result.startswith("/")
        assert not result.endswith("/")
        assert " " not in result


class TestCrossFunctionConsistency:
    """Cross-function consistency tests."""

    def test_media_object_key_has_videos_prefix(self):
        """build_media_object_key uses 'videos/' prefix."""
        result = build_media_object_key("hash", "video.mp4")
        assert result.startswith("videos/")

    def test_audio_object_key_no_videos_prefix(self):
        """build_audio_object_key does NOT use 'videos/' prefix."""
        result = build_audio_object_key("hash")
        assert not result.startswith("videos/")
        assert result == "hash/audio.ogg"

    def test_frame_object_key_no_videos_prefix(self):
        """build_frame_object_key does NOT use 'videos/' prefix."""
        result = build_frame_object_key("hash", 1000)
        assert not result.startswith("videos/")
        assert result == "hash/frames/1000.jpg"

    def test_all_keys_unique_for_same_hash(self):
        """All three functions produce unique keys for same content_hash."""
        content_hash = "testhash"

        media_key = build_media_object_key(content_hash, "original.mp4")
        audio_key = build_audio_object_key(content_hash)
        frame_key = build_frame_object_key(content_hash, 1000)

        keys = [media_key, audio_key, frame_key]
        assert len(set(keys)) == 3

        assert media_key == "videos/testhash/original.mp4"
        assert audio_key == "testhash/audio.ogg"
        assert frame_key == "testhash/frames/1000.jpg"