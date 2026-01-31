"""Tests for app.rag.chunker — markdown-aware chunking."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "diagnostic_api"))

from app.rag.parser import Section
from app.rag.chunker import Chunker, ChunkedSection


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
