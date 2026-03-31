"""Tests for app.rag.md_export — PDF-to-markdown converter."""

import asyncio
from pathlib import Path
from unittest.mock import (
    AsyncMock,
    MagicMock,
    patch,
)

import pytest

from app.rag.md_export import (
    _build_dtc_index,
    _build_frontmatter,
    _detect_language,
    _heading_prefix,
    _sections_to_markdown,
    _slugify,
    _yaml_escape,
    export_pdf_to_markdown,
    main,
)
from app.rag.parser import Section


# ---- helpers ----

def _make_section(
    title: str = "Test Section",
    body: str = "Body text content here.",
    level: int = 1,
    vehicle_model: str = "MWS-150-A",
    dtc_codes: list | None = None,
) -> Section:
    """Create a Section for testing."""
    return Section(
        title=title,
        level=level,
        body=body,
        vehicle_model=vehicle_model,
        dtc_codes=dtc_codes or [],
    )


def _make_markdown(**overrides) -> str:
    """Call _sections_to_markdown with sensible defaults."""
    defaults = {
        "sections": [_make_section()],
        "source_pdf": "Manual.pdf",
        "vehicle_model": "MWS-150-A",
        "language": "en",
        "translated": False,
        "page_count": 10,
        "section_to_page": {0: 0},
        "section_images": {},
        "image_descriptions": {},
        "stem": "Manual",
    }
    defaults.update(overrides)
    sections = defaults.pop("sections")
    return _sections_to_markdown(sections, **defaults)


# ---- TestSlugify ----

class TestSlugify:
    """Tests for the _slugify helper."""

    def test_basic_heading(self):
        """Standard heading produces clean slug."""
        assert _slugify("Chapter 3: Engine") == (
            "chapter-3-engine"
        )

    def test_special_characters_replaced(self):
        """Non-alphanumeric chars become single hyphens."""
        result = _slugify(
            "DTC: P0171 — System Too Lean"
        )
        assert result == "dtc-p0171-system-too-lean"

    def test_leading_trailing_hyphens_stripped(self):
        """Leading and trailing hyphens are removed."""
        assert _slugify("---Hello World---") == (
            "hello-world"
        )

    def test_truncation_at_80_chars(self):
        """Long titles truncate at a hyphen boundary."""
        long_title = "A " + "word " * 30
        slug = _slugify(long_title)
        assert len(slug) <= 80

    def test_empty_string(self):
        """Empty input produces empty output."""
        assert _slugify("") == ""

    def test_multiple_specials_collapsed(self):
        """Runs of special characters collapse to one hyphen."""
        assert _slugify("A!!!B###C") == "a-b-c"

    def test_numbers_preserved(self):
        """Numeric characters are kept in slugs."""
        result = _slugify(
            "3.2 Fuel System Troubleshooting"
        )
        assert result == (
            "3-2-fuel-system-troubleshooting"
        )


# ---- TestYamlEscape ----

class TestYamlEscape:
    """Tests for the _yaml_escape helper."""

    def test_plain_string_unquoted(self):
        """Strings without specials pass through."""
        assert _yaml_escape("Manual.pdf") == "Manual.pdf"

    def test_colon_triggers_quoting(self):
        """Colons in filenames get quoted."""
        result = _yaml_escape("Manual: Part 1.pdf")
        assert result == '"Manual: Part 1.pdf"'

    def test_brackets_trigger_quoting(self):
        """Square brackets get quoted."""
        result = _yaml_escape("Manual [v2].pdf")
        assert result == '"Manual [v2].pdf"'

    def test_embedded_quotes_escaped(self):
        """Double quotes inside value are escaped."""
        result = _yaml_escape('File "test".pdf')
        assert result == '"File \\"test\\".pdf"'


# ---- TestHeadingPrefix ----

class TestHeadingPrefix:
    """Tests for the _heading_prefix helper."""

    def test_level_0_is_h2(self):
        """Root/fallback sections map to ##."""
        assert _heading_prefix(0) == "##"

    def test_level_1_is_h2(self):
        """Chapter level maps to ##."""
        assert _heading_prefix(1) == "##"

    def test_level_2_is_h3(self):
        """Section level maps to ###."""
        assert _heading_prefix(2) == "###"

    def test_level_3_is_h4(self):
        """Subsection level maps to ####."""
        assert _heading_prefix(3) == "####"

    def test_level_4_is_h4(self):
        """Deep levels also map to ####."""
        assert _heading_prefix(4) == "####"


