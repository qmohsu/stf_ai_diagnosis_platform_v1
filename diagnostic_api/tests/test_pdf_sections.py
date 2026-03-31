"""Unit tests for font-based PDF section extraction.

Tests the ``extract_pdf_sections`` function and its helpers using
mocked PyMuPDF output to simulate real PDF structure.  Also covers
the async variant with OCR and page-render integration.
"""

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.rag.pdf_parser import (
    _classify_line,
    compute_body_font_size,
    _extract_page_lines,
    extract_pdf_sections,
    extract_pdf_sections_async,
)
from app.rag.parser import Section


# ------------------------------------------------------------------
# Helpers to build mock PyMuPDF structures
# ------------------------------------------------------------------

def _make_span(text: str, size: float, bold: bool = False):
    """Create a mock span dict matching fitz text dict format."""
    flags = 16 if bold else 0
    return {"text": text, "size": size, "flags": flags}


def _make_line(*spans):
    """Create a mock line dict containing one or more spans."""
    return {"spans": list(spans)}


def _make_block(*lines, block_type: int = 0):
    """Create a mock text block."""
    return {"type": block_type, "lines": list(lines)}


def _make_page_dict(*blocks):
    """Create a mock page dict."""
    return {"blocks": list(blocks)}


def _mock_page(page_dict: dict, plain_text: str = ""):
    """Build a MagicMock fitz.Page with mode-aware get_text.

    ``get_text("dict")`` returns *page_dict*;
    ``get_text("text")`` (or no arg) returns *plain_text*.
    """
    page = MagicMock()

    def _get_text(mode="text"):
        if mode == "dict":
            return page_dict
        return plain_text

    page.get_text = MagicMock(side_effect=_get_text)
    return page


# ------------------------------------------------------------------
# _classify_line tests
# ------------------------------------------------------------------

class TestClassifyLine:
    """Tests for the line classification helper."""

    def test_body_text(self):
        """Normal-sized text should be classified as body."""
        line = {
            "text": "Normal body.",
            "font_size": 10.5,
            "is_bold": False,
        }
        assert _classify_line(line, body_size=10.5) == "body"

    def test_heading_l1(self):
        """Text >= body * 1.5 should be heading_l1."""
        line = {
            "text": "Chapter Title",
            "font_size": 17.0,
            "is_bold": False,
        }
        assert _classify_line(line, body_size=10.5) == "heading_l1"

    def test_heading_l2(self):
        """Text >= body * 1.25 but < 1.5 should be heading_l2."""
        line = {
            "text": "Section Title",
            "font_size": 14.0,
            "is_bold": False,
        }
        assert _classify_line(line, body_size=10.5) == "heading_l2"

    def test_page_number(self):
        """Page numbers like '3-22' should be classified as page_num."""
        line = {
            "text": "3-22",
            "font_size": 12.0,
            "is_bold": False,
        }
        assert _classify_line(line, body_size=10.5) == "page_num"

    def test_eas_code(self):
        """EAS reference codes should be classified as eas_code."""
        line = {
            "text": "EAS30812",
            "font_size": 5.0,
            "is_bold": False,
        }
        assert _classify_line(line, body_size=10.5) == "eas_code"

    def test_ewa_code(self):
        """EWA codes should also be classified as eas_code."""
        line = {
            "text": "EWA13030",
            "font_size": 5.0,
            "is_bold": False,
        }
        assert _classify_line(line, body_size=10.5) == "eas_code"

    def test_eca_code(self):
        """ECA codes should be classified as eas_code."""
        line = {
            "text": "ECA20500",
            "font_size": 5.0,
            "is_bold": False,
        }
        assert _classify_line(line, body_size=10.5) == "eas_code"

    def test_page_number_single_digit(self):
        """Single-digit page numbers like '1-1' are detected."""
        line = {
            "text": "1-1",
            "font_size": 12.0,
            "is_bold": False,
        }
        assert _classify_line(line, body_size=10.5) == "page_num"

    def test_borderline_not_heading(self):
        """Text just below the L2 threshold stays body."""
        line = {
            "text": "Not a heading",
            "font_size": 12.5,
            "is_bold": False,
        }
        # 12.5 / 10.5 = 1.19, below 1.25 threshold
        assert _classify_line(line, body_size=10.5) == "body"


