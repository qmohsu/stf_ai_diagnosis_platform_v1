"""Section-aware text chunker with CJK and image-marker support.

Splits text within section boundaries using a paragraph -> sentence -> word
hierarchy.  Never splits mid-paragraph when the paragraph fits within
chunk_size.

Image-marker handling:
- Recognises ``[Image N, Page M]``, ``[OCR, Page M]``, and
  ``[Full Page, Page M]`` markers produced by the PDF parser's
  OCR / vision enrichment.
- An image block (marker line + following description lines up to the
  next marker or blank-line boundary) is treated as an atomic unit and
  is never split mid-description.
- ``ChunkedSection.has_image`` is ``True`` when the chunk text contains
  at least one image or OCR marker.

CJK handling:
- Sentence splitting recognises Chinese/Japanese full-stop punctuation
  (。！？) in addition to ASCII (.!?).
- Paragraph detection normalises PDF-extracted text that uses single ``\\n``
  between logical paragraphs (heuristic: line ends with CJK sentence-end
  punctuation → paragraph break).
- Word-level fallback uses ``jieba`` for Chinese segmentation when no
  whitespace boundaries are available.
"""

import re
import unicodedata
from typing import List

from pydantic import BaseModel

from app.rag.parser import Section

# Sentence splitting: English (.!?) followed by whitespace, OR
# CJK full-stop punctuation (no trailing whitespace required).
SENTENCE_SPLIT = re.compile(
    r"(?<=[.!?])\s+|(?<=[。！？])"
)

# CJK sentence-ending punctuation used for paragraph heuristic
_CJK_SENTENCE_END = re.compile(r"[。！？]$")

# Detect whether a string contains CJK characters
_CJK_RANGE = re.compile(
    r"[\u2E80-\u9FFF\uF900-\uFAFF\U00020000-\U0002FA1F]"
)

# CJK punctuation suitable as split points for word-level fallback
_CJK_PUNCT_SPLIT = re.compile(
    r"(?<=[，、；：。！？「」（）『』【】])"
)

# Markers inserted by pdf_parser (vision + OCR enrichment)
_IMAGE_MARKER = re.compile(
    r"\[(?:Image \d+, Page \d+"
    r"|OCR, Page \d+"
    r"|Full Page, Page \d+)\]"
)


def _has_cjk(text: str) -> bool:
    """Return True if *text* contains any CJK characters."""
    return bool(_CJK_RANGE.search(text))


def _jieba_segment(text: str) -> List[str]:
    """Segment *text* into words using jieba.

    Imported lazily so jieba is only loaded when CJK text is
    encountered.  Falls back to per-character splitting if jieba
    is not installed.

    Args:
        text: Chinese text to segment.

    Returns:
        List of word tokens (no empty strings).
    """
    try:
        import jieba
        tokens = jieba.lcut(text)
    except ImportError:
        # Graceful degradation: split on punctuation, then chars
        tokens = _CJK_PUNCT_SPLIT.split(text)
        if len(tokens) <= 1:
            tokens = list(text)
    return [t for t in tokens if t.strip()]


def _normalize_paragraphs(text: str) -> str:
    """Normalise paragraph boundaries for PDF-extracted text.

    PyMuPDF typically outputs single ``\\n`` between every line.
    This function inserts ``\\n\\n`` (true paragraph break) after
    lines that end with CJK sentence-ending punctuation, so the
    downstream paragraph splitter can find real boundaries.

    Only applies the heuristic when the text has many single
    newlines but very few double newlines — i.e. it looks like
    raw PDF output rather than a well-formatted document.

    Args:
        text: Raw extracted text.

    Returns:
        Text with normalised paragraph breaks.
    """
    double_nl_count = text.count("\n\n")
    single_nl_count = text.count("\n") - double_nl_count * 2
    if double_nl_count > single_nl_count * 0.3:
        # Already has reasonable paragraph structure
        return text

    lines = text.split("\n")
    result: List[str] = []
    for line in lines:
        result.append(line)
        stripped = line.rstrip()
        if _CJK_SENTENCE_END.search(stripped):
            result.append("")  # insert blank line → \n\n
    return "\n".join(result)


def _merge_image_blocks(paragraphs: List[str]) -> List[str]:
    """Merge image-marker paragraphs with immediately following text.

    In the PDF parser output, ``[Image N, Page M]\\nDescription: ...``
    is normally a single paragraph (joined by ``\\n``).  This function
    handles the edge case where a description line was separated into
    its own paragraph by the ``\\n\\n`` splitter.

    Only the **first** non-marker paragraph after a marker is merged
    (the description).  Subsequent regular paragraphs are kept
    separate to avoid pulling body text into image blocks.

    Args:
        paragraphs: List of paragraph strings (already split by
            ``\\n\\n``).

    Returns:
        New list where image-marker paragraphs are merged with
        at most one following description paragraph.
    """
    merged: List[str] = []
    i = 0
    while i < len(paragraphs):
        para = paragraphs[i]

        if _IMAGE_MARKER.search(para):
            # Merge with the next paragraph if it is NOT another
            # marker (i.e. it is a stray description line).
            if (
                i + 1 < len(paragraphs)
                and not _IMAGE_MARKER.search(paragraphs[i + 1])
            ):
                merged.append(
                    para + "\n\n" + paragraphs[i + 1],
                )
                i += 2
            else:
                merged.append(para)
                i += 1
        else:
            merged.append(para)
            i += 1

    return merged