# ---- TestDetectLanguage ----

class TestDetectLanguage:
    """Tests for the _detect_language helper."""

    def test_english_sections(self):
        """English-only content returns 'en'."""
        sections = [_make_section(body="This is English.")]
        assert _detect_language(sections) == "en"

    def test_chinese_sections(self):
        """CJK content returns 'zh-CN'."""
        sections = [
            _make_section(body="这是中文内容测试文本"),
        ]
        assert _detect_language(sections) == "zh-CN"

    def test_empty_sections(self):
        """No sections defaults to 'en'."""
        assert _detect_language([]) == "en"


# ---- TestBuildFrontmatter ----

class TestBuildFrontmatter:
    """Tests for the _build_frontmatter helper."""

    def test_required_fields_present(self):
        """All required fields are in the output."""
        fm = _build_frontmatter(
            source_pdf="M.pdf",
            vehicle_model="MWS-150-A",
            language="en",
            translated=False,
            page_count=10,
            chapter_count=3,
        )
        assert fm.startswith("---\n")
        assert fm.endswith("\n---")
        assert "source_pdf: M.pdf" in fm
        assert "vehicle_model: MWS-150-A" in fm
        assert "page_count: 10" in fm
        assert "section_count: 3" in fm

    def test_translated_included_when_true(self):
        """translated field appears when True."""
        fm = _build_frontmatter(
            source_pdf="M.pdf",
            vehicle_model="X",
            language="en",
            translated=True,
            page_count=1,
            chapter_count=1,
        )
        assert "translated: true" in fm

    def test_translated_omitted_when_false(self):
        """translated field absent when False."""
        fm = _build_frontmatter(
            source_pdf="M.pdf",
            vehicle_model="X",
            language="en",
            translated=False,
            page_count=1,
            chapter_count=1,
        )
        assert "translated:" not in fm

    def test_special_chars_in_source_pdf_quoted(self):
        """Filenames with YAML-special chars are quoted."""
        fm = _build_frontmatter(
            source_pdf="Manual: Part [1].pdf",
            vehicle_model="X",
            language="en",
            translated=False,
            page_count=1,
            chapter_count=1,
        )
        assert (
            'source_pdf: "Manual: Part [1].pdf"' in fm
        )


# ---- TestBuildDtcIndex ----

class TestBuildDtcIndex:
    """Tests for the _build_dtc_index helper."""

    def test_returns_none_when_no_codes(self):
        """No DTC codes → None."""
        result = _build_dtc_index([_make_section()])
        assert result is None

    def test_builds_table(self):
        """DTC codes produce a markdown table."""
        sections = [
            _make_section(
                title="Fuel System",
                dtc_codes=["P0171"],
            ),
        ]
        result = _build_dtc_index(sections)
        assert result is not None
        assert "## Appendix: DTC Index" in result
        assert "P0171" in result
        assert "[Fuel System](#fuel-system)" in result

    def test_deduplicates(self):
        """Same code in two sections appears once."""
        sections = [
            _make_section(
                title="A", dtc_codes=["P0171"],
            ),
            _make_section(
                title="B", dtc_codes=["P0171"],
            ),
        ]
        result = _build_dtc_index(sections)
        lines = [
            ln for ln in result.split("\n")
            if ln.startswith("| P0171")
        ]
        assert len(lines) == 1


# ---- TestSectionsToMarkdown ----

