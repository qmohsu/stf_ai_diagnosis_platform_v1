"""Unit tests for PDF parser module.

TODOs from code review:
- TODO(9): Add tests for large PDF performance (50MB+) with pytest.mark.slow
- TODO(9): Add tests for password-protected PDF handling
- TODO(9): Add tests for corrupt PDF handling
- TODO(9): Add tests for memory exhaustion scenarios
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.rag.pdf_parser import extract_text_from_pdf, parse_pdf
from app.rag.parser import Section


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


class TestParsePdf:
    """Tests for parse_pdf function."""

    @patch("app.rag.pdf_parser.extract_text_from_pdf")
    def test_parse_pdf_returns_sections(self, mock_extract):
        """Test that parse_pdf returns a list of Section objects."""
        mock_extract.return_value = "## Introduction\n\nThis is content."

        result = parse_pdf(Path("test.pdf"))

        assert isinstance(result, list)
        assert len(result) > 0
        assert all(isinstance(s, Section) for s in result)

    @patch("app.rag.pdf_parser.extract_text_from_pdf")
    def test_parse_pdf_extracts_dtc_codes(self, mock_extract):
        """Test that DTC codes are extracted from PDF content."""
        mock_extract.return_value = "## Engine Diagnostics\n\nError code P0420 detected."

        result = parse_pdf(Path("test.pdf"))

        dtc_codes = []
        for section in result:
            dtc_codes.extend(section.dtc_codes)
        assert "P0420" in dtc_codes

    @patch("app.rag.pdf_parser.extract_text_from_pdf")
    def test_parse_pdf_extracts_vehicle_model(self, mock_extract):
        """Test that vehicle model is extracted from PDF content."""
        mock_extract.return_value = "# STF-1234 Manual\n\nVehicle specifications."

        result = parse_pdf(Path("test.pdf"))

        vehicle_models = [s.vehicle_model for s in result]
        assert "STF-1234" in vehicle_models

    @patch("app.rag.pdf_parser.extract_text_from_pdf")
    def test_parse_pdf_handles_no_headings(self, mock_extract):
        """Test that PDFs without markdown headings still produce sections."""
        mock_extract.return_value = "Plain text content without any headings."

        result = parse_pdf(Path("document.pdf"))

        assert len(result) == 1
        assert result[0].body == "Plain text content without any headings."

    @patch("app.rag.pdf_parser.extract_text_from_pdf")
    def test_parse_pdf_uses_filename_for_title(self, mock_extract):
        """Test that filename is used as fallback title when no headings."""
        mock_extract.return_value = "Content without headings"

        result = parse_pdf(Path("my_document.pdf"))

        assert result[0].title == "my_document"