# ------------------------------------------------------------------
# compute_body_font_size tests
# ------------------------------------------------------------------

class TestComputeBodyFontSize:
    """Tests for body font size detection."""

    def test_mode_is_most_common(self):
        """Body font size should be the most frequent size."""
        mock_doc = MagicMock()
        mock_doc.page_count = 2

        page1_dict = _make_page_dict(
            _make_block(
                _make_line(_make_span("body " * 50, 10.5)),
                _make_line(_make_span("heading", 17.0)),
            )
        )
        page2_dict = _make_page_dict(
            _make_block(
                _make_line(_make_span("more body " * 40, 10.5)),
                _make_line(_make_span("section", 14.0)),
            )
        )

        pages = [MagicMock(), MagicMock()]
        pages[0].get_text.return_value = page1_dict
        pages[1].get_text.return_value = page2_dict
        mock_doc.__getitem__ = MagicMock(
            side_effect=lambda idx: pages[idx],
        )

        result = compute_body_font_size(mock_doc)
        assert result == 10.5

    def test_fallback_on_empty_doc(self):
        """Empty document should return default 10.0."""
        mock_doc = MagicMock()
        mock_doc.page_count = 1
        page = MagicMock()
        page.get_text.return_value = {"blocks": []}
        mock_doc.__getitem__ = MagicMock(
            side_effect=lambda idx: page,
        )

        result = compute_body_font_size(mock_doc)
        assert result == 10.0


# ------------------------------------------------------------------
# extract_pdf_sections integration tests (with mocked fitz)
# ------------------------------------------------------------------

