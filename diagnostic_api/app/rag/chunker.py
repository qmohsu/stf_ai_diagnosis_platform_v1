"""Markdown-aware text chunker.

Splits text within section boundaries using a paragraph -> sentence -> word
hierarchy.  Never splits mid-paragraph when the paragraph fits within
chunk_size.
"""

import re
from typing import List

from pydantic import BaseModel

from app.rag.parser import Section

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


class ChunkedSection(BaseModel):
    """A chunk produced from a parsed Section, carrying full metadata."""

    text: str
    section_title: str
    vehicle_model: str
    dtc_codes: List[str] = []
    chunk_index: int = 0


class Chunker:
    """Section-aware text chunker.

    Splits within section boundaries using the hierarchy:
      paragraphs -> sentences -> words
    Overlap is character-based and word-boundary aligned.
    """

    def __init__(self, chunk_size: int = 500, overlap: int = 50):
        self.chunk_size = chunk_size
        self.overlap = overlap

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chunk_sections(self, sections: List[Section]) -> List[ChunkedSection]:
        """Chunk a list of parsed Sections, preserving metadata."""
        results: List[ChunkedSection] = []
        global_idx = 0

        for section in sections:
            raw_chunks = self._split_section(section.body)
            for raw in raw_chunks:
                results.append(
                    ChunkedSection(
                        text=raw,
                        section_title=section.title,
                        vehicle_model=section.vehicle_model,
                        dtc_codes=section.dtc_codes,
                        chunk_index=global_idx,
                    )
                )
                global_idx += 1

        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _split_section(self, text: str) -> List[str]:
        """Split a section body into chunks respecting paragraph boundaries."""
        text = text.strip()
        if not text:
            return []

        paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]

        chunks: List[str] = []
        current_parts: List[str] = []
        current_len = 0

        for para in paragraphs:
            para_len = len(para)

            if para_len > self.chunk_size:
                # Flush anything accumulated so far
                if current_parts:
                    chunks.append("\n\n".join(current_parts))
                    current_parts = []
                    current_len = 0

                # Split the oversized paragraph by sentences, then words
                chunks.extend(self._split_large_paragraph(para))
                continue

            # Would adding this paragraph exceed the limit?
            sep_len = 2 if current_parts else 0  # "\n\n"
            if current_len + sep_len + para_len > self.chunk_size and current_parts:
                chunks.append("\n\n".join(current_parts))
                # Overlap: take trailing text from the last part
                overlap_text = self._overlap_tail(current_parts[-1])
                if overlap_text:
                    current_parts = [overlap_text]
                    current_len = len(overlap_text)
                else:
                    current_parts = []
                    current_len = 0

            current_parts.append(para)
            current_len += sep_len + para_len

        if current_parts:
            chunks.append("\n\n".join(current_parts))

        return chunks

    def _split_large_paragraph(self, para: str) -> List[str]:
        """Split an oversized paragraph by sentences, then by words."""
        sentences = SENTENCE_SPLIT.split(para)
        chunks: List[str] = []
        current: List[str] = []
        current_len = 0

        for sent in sentences:
            sent_len = len(sent)

            if sent_len > self.chunk_size:
                # Flush current
                if current:
                    chunks.append(" ".join(current))
                    current = []
                    current_len = 0
                # Word-level split
                chunks.extend(self._split_by_words(sent))
                continue

            sep_len = 1 if current else 0
            if current_len + sep_len + sent_len > self.chunk_size and current:
                chunks.append(" ".join(current))
                overlap_text = self._overlap_tail(current[-1])
                if overlap_text:
                    current = [overlap_text]
                    current_len = len(overlap_text)
                else:
                    current = []
                    current_len = 0

            current.append(sent)
            current_len += sep_len + sent_len

        if current:
            chunks.append(" ".join(current))

        return chunks

    def _split_by_words(self, text: str) -> List[str]:
        """Last-resort word-level split with overlap."""
        words = text.split()
        chunks: List[str] = []
        current: List[str] = []
        current_len = 0

        for word in words:
            word_len = len(word)
            sep_len = 1 if current else 0

            if current_len + sep_len + word_len > self.chunk_size and current:
                chunk_text = " ".join(current)
                chunks.append(chunk_text)
                # Overlap: keep trailing words that fit within self.overlap chars
                keep = self._overlap_words(current)
                current = keep
                current_len = sum(len(w) for w in current) + max(0, len(current) - 1)

            current.append(word)
            current_len += sep_len + word_len

        if current:
            chunks.append(" ".join(current))

        return chunks

    def _overlap_tail(self, text: str) -> str:
        """Return the last ~self.overlap chars of text, word-boundary aligned."""
        if self.overlap <= 0 or not text:
            return ""
        if len(text) <= self.overlap:
            return text
        # Walk backwards to find a word boundary
        cut = len(text) - self.overlap
        space_idx = text.find(" ", cut)
        if space_idx == -1:
            return ""
        return text[space_idx + 1 :]

    def _overlap_words(self, words: List[str]) -> List[str]:
        """Return trailing words from list that fit within self.overlap chars."""
        if self.overlap <= 0:
            return []
        result: List[str] = []
        total = 0
        for word in reversed(words):
            added = len(word) + (1 if result else 0)
            if total + added > self.overlap:
                break
            result.append(word)
            total += added
        result.reverse()
        return result
