"""L1 unit tests for redis.py - pure logic key_* functions, zero external dependencies."""

from videomind.infrastructure.cache.redis import (
    key_media_status,
    key_media_progress,
    key_agent_checkpoint,
    key_gpu_lock,
)


class TestKeyMediaStatus:
    """Tests for key_media_status function."""

    def test_format_is_fixed(self):
        """Format should be exactly 'media:{media_id}:status'."""
        media_id = "media-123"
        result = key_media_status(media_id)

        assert result == f"media:{media_id}:status"

    def test_same_input_same_output(self):
        """Same input should always produce same output."""
        media_id = "video-456"

        result1 = key_media_status(media_id)
        result2 = key_media_status(media_id)

        assert result1 == result2

    def test_different_inputs_different_outputs(self):
        """Different media_id should produce different keys."""
        result1 = key_media_status("media-1")
        result2 = key_media_status("media-2")

        assert result1 != result2

    def test_no_extra_prefix_or_suffix(self):
        """Key should not have extra prefix or suffix."""
        media_id = "test-media"
        result = key_media_status(media_id)

        assert not result.startswith(":")
        assert not result.endswith(":")
        assert result.count(":") == 2  # "media:media-id:status" = 2 colons
        assert result == "media:test-media:status"

    def test_uuid_as_media_id(self):
        """Should work with UUID strings."""
        media_id = "550e8400-e29b-41d4-a716-446655440000"
        result = key_media_status(media_id)

        assert result == f"media:{media_id}:status"

    def test_special_characters_in_media_id(self):
        """Should handle special characters in media_id."""
        media_id = "media_with_underscores-and.dots"
        result = key_media_status(media_id)

        assert result == f"media:{media_id}:status"


class TestKeyMediaProgress:
    """Tests for key_media_progress function."""

    def test_format_is_fixed(self):
        """Format should be exactly 'media:{media_id}:progress'."""
        media_id = "media-123"
        result = key_media_progress(media_id)

        assert result == f"media:{media_id}:progress"

    def test_same_input_same_output(self):
        """Same input should always produce same output."""
        media_id = "video-456"

        result1 = key_media_progress(media_id)
        result2 = key_media_progress(media_id)

        assert result1 == result2

    def test_different_inputs_different_outputs(self):
        """Different media_id should produce different keys."""
        result1 = key_media_progress("media-1")
        result2 = key_media_progress("media-2")

        assert result1 != result2

    def test_no_extra_prefix_or_suffix(self):
        """Key should not have extra prefix or suffix."""
        media_id = "test-media"
        result = key_media_progress(media_id)

        assert not result.startswith(":")
        assert not result.endswith(":")
        assert result.count(":") == 2  # "media:media-id:progress" = 2 colons
        assert result == "media:test-media:progress"

    def test_key_is_different_from_status(self):
        """Progress key should be different from status key for same media_id."""
        media_id = "same-media"

        status_key = key_media_status(media_id)
        progress_key = key_media_progress(media_id)

        assert status_key != progress_key
        assert status_key == "media:same-media:status"
        assert progress_key == "media:same-media:progress"


class TestKeyAgentCheckpoint:
    """Tests for key_agent_checkpoint function."""

    def test_format_is_fixed(self):
        """Format should be exactly 'checkpoint:{task_id}'."""
        task_id = "task-123"
        result = key_agent_checkpoint(task_id)

        assert result == f"checkpoint:{task_id}"

    def test_same_input_same_output(self):
        """Same input should always produce same output."""
        task_id = "task-456"

        result1 = key_agent_checkpoint(task_id)
        result2 = key_agent_checkpoint(task_id)

        assert result1 == result2

    def test_different_inputs_different_outputs(self):
        """Different task_id should produce different keys."""
        result1 = key_agent_checkpoint("task-1")
        result2 = key_agent_checkpoint("task-2")

        assert result1 != result2

    def test_no_extra_prefix_or_suffix(self):
        """Key should not have extra prefix or suffix."""
        task_id = "test-task"
        result = key_agent_checkpoint(task_id)

        assert not result.startswith(":")
        assert not result.endswith(":")
        assert result.count(":") == 1  # "checkpoint:task-id" = 1 colon
        assert result == "checkpoint:test-task"

    def test_uuid_as_task_id(self):
        """Should work with UUID strings."""
        task_id = "550e8400-e29b-41d4-a716-446655440000"
        result = key_agent_checkpoint(task_id)

        assert result == f"checkpoint:{task_id}"


class TestKeyGpuLock:
    """Tests for key_gpu_lock function."""

    def test_format_is_fixed(self):
        """Format should be exactly 'gpu:lock:{stage}'."""
        stage = "transcribe"
        result = key_gpu_lock(stage)

        assert result == f"gpu:lock:{stage}"

    def test_same_input_same_output(self):
        """Same input should always produce same output."""
        stage = "encode"

        result1 = key_gpu_lock(stage)
        result2 = key_gpu_lock(stage)

        assert result1 == result2

    def test_different_inputs_different_outputs(self):
        """Different stage should produce different keys."""
        result1 = key_gpu_lock("transcribe")
        result2 = key_gpu_lock("encode")

        assert result1 != result2

    def test_no_extra_prefix_or_suffix(self):
        """Key should not have extra prefix or suffix."""
        stage = "transcribe"
        result = key_gpu_lock(stage)

        assert not result.startswith(":")
        assert not result.endswith(":")
        assert result.count(":") == 2  # "gpu:lock:stage" = 2 colons
        assert result == "gpu:lock:transcribe"


class TestKeyFunctionsNoCollision:
    """Cross-function collision tests - different functions should not collide."""

    def test_media_status_vs_media_progress(self):
        """media:status and media:progress should never collide for same ID."""
        media_id = "same-id"

        status = key_media_status(media_id)
        progress = key_media_progress(media_id)

        assert status != progress
        assert status == "media:same-id:status"
        assert progress == "media:same-id:progress"

    def test_all_four_functions_different_prefixes(self):
        """All four key functions should have distinct key patterns."""
        status = key_media_status("id")
        progress = key_media_progress("id")
        checkpoint = key_agent_checkpoint("id")
        gpu_lock = key_gpu_lock("stage")

        # Check distinct patterns
        assert status == "media:id:status"
        assert progress == "media:id:progress"
        assert checkpoint == "checkpoint:id"
        assert gpu_lock == "gpu:lock:stage"

        # All should be unique
        keys = [status, progress, checkpoint, gpu_lock]
        assert len(set(keys)) == 4

    def test_no_key_starts_or_ends_with_colon(self):
        """No key should start or end with colon."""
        keys = [
            key_media_status("id"),
            key_media_progress("id"),
            key_agent_checkpoint("id"),
            key_gpu_lock("stage"),
        ]

        for key in keys:
            assert not key.startswith(":"), f"Key starts with colon: {key}"
            assert not key.endswith(":"), f"Key ends with colon: {key}"