class TestSectionsToMarkdown:
    """Tests for the _sections_to_markdown formatter."""

    def test_yaml_frontmatter_present(self):
        """Output starts with YAML frontmatter block."""
        md = _make_markdown()
        assert md.startswith("---\n")
        assert "source_pdf: Manual.pdf" in md
        assert "vehicle_model: MWS-150-A" in md
        assert "language: en" in md
        assert "page_count: 10" in md
        assert "section_count: 1" in md

    def test_translated_field_when_true(self):
        """Frontmatter includes translated when True."""
        md = _make_markdown(translated=True)
        assert "translated: true" in md

    def test_no_translated_field_when_false(self):
        """Frontmatter omits translated when False."""
        md = _make_markdown(translated=False)
        assert "translated:" not in md

    def test_single_title_heading(self):
        """Exactly one # heading exists."""
        md = _make_markdown()
        lines = md.split("\n")
        h1_lines = [
            ln for ln in lines
            if ln.startswith("# ")
            and not ln.startswith("## ")
        ]
        assert len(h1_lines) == 1

    def test_title_from_vehicle_model(self):
        """Title heading uses vehicle model."""
        md = _make_markdown(
            vehicle_model="MWS-150-A",
        )
        assert "# MWS-150-A Service Manual" in md

    def test_heading_level_1(self):
        """Level-1 sections produce ## headings."""
        sections = [
            _make_section(
                title="Chapter 1", level=1,
            ),
        ]
        md = _make_markdown(sections=sections)
        assert "## Chapter 1" in md

    def test_heading_level_2(self):
        """Level-2 sections produce ### headings."""
        sections = [
            _make_section(
                title="Section 1.1", level=2,
            ),
        ]
        md = _make_markdown(sections=sections)
        assert "### Section 1.1" in md

    def test_heading_level_3(self):
        """Level-3 sections produce #### headings."""
        sections = [
            _make_section(
                title="Sub 1.1.1", level=3,
            ),
        ]
        md = _make_markdown(sections=sections)
        assert "#### Sub 1.1.1" in md

    def test_page_markers_present(self):
        """Page markers appear for mapped sections."""
        sections = [
            _make_section(title="Ch 1", level=1),
            _make_section(title="Ch 2", level=1),
        ]
        md = _make_markdown(
            sections=sections,
            section_to_page={0: 0, 1: 4},
        )
        assert "<!-- page:1 -->" in md
        assert "<!-- page:5 -->" in md

    def test_image_references_formatted(self):
        """Image references use correct markdown syntax."""
        sections = [_make_section()]
        images = {
            0: [{
                "index": 1,
                "page_num": 3,
                "path_relative": "images/M/p003-1.png",
            }],
        }
        md = _make_markdown(
            sections=sections,
            section_images=images,
        )
        assert (
            "![Image 1 from page 3]"
            "(images/M/p003-1.png)" in md
        )

    def test_vision_descriptions_included(self):
        """Vision descriptions appear as italic text."""
        sections = [_make_section()]
        images = {
            0: [{
                "index": 1,
                "page_num": 3,
                "path_relative": "images/M/p003-1.png",
            }],
        }
        descs = {"p003-1": "Exploded view of injector."}
        md = _make_markdown(
            sections=sections,
            section_images=images,
            image_descriptions=descs,
        )
        assert (
            "*Vision description: "
            "Exploded view of injector.*" in md
        )

    def test_no_vision_description_when_empty(self):
        """No vision text when descriptions dict is empty."""
        sections = [_make_section()]
        images = {
            0: [{
                "index": 1,
                "page_num": 3,
                "path_relative": "images/M/p003-1.png",
            }],
        }
        md = _make_markdown(
            sections=sections,
            section_images=images,
            image_descriptions={},
        )
        assert "*Vision description:" not in md

    def test_empty_sections_omitted(self):
        """Sections with empty bodies are skipped."""
        sections = [
            _make_section(title="Empty", body=""),
            _make_section(
                title="Has Content", body="Real body.",
            ),
        ]
        md = _make_markdown(sections=sections)
        assert "## Empty" not in md
        assert "## Has Content" in md

    def test_dtc_index_generated(self):
        """DTC appendix appears when codes exist."""
        sections = [
            _make_section(
                title="Fuel System",
                dtc_codes=["P0171", "P0174"],
            ),
        ]
        md = _make_markdown(sections=sections)
        assert "## Appendix: DTC Index" in md
        assert "P0171" in md
        assert "P0174" in md
        assert "[Fuel System](#fuel-system)" in md

    def test_no_dtc_index_when_no_codes(self):
        """No appendix when no DTC codes exist."""
        md = _make_markdown()
        assert "Appendix" not in md

    def test_dtc_index_deduplicates(self):
        """Duplicate DTC codes across sections appear once."""
        sections = [
            _make_section(
                title="Sec A",
                dtc_codes=["P0171"],
            ),
            _make_section(
                title="Sec B",
                dtc_codes=["P0171"],
            ),
        ]
        md = _make_markdown(sections=sections)
        lines = [
            ln for ln in md.split("\n")
            if ln.startswith("| P0171")
        ]
        assert len(lines) == 1

    def test_level_0_sections_become_h2(self):
        """Level-0 (root/fallback) sections render as ##."""
        sections = [
            _make_section(title="Root", level=0),
        ]
        md = _make_markdown(sections=sections)
        assert "## Root" in md