class TestExtractPdfSections:
    """Tests for the full section extraction pipeline."""

    @patch("app.rag.pdf_parser.fitz")
    def test_sections_from_headings(self, mock_fitz, tmp_path):
        """Headings should produce separate sections."""
        body_size = 10.5

        p1 = _mock_page(
            _make_page_dict(_make_block(
                _make_line(_make_span("引擎規格", 17.0)),
                _make_line(
                    _make_span(
                        "燃燒循環 4 行程冷卻系統水冷汽門 SOHC",
                        body_size,
                    ),
                ),
                _make_line(
                    _make_span(
                        "排氣量 155 立方公分單缸引擎",
                        body_size,
                    ),
                ),
            )),
            plain_text=(
                "引擎規格\n"
                "燃燒循環 4 行程冷卻系統水冷汽門 SOHC\n"
                "排氣量 155 立方公分單缸引擎"
            ),
        )
        p2 = _mock_page(
            _make_page_dict(_make_block(
                _make_line(_make_span("機油規格", 14.0)),
                _make_line(
                    _make_span(
                        "推薦品牌 YAMALUBE 黏稠度等級",
                        body_size,
                    ),
                ),
                _make_line(
                    _make_span(
                        "SAE 10W-40 API SG 類型或以上",
                        body_size,
                    ),
                ),
            )),
            plain_text=(
                "機油規格\n"
                "推薦品牌 YAMALUBE 黏稠度等級\n"
                "SAE 10W-40 API SG 類型或以上"
            ),
        )
        p3 = _mock_page(
            _make_page_dict(_make_block(
                _make_line(
                    _make_span(
                        "引擎機油量 0.90 L 容量分解 1.00 L",
                        body_size,
                    ),
                ),
            )),
            plain_text="引擎機油量 0.90 L 容量分解 1.00 L",
        )

        pages = [p1, p2, p3]
        mock_doc = MagicMock()
        mock_doc.page_count = 3
        mock_doc.__getitem__ = MagicMock(
            side_effect=lambda idx: pages[idx],
        )
        mock_fitz.open.return_value = mock_doc

        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF")

        with patch(
            "app.rag.pdf_parser.compute_body_font_size",
            return_value=body_size,
        ):
            sections = extract_pdf_sections(pdf_path)

        assert len(sections) == 2
        assert sections[0].title == "引擎規格"
        assert sections[0].level == 1
        assert "燃燒循環" in sections[0].body
        assert sections[1].title == "機油規格"
        assert sections[1].level == 2
        assert "YAMALUBE" in sections[1].body
        # Body from page 3 should attach to section 2
        assert "0.90 L" in sections[1].body

    @patch("app.rag.pdf_parser.fitz")
    def test_eas_codes_in_body(self, mock_fitz, tmp_path):
        """EAS codes should be included in body text."""
        body_size = 10.5

        page = _mock_page(
            _make_page_dict(_make_block(
                _make_line(_make_span("冷卻系統", 14.0)),
                _make_line(_make_span("EAS30812", 5.0)),
                _make_line(
                    _make_span(
                        "冷卻液更換步驟如下。", body_size,
                    ),
                ),
            )),
            plain_text=(
                "冷卻系統\nEAS30812\n冷卻液更換步驟如下。"
            ),
        )

        mock_doc = MagicMock()
        mock_doc.page_count = 1
        mock_doc.__getitem__ = MagicMock(
            side_effect=lambda idx: page,
        )
        mock_fitz.open.return_value = mock_doc

        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF")

        with patch(
            "app.rag.pdf_parser.compute_body_font_size",
            return_value=body_size,
        ):
            sections = extract_pdf_sections(pdf_path)

        assert len(sections) == 1
        assert "EAS30812" in sections[0].body

    @patch("app.rag.pdf_parser.fitz")
    def test_page_numbers_excluded(self, mock_fitz, tmp_path):
        """Page number lines should not appear in section body."""
        body_size = 10.5

        page = _mock_page(
            _make_page_dict(_make_block(
                _make_line(_make_span("定期保養", 14.0)),
                _make_line(_make_span("3-22", 12.0)),
                _make_line(
                    _make_span(
                        "冷卻系統檢查包含水箱蓋拆卸與冷卻液更換程序",
                        body_size,
                    ),
                ),
            )),
            plain_text=(
                "定期保養\n3-22\n"
                "冷卻系統檢查包含水箱蓋拆卸與冷卻液更換程序"
            ),
        )

        mock_doc = MagicMock()
        mock_doc.page_count = 1
        mock_doc.__getitem__ = MagicMock(
            side_effect=lambda idx: page,
        )
        mock_fitz.open.return_value = mock_doc

        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF")

        with patch(
            "app.rag.pdf_parser.compute_body_font_size",
            return_value=body_size,
        ):
            sections = extract_pdf_sections(pdf_path)

        assert len(sections) == 1
        assert "3-22" not in sections[0].body

    @patch("app.rag.pdf_parser.fitz")
    def test_vehicle_model_from_filename(
        self, mock_fitz, tmp_path,
    ):
        """Vehicle model should be extracted from filename."""
        body_size = 10.5

        page = _mock_page(
            _make_page_dict(_make_block(
                _make_line(
                    _make_span("General info", 14.0),
                ),
                _make_line(
                    _make_span(
                        "Some body text here for content.",
                        body_size,
                    ),
                ),
            )),
            plain_text="General info\nSome body text here for content.",
        )

        mock_doc = MagicMock()
        mock_doc.page_count = 1
        mock_doc.__getitem__ = MagicMock(
            side_effect=lambda idx: page,
        )
        mock_fitz.open.return_value = mock_doc

        pdf_path = tmp_path / "MWS150-A service.pdf"
        pdf_path.write_bytes(b"%PDF")

        with patch(
            "app.rag.pdf_parser.compute_body_font_size",
            return_value=body_size,
        ):
            sections = extract_pdf_sections(pdf_path)

        # Vehicle model from filename should propagate
        assert sections[0].vehicle_model != "Generic"
        assert "MWS" in sections[0].vehicle_model

    @patch("app.rag.pdf_parser.fitz")
    def test_dtc_codes_extracted_per_section(
        self, mock_fitz, tmp_path,
    ):
        """DTC codes should be extracted per section."""
        body_size = 10.5

        p1 = _mock_page(
            _make_page_dict(_make_block(
                _make_line(
                    _make_span("Fuel System", 14.0),
                ),
                _make_line(
                    _make_span(
                        "Diagnostic trouble code P0171 detected in the fuel system.",
                        body_size,
                    ),
                ),
            )),
            plain_text=(
                "Fuel System\n"
                "Diagnostic trouble code P0171 detected in the fuel system."
            ),
        )
        p2 = _mock_page(
            _make_page_dict(_make_block(
                _make_line(
                    _make_span("Ignition", 14.0),
                ),
                _make_line(
                    _make_span(
                        "Code P0300 random multiple cylinder misfire detected.",
                        body_size,
                    ),
                ),
            )),
            plain_text=(
                "Ignition\n"
                "Code P0300 random multiple cylinder misfire detected."
            ),
        )

        pages = [p1, p2]
        mock_doc = MagicMock()
        mock_doc.page_count = 2
        mock_doc.__getitem__ = MagicMock(
            side_effect=lambda idx: pages[idx],
        )
        mock_fitz.open.return_value = mock_doc

        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF")

        with patch(
            "app.rag.pdf_parser.compute_body_font_size",
            return_value=body_size,
        ):
            sections = extract_pdf_sections(pdf_path)

        assert len(sections) == 2
        assert "P0171" in sections[0].dtc_codes
        assert "P0300" not in sections[0].dtc_codes
        assert "P0300" in sections[1].dtc_codes

    @patch("app.rag.pdf_parser.fitz")
    def test_fallback_page_level_sections(
        self, mock_fitz, tmp_path,
    ):
        """When no headings are detected, fall back to page-level."""
        body_size = 10.5
        text_content = "All text is the same size. " * 5

        page = _mock_page(
            _make_page_dict(_make_block(
                _make_line(
                    _make_span(text_content, body_size),
                ),
            )),
            plain_text=text_content,
        )

        mock_doc = MagicMock()
        mock_doc.page_count = 1
        mock_doc.__getitem__ = MagicMock(
            side_effect=lambda idx: page,
        )
        mock_fitz.open.return_value = mock_doc

        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF")

        with patch(
            "app.rag.pdf_parser.compute_body_font_size",
            return_value=body_size,
        ):
            sections = extract_pdf_sections(pdf_path)

        assert len(sections) >= 1

    def test_nonexistent_pdf_raises_error(self, tmp_path):
        """FileNotFoundError for non-existent files."""
        fake = tmp_path / "no_such_file.pdf"
        with pytest.raises(FileNotFoundError):
            extract_pdf_sections(fake)


