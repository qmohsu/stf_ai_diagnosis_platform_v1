"""Logical-tree builder — repair rules R1–R5 (spec §5, S2.3).

Builds the index tree from the normalized item stream WITHOUT
trusting any engine heading level.  Deterministic, ordered rules:

R1  Noise demotion: banner/flowchart/caption/sentence titles are
    never node boundaries (their items stay inside the enclosing
    node's span — folded, never dropped).
R2  DTC boundary synthesis: ``故障代碼編號 PXXXX`` in a para or
    table opens a fault-isolation node even when no title exists
    (recovers the 13 orphaned DTCs).
R3  Troubleshooting nesting: known cause-group titles attach
    under the nearest preceding symptom title.
R4  Chapter assembly from page headers: the running header text
    anchors the top level; every page belongs to the chapter
    whose header dominates it.
R5  Known-structure templates: table payloads with self-diag /
    spec headers fix the node_type regardless of title wording.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from manual_pipeline.index_schema import (
    IndexNode,
    Vocab,
    make_node_id,
)
from manual_pipeline.stream import ItemKind, NormalizedItem

# ── R1 noise patterns ─────────────────────────────────────────────

_NOISE_RES = [
    # Bare or code-suffixed caution banners.
    re.compile(
        r"^\s*(?:注\s*意|警\s*告|註)(?:\s+[A-Z]{2,4}\d\w*)?\s*$"
    ),
    # Flowchart decision labels / arrow rows.
    re.compile(r"^\s*(?:OK\s*↓|[▲▼\s]+)\s*$"),
    # Numbered procedure steps promoted to titles (``4. 檢查:``).
    re.compile(r"^\s*\d+\.\s+\S{1,40}:?\s*$"),
    # Bullet fragments promoted to titles.
    re.compile(r"^\s*[•·]"),
]

# Full-sentence warnings rendered as titles: real section titles
# never end with a CJK full stop.
_SENTENCE_TITLE_RE = re.compile(r"。\s*$")

_DTC_BOUNDARY_RE = re.compile(
    r"故障代碼編號\s*([PCBU]\d[0-9A-F]{3})", re.IGNORECASE,
)

# R5 table-template signatures → node_type.
_TABLE_TEMPLATES = [
    ("故障防護系統", "specification"),   # self-diag function table
    ("工作/拆卸零件", "parts"),
    ("鎖緊扭力", "specification"),
]

_MIN_TITLE_CHARS = 2
_MAX_TITLE_CHARS = 60


def is_noise_title(title: str) -> bool:
    """R1: True when a title candidate must not open a node."""
    stripped = title.strip()
    if not (
        _MIN_TITLE_CHARS <= len(stripped) <= _MAX_TITLE_CHARS
    ):
        return True
    if _SENTENCE_TITLE_RE.search(stripped):
        return True
    return any(rx.match(stripped) for rx in _NOISE_RES)


@dataclass
class _Boundary:
    """A node-opening point in the stream."""

    item_idx: int
    title: str
    synthesized: bool  # True for R2 DTC boundaries


@dataclass
class TreeBuildResult:
    """Tree + provenance for the report and invariants."""

    roots: List[IndexNode]
    noise_item_idxs: Set[int]
    synthesized_boundaries: int
    unclassified_nodes: List[str]


_CJK_RE = re.compile(r"[⺀-鿿豈-﫿]")
_ALPHA_WORD_RE = re.compile(r"[A-Za-z]{2,}")


def _headerish(text: str) -> bool:
    """A header fragment that is a section NAME, not furniture.

    CJK manuals: ≥2 CJK chars (TRICITY155: 前煞車, 汽油噴射系統).
    Latin manuals (M4 / Corolla adjustment): ≥2 alphabetic words —
    filters brand marks, years, and document codes (YAMAHA / 2016 /
    EAS10003 / 2A•15) while keeping 'Engine in-car repair
    procedures'.
    """
    if len(_CJK_RE.findall(text)) >= 2:
        return True
    words = _ALPHA_WORD_RE.findall(text)
    return len(words) >= 2 and sum(len(w) for w in words) >= 8


def _chapter_map(
    items: List[NormalizedItem],
    total_pages: int,
) -> Dict[int, str]:
    """R4: page → chapter title from running headers.

    The header zone carries several fragments (brand mark, year,
    document codes, the section name) — only name-like fragments
    (see ``_headerish``) anchor chapters.  Pages without one
    inherit the previous page's chapter; front matter gets
    '前言'.
    """
    page_headers: Dict[int, str] = {}
    for it in items:
        if it.kind != ItemKind.PAGE_HEADER or not it.text:
            continue
        text = it.text.strip()
        if _headerish(text):
            page_headers.setdefault(it.page, text)
    mapping: Dict[int, str] = {}
    current = "前言"
    for page in range(1, total_pages + 1):
        if page in page_headers:
            current = page_headers[page]
        mapping[page] = current
    return mapping


# R6 (Phase 4): task-suffix patterns that mark a bare PARA line
# as an engine-missed section title (the #186 unheaded-title
# family: 液壓煞車系統空氣的釋放, 汽門間隙的調整, 冷卻液溫度感知器
# 的檢查 carry NO title item in the MinerU stream).  Conservative
# by design — whole-para, short, task-suffixed, no sentence stop.
_TASK_SUFFIX_RE = re.compile(
    r"(?:的釋放|的調整|的檢查|的更換|的清潔|的拆卸|的安裝|"
    r"的分解|的組裝|的測量)$"
)
_MAX_PROMOTED_CHARS = 24


def _is_promotable_para(text: str) -> bool:
    """R6: a bare paragraph that IS a section title.

    Must also survive the R1 noise filter — numbered procedure
    steps like ``3. 油門鋼索的安裝`` end in task suffixes too and
    must NOT become nodes (caught by I5 on the first R6 build).
    """
    t = text.strip()
    return (
        0 < len(t) <= _MAX_PROMOTED_CHARS
        and _TASK_SUFFIX_RE.search(t) is not None
        and "。" not in t
        and "," not in t and "," not in t
        and not is_noise_title(t)
    )


def _find_boundaries(
    items: List[NormalizedItem],
    noise: Set[int],
) -> List[_Boundary]:
    """Surviving titles (R1) + synthesized DTC boundaries (R2)
    + promoted unheaded titles (R6)."""
    boundaries: List[_Boundary] = []
    titled_codes: Set[str] = set()
    seen_titles: Set[str] = set()
    for it in items:
        if it.kind == ItemKind.TITLE_CANDIDATE:
            if is_noise_title(it.text):
                noise.add(it.idx)
                continue
            boundaries.append(_Boundary(it.idx, it.text, False))
            seen_titles.add(it.text.strip())
            for code in _DTC_BOUNDARY_RE.findall(it.text):
                titled_codes.add(code.upper())
    # R6 pass: bare-para titles the engine missed.
    for it in items:
        if it.kind == ItemKind.PARA and _is_promotable_para(
            it.text,
        ):
            boundaries.append(_Boundary(
                it.idx, it.text.strip(), True,
            ))
    # R2 pass: DTC blocks with no title of their own.
    for it in items:
        if it.kind not in (ItemKind.PARA, ItemKind.TABLE):
            continue
        payload = f"{it.text} {it.html}"
        m = _DTC_BOUNDARY_RE.search(payload)
        if not m:
            continue
        code = m.group(1).upper()
        if code in titled_codes:
            continue
        titled_codes.add(code)
        boundaries.append(_Boundary(
            it.idx, f"故障代碼編號 {code}", True,
        ))
    boundaries.sort(key=lambda b: b.item_idx)
    return boundaries


def build_tree(
    items: List[NormalizedItem],
    vocab: Vocab,
    total_pages: int,
) -> TreeBuildResult:
    """Run R1–R5 and produce the chapter-rooted logical tree.

    Args:
        items: Normalized stream (document order).
        vocab: Controlled vocabulary.
        total_pages: Page count of the source PDF.

    Returns:
        TreeBuildResult with roots whose sibling spans tile the
        stream (the I1 precondition).
    """
    noise: Set[int] = set()
    chapters = _chapter_map(items, total_pages)
    boundaries = _find_boundaries(items, noise)

    # Group boundaries by chapter (via the page they start on).
    used_ids: Dict[str, int] = {}
    roots: List[IndexNode] = []
    unclassified: List[str] = []
    n_items = len(items)

    # Chapter runs: consecutive pages sharing a chapter title.
    runs: List[Tuple[str, int, int]] = []  # (title, first, last)
    for page in range(1, total_pages + 1):
        title = chapters[page]
        if runs and runs[-1][0] == title:
            runs[-1] = (title, runs[-1][1], page)
        else:
            runs.append((title, page, page))

    # Map item idx ranges per chapter run.
    def items_in_pages(p0: int, p1: int) -> Tuple[int, int]:
        idxs = [
            it.idx for it in items if p0 <= it.page <= p1
        ]
        if not idxs:
            return (0, 0)
        return (idxs[0], idxs[-1] + 1)

    cause_groups = set(vocab.troubleshooting_cause_groups)

    for run_title, p0, p1 in runs:
        span = items_in_pages(p0, p1)
        if span[0] == span[1]:
            continue
        subsystem = vocab.subsystem_for(run_title) or "general"
        chap_id = _dedupe(
            make_node_id(subsystem, "description", run_title),
            used_ids,
        )
        chapter = IndexNode(
            node_id=chap_id,
            title=run_title,
            node_type="description",
            subsystem=subsystem,
            span=span,
            page_range=(p0, p1),
        )

        chap_bounds = [
            b for b in boundaries
            if span[0] <= b.item_idx < span[1]
        ]
        _attach_children(
            chapter, chap_bounds, items, vocab, cause_groups,
            used_ids, unclassified,
        )
        roots.append(chapter)

    # Ensure full coverage: extend last chapter to stream end.
    if roots and roots[-1].span[1] < n_items:
        last = roots[-1]
        last.span = (last.span[0], n_items)
        if last.children:
            tail = last.children[-1]
            tail.span = (tail.span[0], n_items)

    _merge_thin_leaves(roots, items)

    synthesized = sum(1 for b in boundaries if b.synthesized)
    return TreeBuildResult(
        roots=roots,
        noise_item_idxs=noise,
        synthesized_boundaries=synthesized,
        unclassified_nodes=unclassified,
    )


_MIN_LEAF_CHARS = 50


def _leaf_is_thin(
    node: IndexNode, items: List[NormalizedItem],
) -> bool:
    """A childless node with <50 content chars and no image —
    typically a page-break duplicate or a bare cover title."""
    if node.children:
        return False
    span_items = items[node.span[0]:node.span[1]]
    chars = sum(len(it.text) + len(it.html) for it in span_items)
    has_image = any(
        it.kind == ItemKind.IMAGE for it in span_items
    )
    return chars < _MIN_LEAF_CHARS and not has_image


def _merge_thin_leaves(
    roots: List[IndexNode],
    items: List[NormalizedItem],
) -> None:
    """Absorb thin leaves into an adjacent sibling.

    The thin node's title survives as an alias of the absorber,
    so legacy references still resolve; its span is unioned so
    I1 tiling stays intact.  Runs bottom-up over every sibling
    list (chapter roots included).
    """

    def merge_siblings(
        siblings: List[IndexNode],
        parent: Optional[IndexNode],
    ) -> None:
        for node in siblings:
            if node.children:
                merge_siblings(node.children, node)
        i = 0
        while i < len(siblings):
            node = siblings[i]
            if not _leaf_is_thin(node, items):
                i += 1
                continue
            absorber = (
                siblings[i + 1] if i + 1 < len(siblings)
                else (siblings[i - 1] if i > 0 else None)
            )
            if absorber is None:
                # Only child: fold into the parent (its span
                # already covers the child's).
                if parent is None:
                    i += 1
                    continue
                parent.aliases.append(node.title)
                siblings.pop(i)
                continue
            absorber.aliases.append(node.title)
            absorber.span = (
                min(absorber.span[0], node.span[0]),
                max(absorber.span[1], node.span[1]),
            )
            absorber.page_range = (
                min(absorber.page_range[0], node.page_range[0]),
                max(absorber.page_range[1], node.page_range[1]),
            )
            siblings.pop(i)

    merge_siblings(roots, None)


def _attach_children(
    chapter: IndexNode,
    bounds: List[_Boundary],
    items: List[NormalizedItem],
    vocab: Vocab,
    cause_groups: Set[str],
    used_ids: Dict[str, int],
    unclassified: List[str],
) -> None:
    """Build chapter children; apply R3 nesting + R5 templates."""
    if not bounds:
        return
    ends = [b.item_idx for b in bounds[1:]] + [chapter.span[1]]
    is_troubleshooting = "故障排除" in chapter.title

    last_symptom: Optional[IndexNode] = None
    for bound, end in zip(bounds, ends):
        title = bound.title.strip()
        # Synthesized boundaries: R2 (DTC blocks) are
        # fault_isolation; R6 (promoted bare-para titles)
        # classify by their task suffix like any title.
        node_type = (
            "fault_isolation"
            if bound.synthesized and _DTC_BOUNDARY_RE.search(title)
            else vocab.node_type_for(title)
        )
        # R5: table-template override.
        for it in items[bound.item_idx:end]:
            if it.kind == ItemKind.TABLE and it.html:
                for sig, ntype in _TABLE_TEMPLATES:
                    if sig in it.html:
                        if node_type == "description":
                            node_type = ntype
                        break

        subsystem = vocab.subsystem_for(title)
        if subsystem is None:
            subsystem = (
                chapter.subsystem
                if chapter.subsystem != "unclassified"
                else "unclassified"
            )
        node = IndexNode(
            node_id=_dedupe(
                make_node_id(subsystem, node_type, title),
                used_ids,
            ),
            title=title,
            node_type=node_type,
            subsystem=subsystem,
            span=(bound.item_idx, end),
            page_range=_page_range(items, bound.item_idx, end),
        )
        if node.subsystem == "unclassified":
            unclassified.append(node.node_id)

        # R3: cause-group nesting inside the troubleshooting
        # chapter; everything else is a flat chapter child.
        if (
            is_troubleshooting
            and title in cause_groups
            and last_symptom is not None
        ):
            last_symptom.children.append(node)
            last_symptom.span = (
                last_symptom.span[0], node.span[1],
            )
            last_symptom.page_range = (
                last_symptom.page_range[0], node.page_range[1],
            )
        else:
            chapter.children.append(node)
            if is_troubleshooting and title not in cause_groups:
                last_symptom = node


def _page_range(
    items: List[NormalizedItem], start: int, end: int,
) -> Tuple[int, int]:
    """Page range covered by an item span."""
    pages = [it.page for it in items[start:end]]
    if not pages:
        return (0, 0)
    return (min(pages), max(pages))


def _dedupe(node_id: str, used: Dict[str, int]) -> str:
    """Deterministic collision suffixes, robust to natural ids
    that already end in ``-N`` (loop until genuinely unused)."""
    if node_id not in used:
        used[node_id] = 1
        return node_id
    n = used[node_id]
    while True:
        n += 1
        candidate = f"{node_id}-{n}"
        if candidate not in used:
            used[node_id] = n
            used[candidate] = 1
            return candidate
