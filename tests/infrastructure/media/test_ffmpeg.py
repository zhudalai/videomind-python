"""Unit tests for infrastructure/media/ffmpeg.py - _parse_fps and ProbeResult.as_dict.

Pure logic tests (L1, zero external dependencies).
"""

import pytest
from unittest.mock import Mock, patch

from videomind.infrastructure.media.ffmpeg import ProbeResult, _parse_fps


class TestParseFps:
    """Tests for _parse_fps function."""

    def test_parse_fps_standard_integer(self):
        """Standard integer FPS like '30/1' -> 30.0"""
        assert _parse_fps("30/1") == 30.0

    def test_parse_fps_ntsc_30(self):
        """NTSC 30fps: '30000/1001' -> 29.97002997..."""
        result = _parse_fps("30000/1001")
        assert abs(result - 29.97002997002997) < 1e-10

    def test_parse_fps_ntsc_24(self):
        """NTSC 24fps: '24000/1001' -> 23.976..."""
        result = _parse_fps("24000/1001")
        assert abs(result - 23.976023976023978) < 1e-10

    def test_parse_fps_60fps(self):
        """60fps: '60/1' -> 60.0"""
        assert _parse_fps("60/1") == 60.0

    def test_parse_fps_25fps(self):
        """25fps: '25/1' -> 25.0"""
        assert _parse_fps("25/1") == 25.0

    def test_parse_fps_50fps(self):
        """50fps: '50/1' -> 50.0"""
        assert _parse_fps("50/1") == 50.0

    def test_parse_fps_fractional(self):
        """Fractional FPS like '120000/1001' -> ~119.88"""
        result = _parse_fps("120000/1001")
        assert abs(result - 119.88011988011988) < 1e-10

    def test_parse_fps_zero_division_zero_zero(self):
        """'0/0' returns 0.0 (implementation handles gracefully)."""
        result = _parse_fps("0/0")
        assert result == 0.0

    def test_parse_fps_invalid_string_returns_zero(self):
        """Invalid string like 'abc' returns 0.0 (graceful fallback)."""
        result = _parse_fps("abc")
        assert result == 0.0

    def test_parse_fps_empty_string_returns_zero(self):
        """Empty string returns 0.0 (graceful fallback)."""
        result = _parse_fps("")
        assert result == 0.0

    def test_parse_fps_none_returns_zero(self):
        """None returns 0.0 (graceful fallback)."""
        result = _parse_fps(None)
        assert result == 0.0

    def test_parse_fps_malformed_fraction_returns_zero(self):
        """Malformed fraction like '30/' or '/1' returns 0.0."""
        assert _parse_fps("30/") == 0.0
        assert _parse_fps("/1") == 0.0

    def test_parse_fps_negative_values(self):
        """Negative values work mathematically"""
        result = _parse_fps("-30/1")
        assert result == -30.0

    def test_parse_fps_whitespace_handling(self):
        """Whitespace around fraction is handled"""
        assert _parse_fps(" 30/1 ") == 30.0
        assert _parse_fps("30 / 1") == 30.0  # if implementation handles it


class TestProbeResultAsDict:
    """Tests for ProbeResult.as_dict method."""

    def test_as_dict_returns_all_expected_keys(self):
        """as_dict returns dict with all expected keys."""
        probe = ProbeResult(
            duration_ms=120000,
            width=1920,
            height=1080,
            fps=30.0,
            mime_type="video/mp4",
        )
        result = probe.as_dict()

        expected_keys = {"duration_ms", "width", "height", "fps", "mime_type"}
        assert set(result.keys()) == expected_keys

    def test_as_dict_values_match_input(self):
        """as_dict values match constructor input."""
        probe = ProbeResult(
            duration_ms=120000,
            width=1920,
            height=1080,
            fps=29.97002997002997,
            mime_type="video/hevc",
        )
        result = probe.as_dict()

        assert result["duration_ms"] == 120000
        assert result["width"] == 1920
        assert result["height"] == 1080
        assert abs(result["fps"] - 29.97002997002997) < 1e-10
        assert result["mime_type"] == "video/hevc"

    def test_as_dict_returns_new_dict_each_call(self):
        """as_dict returns a new dict each call (not cached reference)."""
        probe = ProbeResult(
            duration_ms=1000,
            width=640,
            height=480,
            fps=25.0,
            mime_type="video/webm",
        )
        d1 = probe.as_dict()
        d2 = probe.as_dict()
        assert d1 is not d2
        assert d1 == d2

    def test_as_dict_mime_type_none(self):
        """mime_type can be None/empty in as_dict output."""
        probe = ProbeResult(
            duration_ms=1000,
            width=640,
            height=480,
            fps=25.0,
            mime_type=None,
        )
        result = probe.as_dict()
        assert result["mime_type"] is None

    def test_as_dict_zero_values(self):
        """Zero values are preserved in as_dict."""
        probe = ProbeResult(
            duration_ms=0,
            width=0,
            height=0,
            fps=0.0,
            mime_type="",
        )
        result = probe.as_dict()
        assert result["duration_ms"] == 0
        assert result["width"] == 0
        assert result["height"] == 0
        assert result["fps"] == 0.0
        assert result["mime_type"] == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])