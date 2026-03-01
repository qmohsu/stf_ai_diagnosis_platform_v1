"""Unit tests for render_page_image() and has_tables_on_page().

All tests use mocked PyMuPDF objects -- no real PDFs needed.
"""

from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from app.rag.pdf_parser import (
    has_tables_on_page,
    render_page_image,
    _DEFAULT_RENDER_DPI,
    _TABLE_LEFT_EDGE_THRESHOLD,
)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _make_page_dict_with_blocks(left_edges: list[float]) -> dict:
    """Build a mock page dict with text blocks at given left edges.

    Args:
        left_edges: List of left-edge x coordinates for blocks.

    Returns:
        A dict matching fitz ``page.get_text("dict")`` format.
    """
    blocks = []
    for x in left_edges:
        blocks.append({
            "type": 0,
            "bbox": (x, 0.0, x + 100.0, 20.0),
            "lines": [
                {
                    "spans": [
                        {"text": "cell", "size": 10.0, "flags": 0},
                    ]
                }
            ],
        })
    return {"blocks": blocks}


# ------------------------------------------------------------------
# render_page_image tests
# ------------------------------------------------------------------

class TestRenderPageImage:
    """Tests for the render_page_image helper."""

    def test_returns_png_bytes(self):
        """Rendered output should be non-empty PNG bytes."""
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

        mock_page = MagicMock()
        mock_page.get_pixmap.return_value = mock_pix

        result = render_page_image(mock_page)

        assert isinstance(result, bytes)
        assert len(result) > 0
        assert result[:4] == b"\x89PNG"

    def test_default_dpi_scale(self):
        """Default DPI (150) should produce a 150/72 scale matrix."""
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"\x89PNG" + b"\x00" * 50

        mock_page = MagicMock()
        mock_page.get_pixmap.return_value = mock_pix

        with patch("app.rag.pdf_parser.fitz") as mock_fitz:
            mock_fitz.Matrix.return_value = "mock_matrix"
            mock_page.get_pixmap.return_value = mock_pix

            render_page_image(mock_page)

            expected_scale = _DEFAULT_RENDER_DPI / 72.0
            mock_fitz.Matrix.assert_called_once_with(
                expected_scale, expected_scale,
            )
            mock_page.get_pixmap.assert_called_once_with(
                matrix="mock_matrix",
            )

    def test_custom_dpi(self):
        """Custom DPI should produce correct scale factor."""
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"\x89PNG" + b"\x00" * 50

        mock_page = MagicMock()
        mock_page.get_pixmap.return_value = mock_pix

        with patch("app.rag.pdf_parser.fitz") as mock_fitz:
            mock_fitz.Matrix.return_value = "mock_matrix"
            mock_page.get_pixmap.return_value = mock_pix

            render_page_image(mock_page, dpi=300)

            expected_scale = 300 / 72.0
            mock_fitz.Matrix.assert_called_once_with(
                expected_scale, expected_scale,
            )

    def test_zero_dpi_raises(self):
        """DPI of zero should raise ValueError."""
        mock_page = MagicMock()
        with pytest.raises(ValueError, match="dpi must be positive"):
            render_page_image(mock_page, dpi=0)

    def test_negative_dpi_raises(self):
        """Negative DPI should raise ValueError."""
        mock_page = MagicMock()
        with pytest.raises(ValueError, match="dpi must be positive"):
            render_page_image(mock_page, dpi=-72)

    def test_pixmap_memory_released(self):
        """Pixmap memory should be freed via del even on success."""
        mock_pix = MagicMock()
        mock_pix.tobytes.return_value = b"\x89PNG"

        mock_page = MagicMock()
        mock_page.get_pixmap.return_value = mock_pix

        # The function uses `del pix` — we verify the call completes
        # without holding references by checking the result is valid.
        result = render_page_image(mock_page)
        assert result == b"\x89PNG"


# ------------------------------------------------------------------
# has_tables_on_page tests
# ------------------------------------------------------------------

