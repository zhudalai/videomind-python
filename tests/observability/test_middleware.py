"""L1 unit tests for observability/middleware.py - path normalization."""

from videomind.observability.middleware import PrometheusMiddleware


class TestNormalizePath:
    """Tests for PrometheusMiddleware._normalize_path."""

    def test_uuid_path_normalized(self):
        """UUID in path replaced with {id}."""
        path = "/api/videos/123e4567-e89b-12d3-a456-426614174000"
        result = PrometheusMiddleware._normalize_path(path)
        assert result == "/api/videos/{id}"

    def test_uuid_with_trailing_slash_normalized(self):
        """UUID followed by slash normalized."""
        path = "/api/videos/123e4567-e89b-12d3-a456-426614174000/"
        result = PrometheusMiddleware._normalize_path(path)
        assert result == "/api/videos/{id}/"

    def test_uuid_in_middle_of_path(self):
        """UUID in middle of path normalized."""
        path = "/api/videos/123e4567-e89b-12d3-a456-426614174000/progress"
        result = PrometheusMiddleware._normalize_path(path)
        assert result == "/api/videos/{id}/progress"

    def test_multiple_uuids_all_normalized(self):
        """Multiple UUIDs in path all replaced."""
        path = "/api/users/11111111-1111-1111-1111-111111111111/videos/22222222-2222-2222-2222-222222222222"
        result = PrometheusMiddleware._normalize_path(path)
        assert result == "/api/users/{id}/videos/{id}"

    def test_numeric_id_normalized(self):
        """Numeric ID replaced with {id}."""
        path = "/api/videos/42"
        result = PrometheusMiddleware._normalize_path(path)
        assert result == "/api/videos/{id}"

    def test_numeric_id_with_trailing_slash(self):
        """Numeric ID with trailing slash normalized."""
        path = "/api/videos/123/"
        result = PrometheusMiddleware._normalize_path(path)
        assert result == "/api/videos/{id}/"

    def test_numeric_id_in_middle(self):
        """Numeric ID in middle of path normalized."""
        path = "/api/users/123/videos/456"
        result = PrometheusMiddleware._normalize_path(path)
        assert result == "/api/users/{id}/videos/{id}"

    def test_static_path_unchanged(self):
        """Paths without UUID/numeric IDs unchanged."""
        path = "/api/health/ready"
        result = PrometheusMiddleware._normalize_path(path)
        assert result == "/api/health/ready"

    def test_mixed_uuid_and_numeric(self):
        """Mixed UUID and numeric IDs both normalized."""
        path = "/api/users/123/videos/123e4567-e89b-12d3-a456-426614174000"
        result = PrometheusMiddleware._normalize_path(path)
        assert result == "/api/users/{id}/videos/{id}"

    def test_uuid_uppercase_hex_normalized(self):
        """Uppercase UUID hex also matched."""
        path = "/api/videos/123E4567-E89B-12D3-A456-426614174000"
        result = PrometheusMiddleware._normalize_path(path)
        assert result == "/api/videos/{id}"

    def test_partial_uuid_not_matched(self):
        """Partial UUID-like strings not matched."""
        path = "/api/videos/123e4567"  # too short
        result = PrometheusMiddleware._normalize_path(path)
        assert result == "/api/videos/123e4567"

    def test_partial_numeric_not_matched_mid_segment(self):
        """Numeric in middle of segment not matched (only full segment)."""
        path = "/api/videos/v123video"  # not a standalone segment
        result = PrometheusMiddleware._normalize_path(path)
        assert result == "/api/videos/v123video"

    def test_empty_path(self):
        """Empty path returns empty."""
        result = PrometheusMiddleware._normalize_path("")
        assert result == ""

    def test_root_path(self):
        """Root path unchanged."""
        result = PrometheusMiddleware._normalize_path("/")
        assert result == "/"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])