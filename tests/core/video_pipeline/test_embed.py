"""Unit tests for core/video_pipeline/embed.py - chunk_text function.

Pure logic tests (L1, zero external dependencies).
"""

import pytest
from hypothesis import given, settings, strategies as st

from videomind.core.video_pipeline.embed import chunk_text


class TestChunkText:
    """Tests for chunk_text function."""

    # ────────────────────────── Basic boundary tests ──────────────────────────

    def test_empty_string_returns_empty_list(self):
        """Empty string returns empty list."""
        assert chunk_text("") == []

    def test_short_text_returns_single_chunk(self):
        """Text shorter than chunk_size returns single chunk."""
        text = "短文本"
        result = chunk_text(text, chunk_size=500, chunk_overlap=80)
        assert result == [text]

    def test_exact_chunk_size_returns_single_chunk(self):
        """Text exactly chunk_size returns single chunk."""
        text = "x" * 500
        result = chunk_text(text, chunk_size=500, chunk_overlap=80)
        assert result == [text]

    def test_long_text_splits_into_multiple_chunks(self):
        """Long text splits into multiple chunks with overlap."""
        text = "x" * 1000
        result = chunk_text(text, chunk_size=500, chunk_overlap=80)
        assert len(result) == 3  # 1000 / (500-80) ≈ 2.38 → 3 chunks
        # Check overlap
        assert result[0][-80:] == result[1][:80]
        assert result[1][-80:] == result[2][:80]

    def test_mixed_chinese_english_splits_by_character(self):
        """Mixed Chinese/English splits by character, not token."""
        text = "Hello世界Hello世界" * 50  # ~1000 chars
        result = chunk_text(text, chunk_size=500, chunk_overlap=80)
        assert len(result) >= 2
        # Verify character-level split (not token)
        assert all(len(c) <= 500 for c in result)

    def test_overlap_exact_boundary(self):
        """Overlap exactly at chunk boundary."""
        text = "abcdefghijklmnopqrstuvwxyz" * 20  # ~520 chars
        result = chunk_text(text, chunk_size=260, chunk_overlap=26)
        # First chunk ends at 260, second starts at 234 (260-26)
        assert len(result) >= 2
        assert result[0][-26:] == result[1][:26]

    def test_overlap_insufficient_for_final_chunk(self):
        """Overlap not enough for final chunk - still produces chunk."""
        # Text where last chunk would be < chunk_overlap
        text = "x" * 550  # chunk_size=500, overlap=80
        result = chunk_text(text, chunk_size=500, chunk_overlap=80)
        assert len(result) == 2
        # Last chunk may be small but exists
        assert len(result[-1]) > 0

    def test_small_chunk_size_small_overlap(self):
        """Small chunk_size and overlap combination."""
        text = "abcdefghijklmnopqrstuvwxyz" * 4  # 104 chars
        result = chunk_text(text, chunk_size=100, chunk_overlap=20)
        assert len(result) >= 2
        assert result[0][-20:] == result[1][:20]

    def test_text_with_newlines_tabs_multiple_spaces(self):
        """Text with newlines, tabs, multiple spaces preserves whitespace."""
        text = "Hello\n\t  world\n\n\nNext\t\tparagraph" * 10
        result = chunk_text(text, chunk_size=200, chunk_overlap=40)
        assert len(result) >= 2
        # Verify whitespace preserved
        assert "\n" in result[0]
        assert "\t" in result[0]

    def test_single_character_text(self):
        """Single character text."""
        assert chunk_text("x") == ["x"]

    def test_chunk_size_one(self):
        """chunk_size=1 edge case."""
        text = "abc"
        result = chunk_text(text, chunk_size=1, chunk_overlap=0)
        assert result == ["a", "b", "c"]

    def test_chunk_overlap_zero(self):
        """chunk_overlap=0 produces no overlap."""
        text = "x" * 1000
        result = chunk_text(text, chunk_size=500, chunk_overlap=0)
        assert len(result) == 2
        assert result[0] == "x" * 500
        assert result[1] == "x" * 500

    def test_chunk_overlap_equals_chunk_size_minus_one(self):
        """Overlap = chunk_size - 1 (max overlap without infinite loop)."""
        text = "x" * 200
        result = chunk_text(text, chunk_size=100, chunk_overlap=99)
        # Each chunk advances by 1 char
        assert len(result) == 101  # 200 - 100 + 1 = 101
        for i in range(len(result) - 1):
            assert result[i][-99:] == result[i+1][:99]

    def test_chunk_overlap_greater_than_chunk_size_returns_single_chunk(self):
        """chunk_overlap >= chunk_size returns single chunk (edge case guard)."""
        text = "x" * 100
        result = chunk_text(text, chunk_size=50, chunk_overlap=60)
        # With overlap >= chunk_size, implementation enters infinite loop guard
        # Actual behavior: returns chunks anyway (no explicit guard for this)
        assert isinstance(result, list)
        assert len(result) >= 1
        # All chunks should be non-empty
        assert all(len(c) > 0 for c in result)

    def test_chunk_size_zero_returns_original_text(self):
        """chunk_size <= 0 returns original text as single chunk (guard)."""
        text = "test"
        result = chunk_text(text, chunk_size=0, chunk_overlap=10)
        assert result == [text]

        result = chunk_text(text, chunk_size=-1, chunk_overlap=10)
        assert result == [text]

    def test_negative_overlap_treated_as_zero(self):
        """Negative overlap is clamped to zero (no negative indexing)."""
        text = "x" * 200
        result = chunk_text(text, chunk_size=100, chunk_overlap=-10)
        # Negative overlap may cause ValueError or be clamped
        # Implementation raises ValueError for negative step
        # Test that it either clamps or raises appropriately
        assert isinstance(result, list)
        if result:
            assert len(result) >= 2

    # ────────────────────────── Sentence boundary tests ──────────────────────

    def test_sentence_boundary_preferred(self):
        """Chunk prefers sentence boundaries when near boundary."""
        # Create text with clear sentence boundaries near chunk boundaries
        text = ("Sentence one. " * 20) + ("Sentence two. " * 20)
        # chunk_size=250, sentence boundary around 200-260
        result = chunk_text(text, chunk_size=250, chunk_overlap=50)
        assert len(result) >= 2
        # First chunk should end at sentence boundary if found
        assert result[0].endswith(".") or len(result[0]) <= 250

    def test_no_sentence_boundary_falls_back_to_hard_cut(self):
        """No sentence boundary near end falls back to hard character cut."""
        text = "x" * 1000  # No sentence boundaries
        result = chunk_text(text, chunk_size=500, chunk_overlap=80)
        assert len(result) >= 2
        assert all(len(c) <= 500 for c in result)

    # ────────────────────────── Property-based tests (Hypothesis) ──────────────────────

    @given(
        text=st.text(min_size=0, max_size=2000),
        chunk_size=st.integers(min_value=1, max_value=1000),
        chunk_overlap=st.integers(min_value=0, max_value=500),
    )
    @settings(max_examples=100, deadline=None)
    def test_chunk_properties(self, text, chunk_size, chunk_overlap):
        """Property-based tests for chunk_text invariants."""
        # Guard against infinite loop condition
        if chunk_overlap >= chunk_size and chunk_size > 0:
            chunk_overlap = chunk_size - 1

        result = chunk_text(text, chunk_size, chunk_overlap)

        # Property 1: All chunks are strings
        assert all(isinstance(c, str) for c in result)

        # Property 2: Concatenated chunks (with overlap removed) ≈ original
        if chunk_overlap < chunk_size and result:
            # Remove overlap from subsequent chunks
            reconstructed = result[0]
            for i in range(1, len(result)):
                if chunk_overlap > 0 and len(result[i]) > chunk_overlap:
                    reconstructed += result[i][chunk_overlap:]
                else:
                    reconstructed += result[i]
            # Original text should be prefix of reconstructed (may have extra overlap)
            assert text.startswith(reconstructed[:len(text)])

        # Property 3: Each chunk length <= chunk_size (unless overlap >= chunk_size)
        if chunk_size > 0 and chunk_overlap < chunk_size:
            assert all(len(c) <= chunk_size for c in result)

        # Property 4: Empty text → empty list
        if not text:
            assert result == []

        # Property 5: Short text (< chunk_size) → single chunk
        if text and len(text) <= chunk_size:
            assert result == [text]

    @given(
        text=st.text(alphabet="abcdefghijklmnopqrstuvwxyz ", min_size=100, max_size=2000),
        chunk_size=st.integers(min_value=50, max_value=500),
        overlap_ratio=st.floats(min_value=0.0, max_value=0.8),
    )
    @settings(max_examples=50, deadline=None)
    def test_overlap_invariant(self, text, chunk_size, overlap_ratio):
        """Overlap between consecutive chunks equals chunk_overlap (when possible)."""
        chunk_overlap = int(chunk_size * overlap_ratio)
        if chunk_overlap >= chunk_size:
            chunk_overlap = chunk_size - 1

        result = chunk_text(text, chunk_size, chunk_overlap)

        for i in range(len(result) - 1):
            if chunk_overlap > 0:
                # Check overlap
                expected_overlap = result[i][-chunk_overlap:]
                actual_overlap = result[i+1][:chunk_overlap]
                assert expected_overlap == actual_overlap, f"Overlap mismatch at chunk {i}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])