# ---- TestExportPdfToMarkdown ----

def _mock_fitz_doc(page_count: int = 5):
    """Create a mock fitz document."""
    mock_doc = MagicMock()
    mock_doc.page_count = page_count
    mock_doc.__enter__ = MagicMock(
        return_value=mock_doc,
    )
    mock_doc.__exit__ = MagicMock(return_value=False)

    mock_page = MagicMock()
    mock_page.get_text.return_value = "Sample text"
    mock_doc.__getitem__ = MagicMock(
        return_value=mock_page,
    )
    return mock_doc


class TestExportPdfToMarkdown:
    """Tests for the export_pdf_to_markdown function."""

    @pytest.mark.asyncio
    async def test_nonexistent_pdf_raises(self, tmp_path):
        """Non-existent file raises FileNotFoundError."""
        fake = tmp_path / "nope.pdf"
        with pytest.raises(FileNotFoundError):
            await export_pdf_to_markdown(
                fake, tmp_path / "out",
            )

    @pytest.mark.asyncio
    @patch("app.rag.md_export.extract_pdf_sections_async")
    @patch("app.rag.md_export.extract_images_from_page")
    @patch("app.rag.md_export.build_page_to_section_map")
    @patch("app.rag.md_export.compute_body_font_size")
    @patch("app.rag.md_export.fitz")
    async def test_output_file_created(
        self,
        mock_fitz,
        mock_body_size,
        mock_page_map,
        mock_images,
        mock_sections,
        tmp_path,
    ):
        """Output .md file is created in output_dir."""
        mock_fitz.open.return_value = _mock_fitz_doc()
        mock_body_size.return_value = 10.5
        mock_page_map.return_value = {0: 0, 1: 0}
        mock_images.return_value = []
        mock_sections.return_value = [
            _make_section(
                title="Chapter 1",
                body="Some content here.",
            ),
        ]

        pdf = tmp_path / "Test_Manual.pdf"
        pdf.write_bytes(b"%PDF-1.4 dummy")
        out_dir = tmp_path / "output"

        result = await export_pdf_to_markdown(
            pdf, out_dir,
        )

        assert result.exists()
        assert result.name == "Test_Manual.md"
        content = result.read_text(encoding="utf-8")
        assert content.startswith("---\n")
        assert "Chapter 1" in content

    @pytest.mark.asyncio
    @patch("app.rag.md_export.extract_pdf_sections_async")
    @patch("app.rag.md_export.extract_images_from_page")
    @patch("app.rag.md_export.build_page_to_section_map")
    @patch("app.rag.md_export.compute_body_font_size")
    @patch("app.rag.md_export.fitz")
    async def test_image_files_written(
        self,
        mock_fitz,
        mock_body_size,
        mock_page_map,
        mock_images,
        mock_sections,
        tmp_path,
    ):
        """Extracted images are saved as PNG files."""
        mock_doc = _mock_fitz_doc(page_count=2)
        mock_fitz.open.return_value = mock_doc
        mock_body_size.return_value = 10.5
        mock_page_map.return_value = {0: 0, 1: 0}

        png_data = b"\x89PNG\r\n\x1a\nfakeimage"
        mock_images.return_value = [
            {"index": 1, "png_bytes": png_data},
        ]
        mock_sections.return_value = [
            _make_section(),
        ]

        pdf = tmp_path / "Manual.pdf"
        pdf.write_bytes(b"%PDF-1.4 dummy")
        out_dir = tmp_path / "output"

        await export_pdf_to_markdown(pdf, out_dir)

        img_dir = out_dir / "images" / "Manual"
        assert img_dir.exists()
        img_files = list(img_dir.glob("*.png"))
        assert len(img_files) >= 1
        assert img_files[0].read_bytes() == png_data

    @pytest.mark.asyncio
    @patch("app.rag.md_export.extract_pdf_sections_async")
    @patch("app.rag.md_export.extract_images_from_page")
    @patch("app.rag.md_export.build_page_to_section_map")
    @patch("app.rag.md_export.compute_body_font_size")
    @patch("app.rag.md_export.fitz")
    async def test_ocr_flag_passed_through(
        self,
        mock_fitz,
        mock_body_size,
        mock_page_map,
        mock_images,
        mock_sections,
        tmp_path,
    ):
        """enable_ocr is forwarded to section extraction."""
        mock_fitz.open.return_value = _mock_fitz_doc()
        mock_body_size.return_value = 10.5
        mock_page_map.return_value = {0: 0}
        mock_images.return_value = []
        mock_sections.return_value = [
            _make_section(),
        ]

        pdf = tmp_path / "M.pdf"
        pdf.write_bytes(b"%PDF-1.4 dummy")

        await export_pdf_to_markdown(
            pdf, tmp_path / "out", enable_ocr=True,
        )

        mock_sections.assert_called_once()
        call_kwargs = mock_sections.call_args
        assert call_kwargs.kwargs.get("enable_ocr") is True

    @pytest.mark.asyncio
    @patch(
        "app.rag.md_export.get_translation_service",
        create=True,
    )
    @patch("app.rag.md_export.extract_pdf_sections_async")
    @patch("app.rag.md_export.extract_images_from_page")
    @patch("app.rag.md_export.build_page_to_section_map")
    @patch("app.rag.md_export.compute_body_font_size")
    @patch("app.rag.md_export.fitz")
    async def test_translation_called_when_enabled(
        self,
        mock_fitz,
        mock_body_size,
        mock_page_map,
        mock_images,
        mock_sections,
        mock_get_translator,
        tmp_path,
    ):
        """Translation service is invoked when enabled."""
        mock_fitz.open.return_value = _mock_fitz_doc()
        mock_body_size.return_value = 10.5
        mock_page_map.return_value = {0: 0}
        mock_images.return_value = []

        original = [
            _make_section(body="中文内容需要翻译"),
        ]
        translated = [
            _make_section(
                body="Chinese content needs translation",
            ),
        ]
        mock_sections.return_value = original

        mock_translator = AsyncMock()
        mock_translator.translate_sections.return_value = (
            translated
        )
        mock_get_translator.return_value = mock_translator

        pdf = tmp_path / "M.pdf"
        pdf.write_bytes(b"%PDF-1.4 dummy")

        with patch(
            "app.rag.translator.get_translation_service",
            return_value=mock_translator,
        ):
            await export_pdf_to_markdown(
                pdf,
                tmp_path / "out",
                enable_translation=True,
            )

        mock_translator.translate_sections.assert_called_once()
        mock_translator.close.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.rag.md_export.extract_pdf_sections_async")
    @patch("app.rag.md_export.extract_images_from_page")
    @patch("app.rag.md_export.build_page_to_section_map")
    @patch("app.rag.md_export.compute_body_font_size")
    @patch("app.rag.md_export.fitz")
    async def test_vehicle_model_from_sections(
        self,
        mock_fitz,
        mock_body_size,
        mock_page_map,
        mock_images,
        mock_sections,
        tmp_path,
    ):
        """Vehicle model in frontmatter comes from sections."""
        mock_fitz.open.return_value = _mock_fitz_doc()
        mock_body_size.return_value = 10.5
        mock_page_map.return_value = {0: 0}
        mock_images.return_value = []
        mock_sections.return_value = [
            _make_section(vehicle_model="TRICITY-155"),
        ]

        pdf = tmp_path / "T.pdf"
        pdf.write_bytes(b"%PDF-1.4 dummy")
        out_dir = tmp_path / "out"

        result = await export_pdf_to_markdown(
            pdf, out_dir,
        )
        content = result.read_text(encoding="utf-8")
        assert "vehicle_model: TRICITY-155" in content

    @pytest.mark.asyncio
    @patch("app.rag.md_export.extract_pdf_sections_async")
    @patch("app.rag.md_export.extract_images_from_page")
    @patch("app.rag.md_export.build_page_to_section_map")
    @patch("app.rag.md_export.compute_body_font_size")
    @patch("app.rag.md_export.fitz")
    async def test_section_extraction_error_propagates(
        self,
        mock_fitz,
        mock_body_size,
        mock_page_map,
        mock_images,
        mock_sections,
        tmp_path,
    ):
        """Errors from section extraction propagate."""
        mock_fitz.open.return_value = _mock_fitz_doc()
        mock_body_size.return_value = 10.5
        mock_page_map.return_value = {0: 0}
        mock_images.return_value = []
        mock_sections.side_effect = RuntimeError(
            "parse fail"
        )

        pdf = tmp_path / "M.pdf"
        pdf.write_bytes(b"%PDF-1.4 dummy")

        with pytest.raises(RuntimeError, match="parse"):
            await export_pdf_to_markdown(
                pdf, tmp_path / "out",
            )