class ChunkedSection(BaseModel):
    """A chunk produced from a parsed Section, carrying full metadata."""

    text: str
    section_title: str
    vehicle_model: str
    dtc_codes: List[str] = []
    chunk_index: int = 0
    has_image: bool = False


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

    def chunk_sections(
        self, sections: List[Section],
    ) -> List[ChunkedSection]:
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
                        has_image=bool(
                            _IMAGE_MARKER.search(raw)
                        ),
                    )
                )
                global_idx += 1

        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _split_section(self, text: str) -> List[str]:
        """Split a section body into chunks respecting paragraph boundaries.

        Image-marker blocks (``[Image N, Page M]``, ``[OCR, Page M]``,
        ``[Full Page, Page M]`` followed by description text) are
        treated as atomic units and are never split mid-description.
        """
        text = text.strip()
        if not text:
            return []

        # Normalise paragraph boundaries for PDF-extracted CJK text
        text = _normalize_paragraphs(text)

        raw_paragraphs = [
            p.strip()
            for p in re.split(r"\n{2,}", text)
            if p.strip()
        ]

        # Merge image-marker paragraphs with following description
        # paragraphs into atomic blocks.
        paragraphs = _merge_image_blocks(raw_paragraphs)

        chunks: List[str] = []
        current_parts: List[str] = []
        current_len = 0

        for para in paragraphs:
            para_len = len(para)

            # Atomic image blocks: if oversized, give own chunk
            if (
                _IMAGE_MARKER.search(para)
                and para_len > self.chunk_size
            ):
                if current_parts:
                    chunks.append("\n\n".join(current_parts))
                    current_parts = []
                    current_len = 0
                chunks.append(para)
                continue

            if para_len > self.chunk_size:
                # Flush anything accumulated so far
                if current_parts:
                    chunks.append("\n\n".join(current_parts))
                    current_parts = []
                    current_len = 0

                # Split the oversized paragraph by sentences, then words
                chunks.extend(
                    self._split_large_paragraph(para),
                )
                continue

            # Would adding this paragraph exceed the limit?
            sep_len = 2 if current_parts else 0  # "\n\n"
            if (
                current_len + sep_len + para_len > self.chunk_size
                and current_parts
            ):
                chunks.append("\n\n".join(current_parts))
                # Overlap: take trailing text from the last part
                overlap_text = self._overlap_tail(
                    current_parts[-1],
                )
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
            if (
                current_len + sep_len + sent_len > self.chunk_size
                and current
            ):
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
        """Last-resort word-level split with overlap.

        For CJK text without whitespace boundaries, uses ``jieba``
        segmentation to produce linguistically meaningful tokens.
        """
        words = text.split()

        # If whitespace split yields a single token and text
        # contains CJK characters, use jieba segmentation.
        if len(words) <= 1 and _has_cjk(text):
            words = _jieba_segment(text)

        chunks: List[str] = []
        current: List[str] = []
        current_len = 0

        # Determine the join string: CJK tokens have no separator
        is_cjk = _has_cjk(text) and " " not in text
        join_str = "" if is_cjk else " "
        sep_char_len = len(join_str)

        for word in words:
            word_len = len(word)
            sep_len = sep_char_len if current else 0

            if (
                current_len + sep_len + word_len > self.chunk_size
                and current
            ):
                chunk_text = join_str.join(current)
                chunks.append(chunk_text)
                # Overlap: keep trailing words
                keep = self._overlap_words(current, sep_char_len)
                current = keep
                current_len = (
                    sum(len(w) for w in current)
                    + max(0, len(current) - 1) * sep_char_len
                )

            current.append(word)
            current_len += sep_len + word_len

        if current:
            chunks.append(join_str.join(current))

        return chunks

    def _overlap_tail(self, text: str) -> str:
        """Return the last ~self.overlap chars of text, boundary aligned.

        For CJK text, falls back to a simple character-level tail
        since word boundaries are not whitespace-delimited.
        """
        if self.overlap <= 0 or not text:
            return ""
        if len(text) <= self.overlap:
            return text

        cut = len(text) - self.overlap

        # Try whitespace boundary first
        space_idx = text.find(" ", cut)
        if space_idx != -1:
            return text[space_idx + 1:]

        # For CJK: try punctuation boundary
        if _has_cjk(text):
            punct_match = _CJK_PUNCT_SPLIT.search(text[cut:])
            if punct_match:
                return text[cut + punct_match.end():]
            # Last resort: character-level cut
            return text[cut:]

        return ""

    def _overlap_words(
        self,
        words: List[str],
        sep_len: int = 1,
    ) -> List[str]:
        """Return trailing words fitting within self.overlap chars.

        Args:
            words: List of word tokens.
            sep_len: Length of separator between tokens (1 for
                space-separated, 0 for CJK concatenation).

        Returns:
            Trailing subset of *words*.
        """
        if self.overlap <= 0:
            return []
        result: List[str] = []
        total = 0
        for word in reversed(words):
            added = len(word) + (sep_len if result else 0)
            if total + added > self.overlap:
                break
            result.append(word)
            total += added
        result.reverse()
        return result
