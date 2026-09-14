"""Normalized item stream — the parser adapter contract (spec §3.4).

Whatever engine runs the geometry pass, its adapter emits a flat
list of ``NormalizedItem``.  Everything downstream (composition,
rescue, the Phase-2 index builder) consumes ONLY this shape — the
seam that makes the engine swappable without touching the index
schema or repair rules.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import List, Optional, Tuple

from pydantic import BaseModel, Field


class ItemKind(str, Enum):
    """Coarse region type from the geometry advisor."""

    PARA = "para"
    TITLE_CANDIDATE = "title-candidate"
    TABLE = "table"
    IMAGE = "image"
    PAGE_HEADER = "page_header"
    NOISE = "noise"


class NormalizedItem(BaseModel):
    """One region proposed by the geometry pass.

    Attributes:
        idx: Position in document order (0-based, contiguous).
        page: 1-based source page number.
        kind: Coarse region type.
        text: Extracted text for para/title/header items ('' if
            the engine emitted nothing).
        html: Table structure as HTML ('' when absent/empty — the
            rescue trigger for tables).
        image_path: Engine-cropped image file, relative to the
            engine output dir (None when absent — the rescue
            trigger for images).
        bbox: (x0, y0, x1, y1) in the engine's 0-1000 normalized
            page coordinates; None when the engine gave none.
    """

    idx: int
    page: int
    kind: ItemKind
    text: str = ""
    html: str = ""
    image_path: Optional[str] = None
    bbox: Optional[Tuple[float, float, float, float]] = None

    def needs_rescue(self) -> bool:
        """True when the engine proposed a region but delivered no
        content for it (the silent-loss mode measured on MinerU:
        48 empty table items on TRICITY155)."""
        if self.kind == ItemKind.TABLE:
            return not self.html.strip() and not self.image_path
        if self.kind == ItemKind.IMAGE:
            return not self.image_path
        return False


# ── MinerU adapter ────────────────────────────────────────────────

# content_list_v2.json is a per-page list of typed items.  Types
# observed on TRICITY155 (mineru 3.4.4 hybrid-engine): paragraph,
# title, table, image, chart, code, equation_interline,
# page_header, page_number, page_footer.
_MINERU_KIND_MAP = {
    "paragraph": ItemKind.PARA,
    "title": ItemKind.TITLE_CANDIDATE,
    "table": ItemKind.TABLE,
    "image": ItemKind.IMAGE,
    "chart": ItemKind.IMAGE,
    "code": ItemKind.PARA,
    "equation_interline": ItemKind.PARA,
    "page_header": ItemKind.PAGE_HEADER,
}
# Dropped entirely (furniture with no content value):
_MINERU_SKIP = {"page_number", "page_footer"}


def _mineru_text(content: dict) -> str:
    """Flatten a MinerU rich-text content payload to plain text."""
    # paragraph: {'paragraph_content': [{'type':'text','content':…}]}
    # title:     {'title_content':     [...], 'level': N}
    # header:    plain list or same shape.
    for key in (
        "paragraph_content", "title_content",
        "page_header_content", "content",
    ):
        val = content.get(key)
        if isinstance(val, str):
            return val
        if isinstance(val, list):
            return "".join(
                str(part.get("content", ""))
                if isinstance(part, dict) else str(part)
                for part in val
            )
    return ""


def load_mineru_stream(
    content_list_path: Path,
) -> List[NormalizedItem]:
    """Adapt a MinerU ``content_list_v2.json`` to the item stream.

    Args:
        content_list_path: Path to the engine's JSON (per-page
            list of typed items).

    Returns:
        Flat, document-ordered list of ``NormalizedItem``.
    """
    pages = json.loads(
        content_list_path.read_text(encoding="utf-8"),
    )
    items: List[NormalizedItem] = []
    for page_idx, page in enumerate(pages):
        for raw in page:
            rtype = raw.get("type", "")
            if rtype in _MINERU_SKIP:
                continue
            kind = _MINERU_KIND_MAP.get(rtype)
            if kind is None:
                kind = ItemKind.PARA
            content = raw.get("content") or {}
            if not isinstance(content, dict):
                content = {"content": content}

            text = ""
            html = ""
            image_path: Optional[str] = None
            if kind in (
                ItemKind.PARA,
                ItemKind.TITLE_CANDIDATE,
                ItemKind.PAGE_HEADER,
            ):
                text = _mineru_text(content).strip()
            elif kind == ItemKind.TABLE:
                html = (content.get("html") or "").strip()
                image_path = _valid_image_path(content)
                # Caption/footnote text rides along as text.
                caption = content.get("table_caption") or []
                footnote = content.get("table_footnote") or []
                text = " ".join(
                    str(x) for x in (*caption, *footnote)
                ).strip()
            elif kind == ItemKind.IMAGE:
                image_path = _valid_image_path(content)
                caption = content.get("image_caption") or []
                text = " ".join(str(x) for x in caption).strip()

            bbox = raw.get("bbox")
            items.append(NormalizedItem(
                idx=len(items),
                page=page_idx + 1,
                kind=kind,
                text=text,
                html=html,
                image_path=image_path,
                bbox=tuple(bbox) if bbox else None,
            ))
    return items


def _valid_image_path(content: dict) -> Optional[str]:
    """Extract a usable image path from an image_source payload.

    MinerU emits ``{'image_source': {'path': 'images/'}}`` (a bare
    directory) for regions it failed to crop — treat those as
    absent so ``needs_rescue`` fires.
    """
    path = (content.get("image_source") or {}).get("path") or ""
    if path and not path.endswith("/"):
        return path
    return None
