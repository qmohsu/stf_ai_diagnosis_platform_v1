"""Unit tests for PDF parser module.

TODOs from code review:
- TODO(9): Add tests for large PDF performance (50MB+) with pytest.mark.slow
- TODO(9): Add tests for password-protected PDF handling
- TODO(9): Add tests for corrupt PDF handling
- TODO(9): Add tests for memory exhaustion scenarios
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.rag.pdf_parser import (
    extract_text_from_pdf,
    extract_text_from_pdf_async,
    extract_images_from_page,
    _MIN_IMAGE_WIDTH,
    _MIN_IMAGE_HEIGHT,
    _MIN_IMAGE_BYTES,
)


class TestExtractTextFromPdf:
    """Tests for extract_text_from_pdf function."""

    def test_nonexistent_pdf_raises_error(self, tmp_path):
        """Test that FileNotFoundError is raised for non-existent files."""
        fake_path = tmp_path / "nonexistent.pdf"
        with pytest.raises(FileNotFoundError) as exc_info:
            extract_text_from_pdf(fake_path)
        assert "PDF file not found" in str(exc_info.value)

    @patch("app.rag.pdf_parser.fitz")
    def test_extract_text_from_valid_pdf(self, mock_fitz, tmp_path):
        """Test that text is extracted from a valid PDF."""
        # Create a mock PDF document
        mock_doc = MagicMock()
        mock_page1 = MagicMock()
        mock_page1.get_text.return_value = "Page one content"
        mock_page2 = MagicMock()
        mock_page2.get_text.return_value = "Page two content"
        mock_doc.__iter__ = lambda self: iter([mock_page1, mock_page2])
        mock_fitz.open.return_value = mock_doc

        # Create a dummy file so path.exists() returns True
        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4")

        result = extract_text_from_pdf(pdf_path)

        assert "Page one content" in result
        assert "Page two content" in result
        mock_doc.close.assert_called_once()

    @patch("app.rag.pdf_parser.fitz")
    def test_extract_preserves_page_markers(self, mock_fitz, tmp_path):
        """Test that page markers [Page N] are included in output."""
        mock_doc = MagicMock()
        mock_page1 = MagicMock()
        mock_page1.get_text.return_value = "First page"
        mock_page2 = MagicMock()
        mock_page2.get_text.return_value = "Second page"
        mock_doc.__iter__ = lambda self: iter([mock_page1, mock_page2])
        mock_fitz.open.return_value = mock_doc

        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4")

        result = extract_text_from_pdf(pdf_path)

        assert "[Page 1]" in result
        assert "[Page 2]" in result

    @patch("app.rag.pdf_parser.fitz")
    def test_empty_pdf_returns_empty_text(self, mock_fitz, tmp_path):
        """Test that PDFs with no text content return empty string."""
        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_page.get_text.return_value = "   \n  "  # whitespace only
        mock_doc.__iter__ = lambda self: iter([mock_page])
        mock_fitz.open.return_value = mock_doc

        pdf_path = tmp_path / "empty.pdf"
        pdf_path.write_bytes(b"%PDF-1.4")

        result = extract_text_from_pdf(pdf_path)

        assert result == ""

    @patch("app.rag.pdf_parser.fitz")
    def test_document_is_closed_after_extraction(self, mock_fitz, tmp_path):
        """Test that the PDF document is properly closed after extraction."""
        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_page.get_text.return_value = "Content"
        mock_doc.__iter__ = lambda self: iter([mock_page])
        mock_fitz.open.return_value = mock_doc

        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4")

        extract_text_from_pdf(pdf_path)

        mock_doc.close.assert_called_once()

    @patch("app.rag.pdf_parser.fitz")
    def test_document_closed_even_on_error(self, mock_fitz, tmp_path):
        """Test that the PDF document is closed even when an error occurs."""
        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_page.get_text.side_effect = Exception("Extraction error")
        mock_doc.__iter__ = lambda self: iter([mock_page])
        mock_fitz.open.return_value = mock_doc

        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4")

        with pytest.raises(Exception, match="Extraction error"):
            extract_text_from_pdf(pdf_path)

        mock_doc.close.assert_called_once()


class TestExtractImagesFromPage:
    """Tests for extract_images_from_page function."""

    def test_small_images_filtered_out(self):
        """Images smaller than minimum dimensions are skipped."""
        mock_doc = MagicMock()
        mock_page = MagicMock()

        # Return one small image
        mock_page.get_images.return_value = [(1, 0, 0, 0, 0, 0, 0)]
        mock_pix = MagicMock()
        mock_pix.width = 30  # below _MIN_IMAGE_WIDTH
        mock_pix.height = 30  # below _MIN_IMAGE_HEIGHT

        with patch("app.rag.pdf_parser.fitz") as mock_fitz:
            mock_fitz.Pixmap.return_value = mock_pix
            result = extract_images_from_page(mock_doc, mock_page, 1)

        assert len(result) == 0

    def test_large_image_returned(self):
        """Images meeting minimum size and byte thresholds are returned."""
        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_page.get_images.return_value = [(1, 0, 0, 0, 0, 0, 0)]

        mock_pix = MagicMock()
        mock_pix.width = 200
        mock_pix.height = 200
        mock_pix.n = 3  # RGB
        mock_pix.tobytes.return_value = b"\x89PNG" + b"\x00" * (_MIN_IMAGE_BYTES + 100)

        with patch("app.rag.pdf_parser.fitz") as mock_fitz:
            mock_fitz.Pixmap.return_value = mock_pix
            result = extract_images_from_page(mock_doc, mock_page, 1)

        assert len(result) == 1
        assert result[0]["index"] == 1

    def test_cmyk_to_rgb_conversion(self):
        """CMYK images (n > 4) are converted to RGB."""
        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_page.get_images.return_value = [(1, 0, 0, 0, 0, 0, 0)]

        mock_pix_cmyk = MagicMock()
        mock_pix_cmyk.width = 200
        mock_pix_cmyk.height = 200
        mock_pix_cmyk.n = 5  # CMYK + alpha

        mock_pix_rgb = MagicMock()
        mock_pix_rgb.width = 200
        mock_pix_rgb.height = 200
        mock_pix_rgb.n = 3
        mock_pix_rgb.tobytes.return_value = b"\x89PNG" + b"\x00" * (_MIN_IMAGE_BYTES + 100)

        with patch("app.rag.pdf_parser.fitz") as mock_fitz:
            # First call returns CMYK pixmap, second returns RGB conversion
            mock_fitz.Pixmap.side_effect = [mock_pix_cmyk, mock_pix_rgb]
            result = extract_images_from_page(mock_doc, mock_page, 1)

        assert len(result) == 1
        # Verify fitz.Pixmap was called twice (once for xref, once for conversion)
        assert mock_fitz.Pixmap.call_count == 2
        mock_fitz.Pixmap.assert_any_call(mock_fitz.csRGB, mock_pix_cmyk)

    def test_image_extraction_failure_graceful(self):
        """Errors during image extraction are caught and skipped."""
        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_page.get_images.return_value = [(1, 0, 0, 0, 0, 0, 0)]

        with patch("app.rag.pdf_parser.fitz") as mock_fitz:
            mock_fitz.Pixmap.side_effect = RuntimeError("corrupt image")
            result = extract_images_from_page(mock_doc, mock_page, 1)

        assert len(result) == 0

    def test_small_byte_size_filtered(self):
        """Images under _MIN_IMAGE_BYTES are filtered out."""
        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_page.get_images.return_value = [(1, 0, 0, 0, 0, 0, 0)]

        mock_pix = MagicMock()
        mock_pix.width = 200
        mock_pix.height = 200
        mock_pix.n = 3
        mock_pix.tobytes.return_value = b"\x89PNG" + b"\x00" * 100  # < 5KB

        with patch("app.rag.pdf_parser.fitz") as mock_fitz:
            mock_fitz.Pixmap.return_value = mock_pix
            result = extract_images_from_page(mock_doc, mock_page, 1)

        assert len(result) == 0


class TestExtractTextWithImages:
    """Tests for async extract_text_from_pdf_async with image description."""

    @pytest.mark.asyncio
    @patch("app.rag.pdf_parser.fitz")
    async def test_describe_images_false_skips_images(self, mock_fitz, tmp_path):
        """Default behavior: no image descriptions when describe_images=False."""
        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_page.get_text.return_value = "Regular text content"
        mock_page.get_images.return_value = [(1, 0, 0, 0, 0, 0, 0)]
        mock_doc.__iter__ = lambda self: iter([mock_page])
        mock_fitz.open.return_value = mock_doc

        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4")

        result = await extract_text_from_pdf_async(pdf_path, describe_images=False)

        assert "Regular text content" in result
        assert "[Image" not in result
        mock_page.get_images.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.rag.pdf_parser.extract_images_from_page")
    @patch("app.rag.pdf_parser.fitz")
    async def test_describe_images_true_includes_descriptions(
        self, mock_fitz, mock_extract_images, tmp_path,
    ):
        """Image descriptions are included when describe_images=True."""
        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_page.get_text.return_value = "Engine diagnostics text"
        mock_doc.__iter__ = lambda self: iter([mock_page])
        mock_fitz.open.return_value = mock_doc

        mock_extract_images.return_value = [
            {"index": 1, "png_bytes": b"fake-png-data"},
        ]

        mock_vs = AsyncMock()
        mock_vs.describe_image = AsyncMock(
            return_value="Wiring diagram for ECU connector C1."
        )

        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4")

        with patch("app.rag.vision.get_vision_service", return_value=mock_vs):
            result = await extract_text_from_pdf_async(
                pdf_path, describe_images=True,
            )

        assert "[Page 1]" in result
        assert "Engine diagnostics text" in result
        assert "[Image 1, Page 1]" in result
        assert "Wiring diagram for ECU connector C1." in result

    @pytest.mark.asyncio
    @patch("app.rag.pdf_parser.extract_images_from_page")
    @patch("app.rag.pdf_parser.fitz")
    async def test_image_extraction_failure_graceful(
        self, mock_fitz, mock_extract_images, tmp_path,
    ):
        """Vision service errors don't break text extraction."""
        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_page.get_text.return_value = "Page text preserved"
        mock_doc.__iter__ = lambda self: iter([mock_page])
        mock_fitz.open.return_value = mock_doc

        mock_extract_images.return_value = [
            {"index": 1, "png_bytes": b"fake-png-data"},
        ]

        mock_vs = AsyncMock()
        mock_vs.describe_image = AsyncMock(side_effect=RuntimeError("vision error"))

        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF-1.4")

        with patch("app.rag.vision.get_vision_service", return_value=mock_vs):
            result = await extract_text_from_pdf_async(
                pdf_path, describe_images=True,
            )

        assert "Page text preserved" in result
        # Image marker should NOT be present since vision failed
        assert "[Image" not in result

    @pytest.mark.asyncio
    async def test_nonexistent_pdf_raises_error(self, tmp_path):
        """FileNotFoundError is raised for non-existent files."""
        fake_path = tmp_path / "nonexistent.pdf"
        with pytest.raises(FileNotFoundError):
            await extract_text_from_pdf_async(fake_path)

    @pytest.mark.asyncio
    @patch("app.rag.pdf_parser.fitz")
    async def test_empty_pdf_returns_empty_text(self, mock_fitz, tmp_path):
        """PDFs with no text or images return empty string."""
        mock_doc = MagicMock()
        mock_page = MagicMock()
        mock_page.get_text.return_value = "   \n  "
        mock_doc.__iter__ = lambda self: iter([mock_page])
        mock_fitz.open.return_value = mock_doc

        pdf_path = tmp_path / "empty.pdf"
        pdf_path.write_bytes(b"%PDF-1.4")

        result = await extract_text_from_pdf_async(pdf_path, describe_images=False)
        assert result == ""
