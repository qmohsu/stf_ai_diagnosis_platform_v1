"""Unit tests for OCR service (app.rag.ocr).

Tests cover:
- Raw OCR extraction with confidence filtering
- Structured extraction (part numbers, torque, dimensions)
- Text overlap / deduplication logic
- Edge cases (empty input, low confidence, no detections)

The easyocr reader is mocked to avoid heavy model initialisation
and ~200 MB download during unit tests.
"""

import io
from unittest.mock import MagicMock, patch

import pytest

from app.rag.ocr import (
    _MIN_CONFIDENCE,
    _PART_NUMBER_RE,
    _TORQUE_RE,
    _DIMENSION_RE,
    compute_text_overlap,
    ocr_extract_structured,
    ocr_image_bytes,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_png_bytes() -> bytes:
    """Create a minimal valid PNG image for testing."""
    from PIL import Image

    img = Image.new("RGB", (100, 100), color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _mock_readtext_results():
    """Return typical easyocr readtext output."""
    return [
        ([[0, 0], [100, 0], [100, 20], [0, 20]],
         "90890-03180", 0.95),
        ([[0, 30], [120, 30], [120, 50], [0, 50]],
         "75 N·m", 0.88),
        ([[0, 60], [80, 60], [80, 80], [0, 80]],
         "ø38 mm", 0.72),
        ([[0, 90], [50, 90], [50, 100], [0, 100]],
         "noise", 0.15),  # Below confidence threshold
    ]


# ------------------------------------------------------------------
# TestOcrImageBytes
# ------------------------------------------------------------------

class TestOcrImageBytes:
    """Tests for ocr_image_bytes()."""

    @patch("app.rag.ocr._get_reader")
    def test_returns_detections_above_threshold(
        self, mock_get_reader,
    ):
        """OCR returns only detections above _MIN_CONFIDENCE."""
        mock_reader = MagicMock()
        mock_reader.readtext.return_value = (
            _mock_readtext_results()
        )
        mock_get_reader.return_value = mock_reader

        results = ocr_image_bytes(_make_png_bytes())

        assert len(results) == 3
        assert all(
            r["confidence"] >= _MIN_CONFIDENCE
            for r in results
        )

    @patch("app.rag.ocr._get_reader")
    def test_low_confidence_filtered(
        self, mock_get_reader,
    ):
        """Detections below 0.3 confidence are excluded."""
        mock_reader = MagicMock()
        mock_reader.readtext.return_value = [
            ([[0, 0], [10, 0], [10, 10], [0, 10]],
             "faint", 0.1),
            ([[0, 0], [10, 0], [10, 10], [0, 10]],
             "barely", 0.29),
        ]
        mock_get_reader.return_value = mock_reader

        results = ocr_image_bytes(_make_png_bytes())

        assert len(results) == 0

    def test_empty_image_returns_empty(self):
        """OCR on empty bytes returns an empty list."""
        results = ocr_image_bytes(b"")
        assert results == []

    @patch("app.rag.ocr._get_reader")
    def test_text_stripped(self, mock_get_reader):
        """Detected text is stripped of whitespace."""
        mock_reader = MagicMock()
        mock_reader.readtext.return_value = [
            ([[0, 0], [10, 0], [10, 10], [0, 10]],
             "  padded text  ", 0.9),
        ]
        mock_get_reader.return_value = mock_reader

        results = ocr_image_bytes(_make_png_bytes())

        assert results[0]["text"] == "padded text"

    @patch("app.rag.ocr._get_reader")
    def test_readtext_exception_returns_empty(
        self, mock_get_reader,
    ):
        """Exception during readtext returns empty list."""
        mock_reader = MagicMock()
        mock_reader.readtext.side_effect = RuntimeError(
            "model not loaded"
        )
        mock_get_reader.return_value = mock_reader

        results = ocr_image_bytes(_make_png_bytes())

        assert results == []


# ------------------------------------------------------------------
# TestOcrExtractStructured
# ------------------------------------------------------------------

class TestOcrExtractStructured:
    """Tests for ocr_extract_structured()."""

    @patch("app.rag.ocr._get_reader")
    def test_part_numbers_extracted(
        self, mock_get_reader,
    ):
        """Part number pattern 90890-XXXXX is captured."""
        mock_reader = MagicMock()
        mock_reader.readtext.return_value = (
            _mock_readtext_results()
        )
        mock_get_reader.return_value = mock_reader

        result = ocr_extract_structured(_make_png_bytes())

        assert "90890-03180" in result["part_numbers"]

    @patch("app.rag.ocr._get_reader")
    def test_torque_values_extracted(
        self, mock_get_reader,
    ):
        """Torque values like '75 N·m' are captured."""
        mock_reader = MagicMock()
        mock_reader.readtext.return_value = (
            _mock_readtext_results()
        )
        mock_get_reader.return_value = mock_reader

        result = ocr_extract_structured(_make_png_bytes())

        assert len(result["torque_values"]) >= 1
        assert any(
            "75" in v for v in result["torque_values"]
        )

    @patch("app.rag.ocr._get_reader")
    def test_dimensions_extracted(
        self, mock_get_reader,
    ):
        """Dimensions like 'ø38 mm' are captured."""
        mock_reader = MagicMock()
        mock_reader.readtext.return_value = (
            _mock_readtext_results()
        )
        mock_get_reader.return_value = mock_reader

        result = ocr_extract_structured(_make_png_bytes())

        assert len(result["dimensions"]) >= 1
        assert any(
            "38" in d for d in result["dimensions"]
        )

    @patch("app.rag.ocr._get_reader")
    def test_full_text_joined(self, mock_get_reader):
        """full_text is all detected texts joined by space."""
        mock_reader = MagicMock()
        mock_reader.readtext.return_value = (
            _mock_readtext_results()
        )
        mock_get_reader.return_value = mock_reader

        result = ocr_extract_structured(_make_png_bytes())

        assert "90890-03180" in result["full_text"]
        assert "75 N·m" in result["full_text"]


# ------------------------------------------------------------------
# TestRegexPatterns
# ------------------------------------------------------------------

class TestRegexPatterns:
    """Direct tests for the OCR regex patterns."""

    def test_part_number_variants(self):
        """Various Yamaha part number formats are matched."""
        cases = [
            "90890-03180",
            "90890-01275",
            "90890-04081",
        ]
        for case in cases:
            assert _PART_NUMBER_RE.search(case), (
                f"Failed to match: {case}"
            )

    def test_torque_variants(self):
        """Various torque value formats are matched."""
        cases = [
            "75 N·m",
            "3.8 kgf·m",
            "27 lb·ft",
            "12 N.m",
            "1.6 kgf.m",
        ]
        for case in cases:
            assert _TORQUE_RE.search(case), (
                f"Failed to match: {case}"
            )

    def test_dimension_variants(self):
        """Various dimension formats are matched."""
        cases = [
            "ø38 mm",
            "ø84 mm",
            "10 mm",
            "31.4 mm",
        ]
        for case in cases:
            assert _DIMENSION_RE.search(case), (
                f"Failed to match: {case}"
            )


# ------------------------------------------------------------------
# TestComputeTextOverlap
# ------------------------------------------------------------------

class TestComputeTextOverlap:
    """Tests for compute_text_overlap()."""

    def test_high_overlap_detected(self):
        """OCR text that duplicates page text is redundant."""
        ocr_text = "引擎 冷卻 系統 檢查"
        page_text = "引擎冷卻系統檢查包含水箱蓋拆卸程序"

        assert compute_text_overlap(ocr_text, page_text)

    def test_low_overlap_keeps_ocr(self):
        """OCR text with new content is NOT redundant."""
        ocr_text = "90890-03180 75 N·m ø38 mm"
        page_text = "拆卸轉向機柱步驟如下"

        assert not compute_text_overlap(
            ocr_text, page_text,
        )

    def test_empty_ocr_is_redundant(self):
        """Empty OCR text is always considered redundant."""
        assert compute_text_overlap("", "some page text")

    def test_custom_threshold(self):
        """Custom threshold changes overlap sensitivity."""
        ocr = "part one two three four five"
        page = "part one two three"
        # 4/6 = 0.67 overlap
        assert not compute_text_overlap(
            ocr, page, threshold=0.8,
        )
        assert compute_text_overlap(
            ocr, page, threshold=0.6,
        )

    def test_mixed_cjk_english(self):
        """Mixed CJK/English overlap is computed correctly."""
        ocr = "90890-03180 更換器 38 mm"
        page = "更換器 YM-A9409-7 前叉油封驅動鋒具"

        # Only '更換器' overlaps → low overlap
        assert not compute_text_overlap(
            ocr, page,
        )
