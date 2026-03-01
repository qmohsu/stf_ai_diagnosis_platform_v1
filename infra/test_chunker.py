"""Tests for app.rag.chunker — section-aware chunking with CJK + image support."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "diagnostic_api"))

from app.rag.parser import Section
from app.rag.chunker import (
    Chunker,
    ChunkedSection,
    SENTENCE_SPLIT,
    _has_cjk,
    _normalize_paragraphs,
    _merge_image_blocks,
    _IMAGE_MARKER,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_section(body: str, title: str = "Test", vehicle_model: str = "STF-850", dtc_codes=None):
    return Section(
        title=title,
        level=2,
        body=body,
        vehicle_model=vehicle_model,
        dtc_codes=dtc_codes or [],
    )


# ---------------------------------------------------------------------------
# Section boundary tests
# ---------------------------------------------------------------------------

class TestSectionBoundaries:
    def test_separate_sections_produce_separate_chunks(self):
        """Chunks from section A should never merge with section B."""
        s1 = _make_section("Short body A.", title="Section A")
        s2 = _make_section("Short body B.", title="Section B")
        chunker = Chunker(chunk_size=500, overlap=0)
        chunks = chunker.chunk_sections([s1, s2])
        assert len(chunks) == 2
        assert chunks[0].section_title == "Section A"
        assert chunks[1].section_title == "Section B"

    def test_single_section_fits_in_one_chunk(self):
        s = _make_section("A short paragraph.", title="One")
        chunker = Chunker(chunk_size=500)
        chunks = chunker.chunk_sections([s])
        assert len(chunks) == 1
        assert chunks[0].text == "A short paragraph."


# ---------------------------------------------------------------------------
# Metadata passthrough
# ---------------------------------------------------------------------------

class TestMetadataPassthrough:
    def test_vehicle_model_preserved(self):
        s = _make_section("Some text.", vehicle_model="STF-1234")
        chunks = Chunker(chunk_size=500).chunk_sections([s])
        assert all(c.vehicle_model == "STF-1234" for c in chunks)

    def test_dtc_codes_preserved(self):
        s = _make_section("Code P0171 discussion.", dtc_codes=["P0171"])
        chunks = Chunker(chunk_size=500).chunk_sections([s])
        assert all("P0171" in c.dtc_codes for c in chunks)

    def test_section_title_preserved(self):
        s = _make_section("Content.", title="Fuel System")
        chunks = Chunker(chunk_size=500).chunk_sections([s])
        assert all(c.section_title == "Fuel System" for c in chunks)

    def test_chunk_index_sequential(self):
        s1 = _make_section("A" * 300, title="A")
        s2 = _make_section("B" * 300, title="B")
        chunks = Chunker(chunk_size=500).chunk_sections([s1, s2])
        indices = [c.chunk_index for c in chunks]
        assert indices == list(range(len(chunks)))


# ---------------------------------------------------------------------------
# Paragraph splitting
# ---------------------------------------------------------------------------

class TestParagraphSplitting:
    def test_paragraphs_kept_together_when_under_limit(self):
        body = "First paragraph.\n\nSecond paragraph."
        s = _make_section(body)
        chunks = Chunker(chunk_size=500).chunk_sections([s])
        assert len(chunks) == 1
        assert "First paragraph." in chunks[0].text
        assert "Second paragraph." in chunks[0].text

    def test_paragraphs_split_when_exceeding_limit(self):
        p1 = "A" * 100
        p2 = "B" * 100
        body = f"{p1}\n\n{p2}"
        # chunk_size=120 means both can't fit (100 + 2 + 100 = 202 > 120)
        chunks = Chunker(chunk_size=120, overlap=0).chunk_sections([_make_section(body)])
        assert len(chunks) == 2

    def test_empty_body_no_chunks(self):
        s = _make_section("")
        chunks = Chunker(chunk_size=500).chunk_sections([s])
        assert len(chunks) == 0


# ---------------------------------------------------------------------------
# Overlap
# ---------------------------------------------------------------------------

class TestOverlap:
    def test_overlap_produces_shared_text(self):
        """With overlap > 0, consecutive chunks should share some trailing words."""
        words = " ".join(f"word{i}" for i in range(100))
        s = _make_section(words)
        chunks = Chunker(chunk_size=200, overlap=40).chunk_sections([s])
        assert len(chunks) >= 2
        # The end of chunk[0] should overlap with the start of chunk[1]
        tail_words = set(chunks[0].text.split()[-3:])
        head_words = set(chunks[1].text.split()[:5])
        assert len(tail_words & head_words) > 0, "Expected some overlap between consecutive chunks"

    def test_zero_overlap(self):
        words = " ".join(f"w{i}" for i in range(80))
        s = _make_section(words)
        chunks_no = Chunker(chunk_size=200, overlap=0).chunk_sections([s])
        chunks_yes = Chunker(chunk_size=200, overlap=50).chunk_sections([s])
        # With overlap there may be more chunks or at least shared text
        assert len(chunks_no) >= 1
        assert len(chunks_yes) >= 1


# ---------------------------------------------------------------------------
# Large document
# ---------------------------------------------------------------------------

class TestLargeDocument:
    def test_all_text_covered(self):
        """Every word in the original body should appear in at least one chunk."""
        body = " ".join(f"token{i}" for i in range(200))
        s = _make_section(body)
        chunks = Chunker(chunk_size=300, overlap=30).chunk_sections([s])
        all_chunk_text = " ".join(c.text for c in chunks)
        for i in range(200):
            assert f"token{i}" in all_chunk_text

    def test_no_chunk_exceeds_limit_significantly(self):
        """Chunks should not exceed chunk_size by more than one word."""
        body = " ".join(f"word{i}" for i in range(300))
        s = _make_section(body)
        chunk_size = 250
        chunks = Chunker(chunk_size=chunk_size, overlap=30).chunk_sections([s])
        for c in chunks:
            # Allow a small tolerance (one extra word)
            assert len(c.text) <= chunk_size + 20, f"Chunk too large: {len(c.text)} chars"


# ---------------------------------------------------------------------------
# CJK support tests
# ---------------------------------------------------------------------------

class TestCJKDetection:
    def test_has_cjk_true(self):
        """Chinese text should be detected as CJK."""
        assert _has_cjk("這是中文測試")
        assert _has_cjk("Mixed English and 中文")

    def test_has_cjk_false(self):
        """Pure ASCII text should not be detected as CJK."""
        assert not _has_cjk("This is English text.")
        assert not _has_cjk("")


class TestCJKSentenceSplitting:
    def test_chinese_sentence_split(self):
        """Sentences ending with 。should be split."""
        text = "第一句話。第二句話。第三句話。"
        parts = SENTENCE_SPLIT.split(text)
        assert len(parts) >= 3
        assert parts[0] == "第一句話。"

    def test_mixed_punctuation(self):
        """Both English and Chinese sentence endings should split."""
        text = "English sentence. 中文句子。Another one!"
        parts = SENTENCE_SPLIT.split(text)
        assert len(parts) >= 3

    def test_chinese_exclamation_split(self):
        """Chinese exclamation ！ should trigger a split."""
        text = "危險！請勿觸摸。"
        parts = SENTENCE_SPLIT.split(text)
        assert len(parts) >= 2

    def test_chinese_question_split(self):
        """Chinese question ？ should trigger a split."""
        text = "是否正確？請確認。"
        parts = SENTENCE_SPLIT.split(text)
        assert len(parts) >= 2


class TestCJKParagraphNormalization:
    def test_cjk_paragraph_detection(self):
        """Lines ending with 。followed by \\n should become paragraphs."""
        text = "第一段文字說明。\n第二段文字說明。\n第三段。"
        normalized = _normalize_paragraphs(text)
        # Should have double newlines after sentence-ending lines
        assert "\n\n" in normalized

    def test_already_formatted_text_unchanged(self):
        """Text with existing paragraph structure stays unchanged."""
        text = "Para one.\n\nPara two.\n\nPara three."
        normalized = _normalize_paragraphs(text)
        assert normalized == text

    def test_english_text_unchanged(self):
        """Pure English lines without CJK endings stay unchanged."""
        text = "Line one continues\nLine two continues\nLine three."
        normalized = _normalize_paragraphs(text)
        # English lines don't end with CJK punct, so no change
        assert normalized.count("\n\n") == 0


class TestCJKChunking:
    def test_chinese_text_produces_multiple_chunks(self):
        """Long Chinese text should be split into multiple chunks."""
        # ~800 chars of Chinese (well above 500 chunk_size)
        body = "這是測試文字。" * 100
        s = _make_section(body)
        chunks = Chunker(chunk_size=200, overlap=20).chunk_sections([s])
        assert len(chunks) >= 3

    def test_chinese_chunks_not_empty(self):
        """All CJK chunks should have non-empty text."""
        body = "引擎規格如下。汽缸數為單缸。壓縮比為十。" * 20
        s = _make_section(body)
        chunks = Chunker(chunk_size=100, overlap=10).chunk_sections([s])
        for c in chunks:
            assert len(c.text.strip()) > 0

    def test_chinese_chunks_size_reasonable(self):
        """CJK chunks should respect chunk_size limits."""
        body = "定期保養項目包含機油更換和濾清器檢查。" * 30
        s = _make_section(body)
        chunk_size = 200
        chunks = Chunker(
            chunk_size=chunk_size, overlap=20,
        ).chunk_sections([s])
        for c in chunks:
            # Allow some tolerance for CJK splitting
            assert len(c.text) <= chunk_size + 50, (
                f"Chunk too large: {len(c.text)} chars"
            )

    def test_all_chinese_text_covered(self):
        """Every character should appear in at least one chunk."""
        sentences = [f"句子{i}結尾。" for i in range(50)]
        body = "".join(sentences)
        s = _make_section(body)
        chunks = Chunker(
            chunk_size=100, overlap=10,
        ).chunk_sections([s])
        all_text = "".join(c.text for c in chunks)
        # Every original sentence should appear
        for i in range(50):
            assert f"句子{i}" in all_text

    def test_mixed_cjk_english_chunking(self):
        """Mixed CJK + English text should chunk without errors."""
        body = (
            "Engine oil: SAE 10W-40。"
            "引擎機油推薦品牌為YAMALUBE。"
            "壓縮比 10.5:1。"
            "Compression ratio is standard。"
        ) * 10
        s = _make_section(body)
        chunks = Chunker(
            chunk_size=200, overlap=20,
        ).chunk_sections([s])
        assert len(chunks) >= 1
        for c in chunks:
            assert len(c.text.strip()) > 0


# ---------------------------------------------------------------------------
# Image-marker detection
# ---------------------------------------------------------------------------

class TestImageMarkerRegex:
    """Tests for _IMAGE_MARKER regex pattern."""

    def test_matches_image_marker(self):
        """Should match [Image N, Page M] format."""
        assert _IMAGE_MARKER.search("[Image 1, Page 5]")
        assert _IMAGE_MARKER.search("[Image 12, Page 100]")

    def test_matches_ocr_marker(self):
        """Should match [OCR, Page M] format."""
        assert _IMAGE_MARKER.search("[OCR, Page 3]")

    def test_matches_full_page_marker(self):
        """Should match [Full Page, Page M] format."""
        assert _IMAGE_MARKER.search("[Full Page, Page 42]")

    def test_no_false_positive_on_regular_text(self):
        """Regular text should not match."""
        assert not _IMAGE_MARKER.search("Regular paragraph.")
        assert not _IMAGE_MARKER.search("[Page 5]")
        assert not _IMAGE_MARKER.search("[Section 1]")


# ---------------------------------------------------------------------------
# Image-block merging
# ---------------------------------------------------------------------------

class TestMergeImageBlocks:
    """Tests for _merge_image_blocks helper."""

    def test_no_markers_unchanged(self):
        """Paragraphs without markers are returned unchanged."""
        paras = ["Para one.", "Para two.", "Para three."]
        result = _merge_image_blocks(paras)
        assert result == paras

    def test_marker_merged_with_description(self):
        """Marker paragraph should merge with following description."""
        paras = [
            "Body text before.",
            "[Image 1, Page 5]\nDescription: A wiring diagram.",
            "The connector has 4 pins.",
            "Body text after.",
        ]
        result = _merge_image_blocks(paras)
        assert len(result) == 3
        assert "[Image 1, Page 5]" in result[1]
        assert "The connector has 4 pins." in result[1]
        assert result[0] == "Body text before."
        assert result[2] == "Body text after."

    def test_consecutive_markers_separate(self):
        """Consecutive markers should each be separate blocks."""
        paras = [
            "[Image 1, Page 3]\nDescription: Diagram A.",
            "[Image 2, Page 3]\nDescription: Diagram B.",
        ]
        result = _merge_image_blocks(paras)
        assert len(result) == 2
        assert "[Image 1, Page 3]" in result[0]
        assert "[Image 2, Page 3]" in result[1]

    def test_ocr_marker_merged(self):
        """OCR markers should also be merged with descriptions."""
        paras = [
            "[OCR, Page 7]\nPart numbers: 90890-03180",
            "Torque: 75 N·m",
            "Body text follows.",
        ]
        result = _merge_image_blocks(paras)
        assert len(result) == 2
        assert "[OCR, Page 7]" in result[0]
        assert "Torque: 75 N·m" in result[0]
        assert result[1] == "Body text follows."

    def test_empty_list(self):
        """Empty paragraph list returns empty."""
        assert _merge_image_blocks([]) == []


# ---------------------------------------------------------------------------
# Image-aware chunking
# ---------------------------------------------------------------------------

class TestImageAwareChunking:
    """Tests for image-block-aware chunking behaviour."""

    def test_has_image_set_true(self):
        """Chunks containing image markers have has_image=True."""
        body = (
            "Regular body text.\n\n"
            "[Image 1, Page 5]\n"
            "Description: Wiring diagram for ECU."
        )
        s = _make_section(body)
        chunks = Chunker(chunk_size=500).chunk_sections([s])
        # All text fits in one chunk
        assert len(chunks) == 1
        assert chunks[0].has_image is True

    def test_has_image_set_false(self):
        """Chunks without image markers have has_image=False."""
        body = "Regular body text. No images here."
        s = _make_section(body)
        chunks = Chunker(chunk_size=500).chunk_sections([s])
        assert len(chunks) == 1
        assert chunks[0].has_image is False

    def test_image_block_not_split(self):
        """Image description should not be split from its marker."""
        desc = "X" * 200
        body = (
            "Short intro.\n\n"
            f"[Image 1, Page 3]\nDescription: {desc}"
        )
        s = _make_section(body)
        # chunk_size smaller than marker+desc but we should
        # keep the image block atomic
        chunks = Chunker(
            chunk_size=100, overlap=10,
        ).chunk_sections([s])
        # The image block should be in a single chunk
        image_chunks = [
            c for c in chunks if c.has_image
        ]
        assert len(image_chunks) >= 1
        # The marker and its description must be together
        for ic in image_chunks:
            if "[Image 1, Page 3]" in ic.text:
                assert desc[:50] in ic.text

    def test_ocr_block_stays_atomic(self):
        """OCR blocks should not be split."""
        body = (
            "Some text.\n\n"
            "[OCR, Page 12]\n"
            "Part numbers: 90890-03180, 90890-03181\n"
            "Torque: 75 N·m, 22 N·m\n"
            "Dimensions: ø38 mm"
        )
        s = _make_section(body)
        chunks = Chunker(
            chunk_size=500, overlap=20,
        ).chunk_sections([s])
        # All fits in one chunk
        ocr_chunks = [
            c for c in chunks if "[OCR, Page 12]" in c.text
        ]
        assert len(ocr_chunks) == 1
        assert "90890-03180" in ocr_chunks[0].text
        assert "ø38 mm" in ocr_chunks[0].text

    def test_mixed_text_and_images(self):
        """Body with regular text + images produces correct has_image."""
        long_body = "Regular paragraph. " * 30  # ~600 chars
        body = (
            long_body + "\n\n"
            "[Image 1, Page 2]\n"
            "Description: Engine diagram."
        )
        s = _make_section(body)
        chunks = Chunker(
            chunk_size=300, overlap=20,
        ).chunk_sections([s])
        # Should have at least 2 chunks
        assert len(chunks) >= 2
        # At least one chunk with image, at least one without
        image_count = sum(
            1 for c in chunks if c.has_image
        )
        no_image_count = sum(
            1 for c in chunks if not c.has_image
        )
        assert image_count >= 1
        assert no_image_count >= 1