# ---- TestMain ----

class TestMain:
    """Tests for the CLI entry point."""

    @pytest.mark.asyncio
    @patch("app.rag.md_export.export_pdf_to_markdown")
    async def test_processes_all_pdfs(
        self, mock_export, tmp_path,
    ):
        """All PDF files in --dir are processed."""
        mock_export.return_value = (
            tmp_path / "out" / "a.md"
        )

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "a.pdf").write_bytes(b"fake")
        (data_dir / "b.pdf").write_bytes(b"fake")
        (data_dir / "readme.txt").write_text("ignore")

        with patch(
            "sys.argv",
            [
                "md_export",
                "--dir", str(data_dir),
                "--output", str(tmp_path / "out"),
            ],
        ):
            await main()

        assert mock_export.call_count == 2

    @pytest.mark.asyncio
    async def test_nonexistent_dir_returns(self, tmp_path):
        """Non-existent --dir logs error and returns."""
        with patch(
            "sys.argv",
            [
                "md_export",
                "--dir", str(tmp_path / "nope"),
                "--output", str(tmp_path / "out"),
            ],
        ):
            await main()

    @pytest.mark.asyncio
    @patch("app.rag.md_export.export_pdf_to_markdown")
    async def test_flags_propagated(
        self, mock_export, tmp_path,
    ):
        """CLI flags are passed to export function."""
        mock_export.return_value = (
            tmp_path / "out" / "a.md"
        )

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "a.pdf").write_bytes(b"fake")

        with patch(
            "sys.argv",
            [
                "md_export",
                "--dir", str(data_dir),
                "--output", str(tmp_path / "out"),
                "--describe-images",
                "--enable-ocr",
                "--enable-translation",
            ],
        ):
            await main()

        call_kwargs = mock_export.call_args
        assert (
            call_kwargs.kwargs["describe_images"] is True
        )
        assert (
            call_kwargs.kwargs["enable_ocr"] is True
        )
        assert (
            call_kwargs.kwargs["enable_translation"]
            is True
        )

    @pytest.mark.asyncio
    @patch("app.rag.md_export.export_pdf_to_markdown")
    async def test_continues_on_single_file_error(
        self, mock_export, tmp_path,
    ):
        """One failing PDF does not stop others."""
        mock_export.side_effect = [
            RuntimeError("fail"),
            tmp_path / "out" / "b.md",
        ]

        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "a.pdf").write_bytes(b"fake")
        (data_dir / "b.pdf").write_bytes(b"fake")

        with patch(
            "sys.argv",
            [
                "md_export",
                "--dir", str(data_dir),
                "--output", str(tmp_path / "out"),
            ],
        ):
            await main()

        assert mock_export.call_count == 2