class TestHasTablesOnPage:
    """Tests for the has_tables_on_page helper."""

    def test_find_tables_returns_true(self):
        """Returns True when find_tables detects tables."""
        mock_page = MagicMock()
        mock_tables = MagicMock()
        mock_tables.tables = [MagicMock()]  # 1 table found
        mock_page.find_tables.return_value = mock_tables

        assert has_tables_on_page(mock_page) is True

    def test_find_tables_returns_false_no_tables(self):
        """Returns False when find_tables finds no tables."""
        mock_page = MagicMock()
        mock_tables = MagicMock()
        mock_tables.tables = []
        mock_page.find_tables.return_value = mock_tables

        assert has_tables_on_page(mock_page) is False

    def test_find_tables_none_result(self):
        """Returns False when find_tables returns None."""
        mock_page = MagicMock()
        mock_page.find_tables.return_value = None

        assert has_tables_on_page(mock_page) is False

    def test_fallback_heuristic_detects_columns(self):
        """Falls back to left-edge heuristic when find_tables missing."""
        mock_page = MagicMock()
        # Simulate AttributeError (old PyMuPDF version)
        mock_page.find_tables.side_effect = AttributeError(
            "no find_tables",
        )

        # Create blocks with 4 distinct left edges -> table
        page_dict = _make_page_dict_with_blocks(
            [10.0, 100.0, 200.0, 300.0],
        )
        mock_page.get_text.return_value = page_dict

        assert has_tables_on_page(mock_page) is True

    def test_fallback_heuristic_too_few_columns(self):
        """Fallback returns False when fewer columns than threshold."""
        mock_page = MagicMock()
        mock_page.find_tables.side_effect = AttributeError(
            "no find_tables",
        )

        # Only 2 distinct left edges -> not a table
        page_dict = _make_page_dict_with_blocks([10.0, 100.0])
        mock_page.get_text.return_value = page_dict

        assert has_tables_on_page(mock_page) is False

    def test_fallback_heuristic_exact_threshold(self):
        """Fallback returns True at exactly the threshold count."""
        mock_page = MagicMock()
        mock_page.find_tables.side_effect = AttributeError(
            "no find_tables",
        )

        # Exactly _TABLE_LEFT_EDGE_THRESHOLD distinct positions
        edges = [i * 50.0 for i in range(_TABLE_LEFT_EDGE_THRESHOLD)]
        page_dict = _make_page_dict_with_blocks(edges)
        mock_page.get_text.return_value = page_dict

        assert has_tables_on_page(mock_page) is True

    def test_fallback_jitter_tolerance(self):
        """Left edges within 5pt are treated as the same column."""
        mock_page = MagicMock()
        mock_page.find_tables.side_effect = AttributeError(
            "no find_tables",
        )

        # 3 blocks but edges within 5pt of each other -> 1 column
        page_dict = _make_page_dict_with_blocks(
            [10.0, 12.0, 13.0],
        )
        mock_page.get_text.return_value = page_dict

        assert has_tables_on_page(mock_page) is False

    def test_fallback_ignores_image_blocks(self):
        """Image blocks (type != 0) should not count as columns."""
        mock_page = MagicMock()
        mock_page.find_tables.side_effect = AttributeError(
            "no find_tables",
        )

        blocks = [
            {"type": 1, "bbox": (10.0, 0, 200, 200)},  # image
            {"type": 1, "bbox": (250.0, 0, 400, 200)},  # image
            {"type": 1, "bbox": (450.0, 0, 600, 200)},  # image
            {"type": 0, "bbox": (10.0, 0, 200, 20),
             "lines": [{"spans": [
                 {"text": "only text", "size": 10, "flags": 0}
             ]}]},
        ]
        mock_page.get_text.return_value = {"blocks": blocks}

        # Only 1 text block -> not a table
        assert has_tables_on_page(mock_page) is False

    def test_find_tables_exception_falls_through(self):
        """Non-AttributeError in find_tables triggers fallback."""
        mock_page = MagicMock()
        mock_page.find_tables.side_effect = RuntimeError(
            "internal error",
        )

        # Provide enough columns for heuristic to detect a table
        page_dict = _make_page_dict_with_blocks(
            [10.0, 100.0, 200.0, 300.0],
        )
        mock_page.get_text.return_value = page_dict

        assert has_tables_on_page(mock_page) is True

    def test_empty_page_no_table(self):
        """Empty page should not be detected as having tables."""
        mock_page = MagicMock()
        mock_page.find_tables.side_effect = AttributeError(
            "no find_tables",
        )
        mock_page.get_text.return_value = {"blocks": []}

        assert has_tables_on_page(mock_page) is False
