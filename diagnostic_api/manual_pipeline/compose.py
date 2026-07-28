"""Composition + rescue: assemble content.md (spec §1.3 ③④⑤).

Ordering and region types come from the geometry stream; text
completeness comes from the authority layer.  Per page:

1. Emit stream items in reading order (titles as cosmetic ``##``,
   paragraphs as engine text, tables as HTML, images as links).
2. Rescue engine-dropped regions: render the bbox from the PDF,
   attach the region's authoritative text lines (dual
   representation), and — for tables — try the geometric
   ``find_tables`` ladder rung first.
3. Completeness sweep: any authoritative line of the page still
   missing from what was emitted is appended verbatim under a
   recovered-text block, so I0 holds by construction.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from manual_pipeline.authority import (
    BaselineLine,
    TextAuthority,
    normalize,
)
from manual_pipeline.stream import ItemKind, NormalizedItem


@dataclass
class RescueRecord:
    """One engine-dropped region recovered from the PDF."""

    page: int
    kind: str
    image_file: str
    table_markdown_recovered: bool
    text_lines_attached: int


@dataclass
class ComposeResult:
    """Composition output + provenance for the build report."""

    markdown: str
    rescues: List[RescueRecord] = field(default_factory=list)
    recovered_lines: int = 0
    recovered_pages: List[int] = field(default_factory=list)
    images_emitted: int = 0


def compose(
    items: List[NormalizedItem],
    authority: TextAuthority,
    engine_dir: Path,
    out_dir: Path,
    frontmatter: str = "",
) -> ComposeResult:
    """Assemble the content markdown.

    Args:
        items: Normalized geometry stream (document order).
        authority: Text authority for the same PDF.
        engine_dir: MinerU output dir (engine image paths are
            relative to it).
        out_dir: Destination dir; images land in
            ``out_dir/images/``.
        frontmatter: Optional YAML frontmatter block (verbatim,
            including ``---`` fences) to prepend.

    Returns:
        ComposeResult with the markdown text and provenance.
    """
    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    result = ComposeResult(markdown="")
    out: List[str] = []
    if frontmatter:
        out.append(frontmatter.rstrip() + "\n")

    by_page: dict = {}
    for it in items:
        by_page.setdefault(it.page, []).append(it)

    for page in range(1, authority.page_count + 1):
        emitted_norm: List[str] = []
        page_out: List[str] = []
        for it in by_page.get(page, []):
            _emit_item(
                it, authority, engine_dir, images_dir,
                page_out, emitted_norm, result,
            )

        # ── Completeness sweep (union backfill) ──────────────
        emitted_blob = "".join(emitted_norm)
        missing: List[BaselineLine] = [
            ln for ln in authority.content_lines(page)
            if ln.norm not in emitted_blob
        ]
        if missing:
            page_out.append(
                f"<!-- recovered text (p{page}): lines the "
                f"geometry pass dropped; source = PDF text "
                f"layer -->"
            )
            for ln in missing:
                page_out.append(ln.raw)
            page_out.append("")
            result.recovered_lines += len(missing)
            result.recovered_pages.append(page)

        out.extend(page_out)

    result.markdown = "\n".join(out)
    return result


def _emit_item(
    it: NormalizedItem,
    authority: TextAuthority,
    engine_dir: Path,
    images_dir: Path,
    page_out: List[str],
    emitted_norm: List[str],
    result: ComposeResult,
) -> None:
    """Emit one stream item into the page buffer."""
    if it.kind == ItemKind.PAGE_HEADER:
        # Furniture: carried by the authority filter; not content.
        return

    if it.kind == ItemKind.TITLE_CANDIDATE:
        if it.text:
            page_out.append(f"## {it.text}\n")
            emitted_norm.append(normalize(it.text))
        return

    if it.kind == ItemKind.PARA:
        if it.text:
            page_out.append(it.text + "\n")
            emitted_norm.append(normalize(it.text))
        return

    if it.kind == ItemKind.TABLE:
        if it.text:  # caption / footnote
            page_out.append(it.text + "\n")
            emitted_norm.append(normalize(it.text))
        if it.html:
            page_out.append(it.html + "\n")
            emitted_norm.append(normalize(it.html))
            return
        _rescue_region(
            it, authority, images_dir, page_out,
            emitted_norm, result, try_table=True,
        )
        return

    if it.kind == ItemKind.IMAGE:
        if it.image_path:
            src = engine_dir / it.image_path
            name = Path(it.image_path).name
            if src.is_file():
                shutil.copy2(src, images_dir / name)
            page_out.append(f"![figure](images/{name})\n")
            result.images_emitted += 1
            # Dual representation: figure-label text stays
            # searchable next to the image.
            if it.bbox:
                labels = authority.lines_in_region(
                    it.page, it.bbox,
                )
                for ln in labels:
                    page_out.append(ln.raw)
                    emitted_norm.append(ln.norm)
                if labels:
                    page_out.append("")
            if it.text:
                page_out.append(it.text + "\n")
                emitted_norm.append(normalize(it.text))
            return
        _rescue_region(
            it, authority, images_dir, page_out,
            emitted_norm, result, try_table=False,
        )


def _rescue_region(
    it: NormalizedItem,
    authority: TextAuthority,
    images_dir: Path,
    page_out: List[str],
    emitted_norm: List[str],
    result: ComposeResult,
    try_table: bool,
) -> None:
    """Recover an engine-dropped region from the PDF (spec ④⑤).

    Renders the bbox as an image, optionally attempts geometric
    table extraction (ladder rung 1), and attaches the region's
    authoritative text lines so the content stays searchable.
    """
    if it.bbox is None:
        return  # Nothing to locate; completeness sweep covers text.

    name = f"rescue_p{it.page:03d}_{it.idx}.png"
    authority.render_region(it.page, it.bbox, images_dir / name)
    page_out.append(
        f"<!-- rescued region (engine emitted empty "
        f"{it.kind.value}) -->"
    )
    page_out.append(f"![rescued {it.kind.value}](images/{name})\n")
    result.images_emitted += 1

    table_md = ""
    if try_table:
        table_md = authority.find_table_markdown(it.page, it.bbox)
        if table_md:
            page_out.append(table_md + "\n")
            emitted_norm.append(normalize(table_md))

    lines = authority.lines_in_region(it.page, it.bbox)
    attached = 0
    if lines:
        blob = "".join(emitted_norm)
        for ln in lines:
            if ln.norm not in blob:
                page_out.append(ln.raw)
                emitted_norm.append(ln.norm)
                attached += 1
        page_out.append("")

    result.rescues.append(RescueRecord(
        page=it.page,
        kind=it.kind.value,
        image_file=name,
        table_markdown_recovered=bool(table_md),
        text_lines_attached=attached,
    ))