# ------------------------------------------------------------------
# extract_pdf_sections_async OCR / page-render integration tests
# ------------------------------------------------------------------

class TestExtractPdfSectionsAsyncOCR:
    """Tests for OCR and page-render integration in async extraction."""

    def _make_sections(self) -> list[Section]:
        """Create a single test section."""
        return [
            Section(
                title="Test Heading",
                level=1,
                body="Body text here.",
                vehicle_model="MWS150-A",
                dtc_codes=[],
            ),
        ]

    def _mock_doc_one_page_with_images(
        self, mock_fitz, body_size=10.5,
    ):
        """Build a mock document with one page containing images."""
        mock_doc = MagicMock()
        mock_doc.page_count = 1

        page = MagicMock()
        page.get_text.return_value = "some page text"
        mock_doc.__getitem__ = MagicMock(
            side_effect=lambda idx: page,
        )
        mock_fitz.open.return_value = mock_doc
        return mock_doc, page

    @pytest.mark.asyncio
    @patch("app.rag.pdf_parser.fitz")
    @patch("app.rag.pdf_parser.extract_pdf_sections")
    @patch("app.rag.pdf_parser.extract_images_from_page")
    async def test_ocr_appends_block(
        self,
        mock_extract_images,
        mock_extract_sections,
        mock_fitz,
        tmp_path,
    ):
        """OCR results should appear as [OCR, Page N] block."""
        sections = self._make_sections()
        mock_extract_sections.return_value = sections

        mock_extract_images.return_value = [
            {"index": 1, "png_bytes": b"fake-png"},
        ]

        mock_doc, page = self._mock_doc_one_page_with_images(
            mock_fitz,
        )

        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF")

        mock_ocr = MagicMock(return_value={
            "raw_texts": ["90890-03180", "75 N·m"],
            "part_numbers": ["90890-03180"],
            "torque_values": ["75 N·m"],
            "dimensions": [],
            "full_text": "90890-03180 75 N·m",
        })
        mock_overlap = MagicMock(return_value=False)

        with (
            patch(
                "app.rag.pdf_parser.compute_body_font_size",
                return_value=10.5,
            ),
            patch(
                "app.rag.pdf_parser._extract_page_lines",
                return_value=[{
                    "text": "Test Heading",
                    "font_size": 17.0,
                    "is_bold": False,
                }],
            ),
            patch(
                "app.rag.ocr.ocr_extract_structured",
                mock_ocr,
            ),
            patch(
                "app.rag.ocr.compute_text_overlap",
                mock_overlap,
            ),
        ):
            result = await extract_pdf_sections_async(
                pdf_path,
                enable_ocr=True,
            )

        assert len(result) == 1
        body = result[0].body
        assert "[OCR, Page 1]" in body
        assert "Part numbers: 90890-03180" in body
        assert "Torque: 75 N·m" in body

    @pytest.mark.asyncio
    @patch("app.rag.pdf_parser.fitz")
    @patch("app.rag.pdf_parser.extract_pdf_sections")
    @patch("app.rag.pdf_parser.extract_images_from_page")
    async def test_ocr_skipped_when_redundant(
        self,
        mock_extract_images,
        mock_extract_sections,
        mock_fitz,
        tmp_path,
    ):
        """Redundant OCR text should not be appended."""
        sections = self._make_sections()
        mock_extract_sections.return_value = sections

        mock_extract_images.return_value = [
            {"index": 1, "png_bytes": b"fake-png"},
        ]

        mock_doc, page = self._mock_doc_one_page_with_images(
            mock_fitz,
        )

        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF")

        mock_ocr = MagicMock(return_value={
            "raw_texts": ["same text"],
            "part_numbers": [],
            "torque_values": [],
            "dimensions": [],
            "full_text": "same text",
        })
        # overlap_func returns True → redundant
        mock_overlap = MagicMock(return_value=True)

        with (
            patch(
                "app.rag.pdf_parser.compute_body_font_size",
                return_value=10.5,
            ),
            patch(
                "app.rag.pdf_parser._extract_page_lines",
                return_value=[{
                    "text": "Test Heading",
                    "font_size": 17.0,
                    "is_bold": False,
                }],
            ),
            patch(
                "app.rag.ocr.ocr_extract_structured",
                mock_ocr,
            ),
            patch(
                "app.rag.ocr.compute_text_overlap",
                mock_overlap,
            ),
        ):
            result = await extract_pdf_sections_async(
                pdf_path,
                enable_ocr=True,
            )

        # Body should be unchanged (no OCR block appended)
        assert "[OCR, Page" not in result[0].body

    @pytest.mark.asyncio
    @patch("app.rag.pdf_parser.fitz")
    @patch("app.rag.pdf_parser.extract_pdf_sections")
    @patch("app.rag.pdf_parser.extract_images_from_page")
    async def test_no_flags_returns_plain_sections(
        self,
        mock_extract_images,
        mock_extract_sections,
        mock_fitz,
        tmp_path,
    ):
        """No enrichment flags → plain sections unchanged."""
        sections = self._make_sections()
        mock_extract_sections.return_value = sections

        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF")

        result = await extract_pdf_sections_async(pdf_path)

        assert result == sections
        mock_extract_images.assert_not_called()

    @pytest.mark.asyncio
    @patch("app.rag.pdf_parser.render_page_image")
    @patch("app.rag.pdf_parser.fitz")
    @patch("app.rag.pdf_parser.extract_pdf_sections")
    @patch("app.rag.pdf_parser.extract_images_from_page")
    async def test_page_render_with_vision(
        self,
        mock_extract_images,
        mock_extract_sections,
        mock_fitz,
        mock_render,
        tmp_path,
    ):
        """Full-page render + vision appends [Full Page] block."""
        sections = self._make_sections()
        mock_extract_sections.return_value = sections

        mock_extract_images.return_value = [
            {"index": 1, "png_bytes": b"fake-png"},
        ]

        mock_doc, page = self._mock_doc_one_page_with_images(
            mock_fitz,
        )
        mock_render.return_value = b"\x89PNG-fullpage"

        mock_vs = AsyncMock()
        mock_vs.describe_image = AsyncMock(
            side_effect=[
                "Individual image desc",  # per-image call
                "Full page diagram desc",  # full-page call
            ],
        )

        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF")

        with (
            patch(
                "app.rag.pdf_parser.compute_body_font_size",
                return_value=10.5,
            ),
            patch(
                "app.rag.pdf_parser._extract_page_lines",
                return_value=[{
                    "text": "Test Heading",
                    "font_size": 17.0,
                    "is_bold": False,
                }],
            ),
            patch(
                "app.rag.vision.get_vision_service",
                return_value=mock_vs,
            ),
        ):
            result = await extract_pdf_sections_async(
                pdf_path,
                describe_images=True,
                enable_page_render=True,
            )

        body = result[0].body
        assert "[Image 1, Page 1]" in body
        assert "[Full Page, Page 1]" in body
        assert "Full page diagram desc" in body

    @pytest.mark.asyncio
    @patch("app.rag.pdf_parser.fitz")
    @patch("app.rag.pdf_parser.extract_pdf_sections")
    @patch("app.rag.pdf_parser.extract_images_from_page")
    async def test_ocr_error_graceful(
        self,
        mock_extract_images,
        mock_extract_sections,
        mock_fitz,
        tmp_path,
    ):
        """OCR exceptions are caught and don't break extraction."""
        sections = self._make_sections()
        mock_extract_sections.return_value = sections

        mock_extract_images.return_value = [
            {"index": 1, "png_bytes": b"fake-png"},
        ]

        mock_doc, page = self._mock_doc_one_page_with_images(
            mock_fitz,
        )

        pdf_path = tmp_path / "test.pdf"
        pdf_path.write_bytes(b"%PDF")

        mock_ocr = MagicMock(
            side_effect=RuntimeError("OCR crashed"),
        )
        mock_overlap = MagicMock(return_value=False)

        with (
            patch(
                "app.rag.pdf_parser.compute_body_font_size",
                return_value=10.5,
            ),
            patch(
                "app.rag.pdf_parser._extract_page_lines",
                return_value=[{
                    "text": "Test Heading",
                    "font_size": 17.0,
                    "is_bold": False,
                }],
            ),
            patch(
                "app.rag.ocr.ocr_extract_structured",
                mock_ocr,
            ),
            patch(
                "app.rag.ocr.compute_text_overlap",
                mock_overlap,
            ),
        ):
            result = await extract_pdf_sections_async(
                pdf_path,
                enable_ocr=True,
            )

        # Should return sections unmodified (no crash)
        assert len(result) == 1
        assert "[OCR, Page" not in result[0].body
