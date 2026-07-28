"""Text authority: the PDF text layer via PyMuPDF (spec §1.3 ②).

For born-digital PDFs the embedded text layer IS the ground truth —
100% recall by definition.  This module extracts it per page (with
line bboxes for region assignment), tags running-header furniture,
and provides the normalization used by both composition and the I0
reconciliation gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Set, Tuple

import fitz  # PyMuPDF — optional dep, build-host only.

# Keep CJK ideographs and ASCII alphanumerics; drop everything
# else (whitespace, punctuation, markup) so comparisons survive
# rendering differences between the PDF layer and markdown.
_KEEP_RE = re.compile(r"[⺀-鿿豈-﫿a-zA-Z0-9]+")

# A line whose identical text appears on more than this many pages
# is running furniture (chapter headers, footers).
_FURNITURE_PAGE_THRESHOLD = 20

# Normalized lines shorter than this carry no reconciliation value
# (stray glyphs, page decorations).
_MIN_NORM_CHARS = 4

_PAGE_NUMBER_RE = re.compile(r"^[\d\s\-–—.]+$")


def normalize(text: str) -> str:
    """Reduce text to bare CJK+alphanumeric content."""
    return "".join(_KEEP_RE.findall(text))


@dataclass(frozen=True)
class BaselineLine:
    """One authoritative text line from the PDF layer.

    Attributes:
        page: 1-based page number.
        raw: Original line text (stripped).
        norm: Normalized form (see ``normalize``).
        bbox: Line bounding box in PDF points (x0, y0, x1, y1).
        furniture: True for running headers / page numbers —
            excluded from content and from the I0 baseline.
    """

    page: int
    raw: str
    norm: str
    bbox: Tuple[float, float, float, float]
    furniture: bool


class TextAuthority:
    """Per-page authoritative lines + page geometry for one PDF."""

    def __init__(self, pdf_path: Path) -> None:
        """Extract all pages up front.

        Args:
            pdf_path: Source PDF (born-digital).
        """
        self._doc = fitz.open(str(pdf_path))
        self.page_sizes: List[Tuple[float, float]] = [
            (p.rect.width, p.rect.height) for p in self._doc
        ]
        raw_pages: List[List[Tuple[str, Tuple]]] = []
        for page in self._doc:
            lines: List[Tuple[str, Tuple]] = []
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    text = "".join(
                        span["text"] for span in line["spans"]
                    ).strip()
                    if text:
                        lines.append((text, tuple(line["bbox"])))
            raw_pages.append(lines)

        freq: Dict[str, Set[int]] = {}
        for pno, lines in enumerate(raw_pages):
            for text, _ in lines:
                freq.setdefault(text, set()).add(pno)
        furniture_texts = {
            t for t, pages in freq.items()
            if len(pages) > _FURNITURE_PAGE_THRESHOLD
        }

        self.pages: List[List[BaselineLine]] = []
        for pno, lines in enumerate(raw_pages):
            out: List[BaselineLine] = []
            for text, bbox in lines:
                norm = normalize(text)
                is_furniture = (
                    text in furniture_texts
                    or _PAGE_NUMBER_RE.fullmatch(text) is not None
                    or len(norm) < _MIN_NORM_CHARS
                )
                out.append(BaselineLine(
                    page=pno + 1,
                    raw=text,
                    norm=norm,
                    bbox=bbox,
                    furniture=is_furniture,
                ))
            self.pages.append(out)

    @property
    def page_count(self) -> int:
        """Number of pages in the source PDF."""
        return len(self.pages)

    def content_lines(self, page: int) -> List[BaselineLine]:
        """Non-furniture lines of a 1-based page."""
        return [
            ln for ln in self.pages[page - 1] if not ln.furniture
        ]

    def lines_in_region(
        self,
        page: int,
        bbox_norm: Tuple[float, float, float, float],
    ) -> List[BaselineLine]:
        """Content lines whose center falls inside a region.

        Args:
            page: 1-based page number.
            bbox_norm: Region in the engine's 0-1000 normalized
                coordinates.

        Returns:
            Baseline lines belonging to the region, in order.
        """
        w, h = self.page_sizes[page - 1]
        x0, y0, x1, y1 = (
            bbox_norm[0] / 1000 * w,
            bbox_norm[1] / 1000 * h,
            bbox_norm[2] / 1000 * w,
            bbox_norm[3] / 1000 * h,
        )
        out = []
        for ln in self.content_lines(page):
            cx = (ln.bbox[0] + ln.bbox[2]) / 2
            cy = (ln.bbox[1] + ln.bbox[3]) / 2
            if x0 <= cx <= x1 and y0 <= cy <= y1:
                out.append(ln)
        return out

    def render_region(
        self,
        page: int,
        bbox_norm: Tuple[float, float, float, float],
        out_path: Path,
        dpi: int = 150,
    ) -> Tuple[int, int]:
        """Rasterize a region to PNG (the rescue renderer).

        Args:
            page: 1-based page number.
            bbox_norm: Region in 0-1000 normalized coordinates.
            out_path: Destination PNG path.
            dpi: Render resolution.

        Returns:
            (width, height) of the rendered image in pixels.
        """
        w, h = self.page_sizes[page - 1]
        clip = fitz.Rect(
            bbox_norm[0] / 1000 * w,
            bbox_norm[1] / 1000 * h,
            bbox_norm[2] / 1000 * w,
            bbox_norm[3] / 1000 * h,
        )
        pix = self._doc[page - 1].get_pixmap(dpi=dpi, clip=clip)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        pix.save(str(out_path))
        return pix.width, pix.height

    def find_table_markdown(
        self,
        page: int,
        bbox_norm: Tuple[float, float, float, float],
    ) -> str:
        """Geometric table extraction (ladder rung 1, spec §10.4).

        Runs PyMuPDF's ruled-table finder clipped to the region.
        Deterministic — follows the table's vector ruling lines.

        Args:
            page: 1-based page number.
            bbox_norm: Region in 0-1000 normalized coordinates.

        Returns:
            Markdown table text, or '' when no table was found.
        """
        w, h = self.page_sizes[page - 1]
        clip = fitz.Rect(
            bbox_norm[0] / 1000 * w,
            bbox_norm[1] / 1000 * h,
            bbox_norm[2] / 1000 * w,
            bbox_norm[3] / 1000 * h,
        )
        try:
            tabs = self._doc[page - 1].find_tables(clip=clip)
        except Exception:  # pragma: no cover - pymupdf internals
            return ""
        parts = []
        for tab in tabs.tables:
            md = tab.to_markdown()
            if md.strip():
                parts.append(md.strip())
        return "\n\n".join(parts)

    def close(self) -> None:
        """Release the underlying document."""
        self._doc.close()
