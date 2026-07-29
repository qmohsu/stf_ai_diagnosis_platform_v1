"""Manual filesystem navigation tools for the harness agent loop.

Four tools for structured manual retrieval:
  - ``list_manuals``: discover available manuals
  - ``get_manual_toc``: read a manual's heading structure
  - ``read_manual_section``: read a full section with images
  - ``search_manual_text``: literal grep-style search
    (HARNESS-30a — the mandatory absence-check)

These complement ``search_manual`` (semantic RAG search) with
precise filesystem-based navigation.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

import structlog

from app.config import settings
from app.harness.tool_registry import (
    ToolDefinition,
    ToolOutput,
)
from app.harness_tools.input_models import (
    GetManualTocInput,
    ListManualsInput,
    ReadManualSectionInput,
    SearchManualTextInput,
)
from app.harness_tools.manual_fs import (
    HeadingNode,
    _clean_md,
    _flatten_tree,
    build_multimodal_section,
    extract_section,
    find_closest_slug,
    parse_frontmatter,
    parse_heading_tree,
    promote_unheaded_titles,
    slugify,
)

logger = structlog.get_logger(__name__)

_MANUAL_DIR = Path(settings.manual_storage_path)

# Directories to skip when scanning for .md files.  ``index``
# holds the Phase-3 sidecar + v2 content — its .md must not be
# listed as a separate manual.
_SKIP_DIRS = {"images", "uploads", ".queue", "index"}


# ── Helpers ───────────────────────────────────────────────────────


def _scan_manual_files() -> List[Path]:
    """Find all manual .md files in the storage directory.

    Skips files inside ``images/``, ``uploads/``, and
    ``.queue/`` subdirectories.

    Returns:
        Sorted list of absolute paths to ``.md`` files.
    """
    if not _MANUAL_DIR.is_dir():
        return []
    results: List[Path] = []
    for md_file in _MANUAL_DIR.rglob("*.md"):
        # Skip files in excluded subdirectories.
        rel = md_file.relative_to(_MANUAL_DIR)
        if any(
            part in _SKIP_DIRS for part in rel.parts[:-1]
        ):
            continue
        results.append(md_file)
    return sorted(results)


def _read_manual_file(manual_id: str) -> str | None:
    """Read a manual markdown file by its filename stem.

    Searches for ``{manual_id}.md`` anywhere in the manual
    storage directory (excluding skip dirs).

    Args:
        manual_id: Filename stem (e.g.
            ``MWS150A_Service_Manual``).

    Returns:
        File content string, or None if not found.
    """
    for md_file in _scan_manual_files():
        if md_file.stem == manual_id:
            # Promote span-anchored unheaded titles to real
            # headings BEFORE the HTML-noise strip — the page-
            # anchor spans are the detection signal and _clean_md
            # removes them (#186).  Then the defensive strip for
            # older .md files that pre-date the conversion-time
            # cleaner.
            return _clean_md(
                promote_unheaded_titles(
                    md_file.read_text(encoding="utf-8"),
                ),
            )
    return None


def _format_toc_tree(
    nodes: List[HeadingNode],
    indent: int = 0,
    max_depth: int | None = None,
) -> str:
    """Format a heading tree as indented text.

    Args:
        nodes: Heading nodes to format.
        indent: Current indentation level (0-based).
        max_depth: Optional cap on how deep to descend.  When
            set, children at ``indent >= max_depth`` are omitted
            and a placeholder ``"  ...N more nested sections"``
            is shown so the agent knows there's more to explore.
            ``None`` means unlimited (full tree).

    Returns:
        Indented tree string with slugs in brackets.
    """
    lines: List[str] = []
    prefix = "  " * indent
    for node in nodes:
        lines.append(
            f"{prefix}- {node.title}  "
            f"[{node.slug}]"
        )
        if not node.children:
            continue
        if max_depth is not None and indent + 1 >= max_depth:
            # Don't recurse — but tell the agent how many we hid
            # so it can opt into a deeper view.
            hidden = _count_descendants(node.children)
            if hidden > 0:
                lines.append(
                    f"{prefix}  ...{hidden} more "
                    f"nested sections "
                    f"(call get_manual_toc with "
                    f"max_depth={max_depth + 1} or higher)"
                )
            continue
        lines.append(
            _format_toc_tree(
                node.children,
                indent + 1,
                max_depth=max_depth,
            ),
        )
    return "\n".join(lines)


def _count_descendants(nodes: List[HeadingNode]) -> int:
    """Total number of nodes in a subtree (including roots)."""
    n = 0
    for node in nodes:
        n += 1 + _count_descendants(node.children)
    return n


# OBD-II style DTC token: P/C/B/U + 4 hex-ish digits (P0107,
# P062F, ...).  Word-bounded so P0107 in "P0107、P0108" matches
# but the digits of e.g. "8-101" do not.
_DTC_TOKEN_PATTERN = re.compile(
    r"\b([PCBU]\d[0-9A-F]{3})\b", re.IGNORECASE,
)

# A DTC-index table row: first cell holds exactly one DTC token,
# e.g. ``| P0107 | 20 |``.
_DTC_ROW_PATTERN = re.compile(
    r"^\|\s*([PCBU]\d[0-9A-F]{3})\s*\|", re.IGNORECASE,
)


def _build_dtc_slug_map(md_text: str) -> Dict[str, str]:
    """Map DTC codes to the slug of the heading that names them.

    Scans EVERY heading in the manual (all depths — the
    ``故障代碼編號 P0107、P0108`` diagnostic sections sit at
    ``####`` level, below the default TOC depth) and records,
    for each DTC token in a heading title, the slug of the first
    heading that mentions it.  A heading naming several codes
    (``P0107、P0108``) maps each of them to itself.

    Args:
        md_text: Full manual markdown.

    Returns:
        Dict of upper-cased DTC code → section slug.
    """
    mapping: Dict[str, str] = {}
    for node in _flatten_tree(parse_heading_tree(md_text)):
        for raw in _DTC_TOKEN_PATTERN.findall(node.title):
            mapping.setdefault(raw.upper(), node.slug)
    return mapping


# HARNESS-30a (#217): the marker a quick-index row carries when a
# code appears in the manual body but no heading names it.  13/22
# TRICITY155 codes are in this state; cross-006 showed the agent
# reading the former ``-`` as proof of absence.  The marker must be
# an instruction, never a shrug.
_UNINDEXED_MARKER = "NOT-INDEXED(use search_manual_text)"


def _augment_dtc_index(
    index_text: str,
    slug_map: Dict[str, str],
) -> str:
    """Append a section-slug column to the DTC index table.

    The conversion pipeline emits the appendix as
    ``| DTC | Occurrences |`` — an occurrence count with no way
    to navigate to the code's diagnostic section.  This rewrites
    each row to carry the slug of the heading that names the
    code, so the agent can jump straight from the index to
    ``read_manual_section`` (the behaviour the manual-agent
    prompt promises).  Codes with no matching heading get
    ``NOT-INDEXED(use search_manual_text)`` — they ARE in the
    manual (see the occurrence count) but lack an indexed
    section (HARNESS-30a).

    Args:
        index_text: The raw index table text.
        slug_map: DTC code → slug from ``_build_dtc_slug_map``.

    Returns:
        Table text with a ``Section slug`` column appended to
        header, separator, and each DTC row; non-table lines
        are passed through unchanged.
    """
    out_lines: List[str] = []
    for line in index_text.split("\n"):
        stripped = line.rstrip()
        row_match = _DTC_ROW_PATTERN.match(stripped)
        if row_match:
            code = row_match.group(1).upper()
            slug = slug_map.get(code, _UNINDEXED_MARKER)
            out_lines.append(f"{stripped} {slug} |")
        elif re.match(r"^\|[\s:|-]+\|$", stripped):
            out_lines.append(f"{stripped}-----|")
        elif stripped.startswith("|"):
            # Header (or other non-DTC) table row.
            out_lines.append(f"{stripped} Section slug |")
        else:
            out_lines.append(line)
    return "\n".join(out_lines)


def _extract_dtc_index(md_text: str) -> str | None:
    """Extract DTC Index table from the Appendix section.

    Looks for a section titled ``Appendix: DTC Index`` and
    returns the table content.

    Args:
        md_text: Full manual markdown.

    Returns:
        DTC index text, or None if not found.
    """
    section = extract_section(
        md_text, slugify("Appendix: DTC Index"),
    )
    if section is None:
        return None
    # Strip the heading line itself; keep the table.
    lines = section.split("\n")
    body_lines = [
        line for line in lines
        if not line.startswith("#")
    ]
    body = "\n".join(body_lines).strip()
    return body if body else None


# ── Tool: list_manuals ────────────────────────────────────────────


async def list_manuals(
    input_data: Dict[str, Any],
) -> str:
    """List available service manuals.

    Scans the manual storage directory for ``.md`` files and
    returns a summary of each manual's metadata.

    Args:
        input_data: Optional ``vehicle_model`` filter.

    Returns:
        Formatted text listing each manual's ID, vehicle
        model, page count, and section count.
    """
    vehicle_filter: str | None = input_data.get(
        "vehicle_model",
    )

    md_files = _scan_manual_files()
    if not md_files:
        return (
            "No manuals found in storage. "
            "Upload a service manual PDF first."
        )

    entries: List[str] = []
    for md_file in md_files:
        # Read only the frontmatter (first ~10 lines).
        try:
            text = md_file.read_text(encoding="utf-8")
        except OSError as exc:
            logger.warning(
                "manual_read_error",
                path=str(md_file),
                exc_info=exc,
            )
            continue

        fm = parse_frontmatter(text)
        model = fm.get("vehicle_model", "unknown")
        manufacturer = fm.get("manufacturer", "")
        # APP-61: optional factory / manual code alias (e.g.
        # ``MWS150-A`` for the Yamaha Tricity 155).  Surfaced so the
        # agent can match a question phrased by the code on the
        # manual cover, not just the marketing model name.
        factory_code = fm.get("factory_code") or ""
        # Canonical "<Manufacturer> <Model>" identity (APP-59).
        canonical = (
            f"{manufacturer} {model}".strip()
            if manufacturer
            else model
        )

        if vehicle_filter:
            vf = vehicle_filter.lower()
            # Match leniently against model, manufacturer, the
            # canonical name, or the factory code so the agent can
            # filter by any of them.
            if (
                vf not in model.lower()
                and vf not in manufacturer.lower()
                and vf not in canonical.lower()
                and vf not in factory_code.lower()
            ):
                continue

        page_count = fm.get("page_count", "?")
        section_count = fm.get("section_count", "?")
        code_part = (
            f"factory_code=\"{factory_code}\"  " if factory_code else ""
        )
        entries.append(
            f"- {md_file.stem}  "
            f"vehicle=\"{canonical}\"  "
            f"{code_part}"
            f"pages={page_count}  "
            f"sections={section_count}"
        )

    if not entries:
        if vehicle_filter:
            return (
                f"No manuals found matching '{vehicle_filter}'. "
                f"Use list_manuals without a filter to see all "
                f"available manuals."
            )
        return "No manuals found in storage."

    header = f"Available manuals ({len(entries)}):\n"
    # HARNESS-25 (issue #136): force an explicit make/model match
    # against the vehicle under diagnosis so the agent stops adopting
    # an unrelated manual as authoritative (the P00AF Hiace run cited
    # a Yamaha scooter manual — #135).
    footer = (
        "\n\nIMPORTANT: only treat a manual as authoritative for "
        "this diagnosis if its `vehicle=` make/model OR its "
        "`factory_code=` matches the vehicle under investigation "
        "(check the session's vehicle_id / VIN). A manual's "
        "`factory_code` is an alternate identifier for the SAME "
        "vehicle (e.g. factory_code=\"MWS150-A\" is the Yamaha "
        "Tricity 155), so a question that names the factory code is "
        "a match for that manual. If none of the manuals above "
        "match this vehicle, say so explicitly — e.g. \"no service "
        "manual is available for this vehicle\" — and do NOT adopt "
        "an unrelated manual's vehicle identity or use it as "
        "ground truth."
    )
    return header + "\n".join(entries) + footer


# ── Tool: get_manual_toc ──────────────────────────────────────────


async def get_manual_toc(
    input_data: Dict[str, Any],
) -> str:
    """Get the table of contents for a manual.

    Parses the heading structure and returns a hierarchical
    tree with section slugs for use with ``read_manual_section``.

    Args:
        input_data: Must contain ``manual_id`` (str).  Optional
            ``max_depth`` (int, default 3) caps how deep the tree
            goes — useful for keeping the response small enough to
            fit in a context budget.  Pass a high value
            (e.g. 99) to see the full tree.

    Returns:
        Indented heading tree with slugs and optional DTC index.
    """
    manual_id: str = input_data["manual_id"]
    max_depth_raw = input_data.get("max_depth", 3)
    try:
        max_depth = int(max_depth_raw) if max_depth_raw else None
    except (TypeError, ValueError):
        max_depth = 3

    # ── Index track (HARNESS-30 Phase 3) ─────────────────────
    from app.harness_tools.manual_index import load_runtime_index
    rt_index = load_runtime_index(manual_id)
    if rt_index is not None:
        return rt_index.toc_text(max_depth=max_depth or 3)

    md_text = _read_manual_file(manual_id)
    if md_text is None:
        available = [
            f.stem for f in _scan_manual_files()
        ]
        if available:
            return (
                f"Manual '{manual_id}' not found. "
                f"Available manuals: "
                f"{', '.join(available)}"
            )
        return (
            f"Manual '{manual_id}' not found. "
            f"No manuals are available."
        )

    tree = parse_heading_tree(md_text)
    if not tree:
        return (
            f"Manual '{manual_id}' has no headings. "
            f"The file may be empty or malformed."
        )

    toc = _format_toc_tree(tree, max_depth=max_depth)

    # Append DTC index if present, enriched with the slug of
    # each code's diagnostic section (those sections often sit
    # below max_depth and are otherwise invisible here).
    dtc_index = _extract_dtc_index(md_text)
    if dtc_index:
        dtc_index = _augment_dtc_index(
            dtc_index, _build_dtc_slug_map(md_text),
        )
        toc += (
            "\n\nDTC Quick Index (pass a Section slug to "
            "read_manual_section for the code's diagnostic "
            "procedure):\n" + dtc_index
            + "\n\nIMPORTANT: a row marked "
            "NOT-INDEXED(use search_manual_text) means the code "
            "IS present in the manual (see its occurrence count) "
            "but no section heading names it — locate its content "
            "with search_manual_text. NEVER cite a NOT-INDEXED "
            "mark as evidence the manual lacks the code."
        )

    return toc


# ── Tool: read_manual_section ─────────────────────────────────────


async def read_manual_section(
    input_data: Dict[str, Any],
) -> ToolOutput:
    """Read a specific section from a manual with images.

    Matches the section by slug or heading text, extracts the
    full content, and loads any referenced images as multimodal
    content blocks.

    Args:
        input_data: Must contain ``manual_id`` and ``section``.
            Optional ``include_subsections`` (default True).

    Returns:
        Plain string for text-only sections, or
        ``List[ContentBlock]`` for sections with images.
    """
    manual_id: str = input_data["manual_id"]
    section_query: str = input_data["section"]
    include_subs: bool = input_data.get(
        "include_subsections", True,
    )

    # ── Index track (HARNESS-30 Phase 3) ─────────────────────
    from app.harness_tools.manual_index import load_runtime_index
    rt_index = load_runtime_index(manual_id)
    if rt_index is not None:
        node, candidates = rt_index.resolve(section_query)
        if node is None and candidates:
            listing = "\n".join(
                f"- {c.title}  [{c.node_id}] "
                f"({c.subsystem}/{c.node_type})"
                for c in candidates[:10]
            )
            return (
                f"Section query '{section_query}' is ambiguous "
                f"({len(candidates)} matches). Pick ONE node_id "
                f"and call again:\n{listing}"
            )
        if node is None:
            return (
                f"Section '{section_query}' not found in manual "
                f"'{manual_id}'. Use get_manual_toc for node_ids "
                f"or search_manual_text to locate content."
            )
        section_text = rt_index.section_text(node)
        if section_text is None:
            return (
                f"Node '{node.node_id}' has no content anchor — "
                f"use search_manual_text."
            )
        blocks = build_multimodal_section(
            section_text, rt_index.dir,
        )
        has_images = any(
            b.get("type") == "image_url" for b in blocks
        )
        return blocks if has_images else section_text

    md_text = _read_manual_file(manual_id)
    if md_text is None:
        available = [
            f.stem for f in _scan_manual_files()
        ]
        if available:
            return (
                f"Manual '{manual_id}' not found. "
                f"Available: {', '.join(available)}"
            )
        return f"Manual '{manual_id}' not found."

    tree = parse_heading_tree(md_text)
    flat = _flatten_for_slugs(tree)
    all_slugs = [node.slug for node in flat]

    # Try matching strategies in order.
    target_slug = _match_section(
        section_query, all_slugs,
    )

    if target_slug is None:
        # Build actionable error message.
        closest = find_closest_slug(
            section_query, all_slugs,
        )
        if closest:
            # Find the title for the suggestion.
            title = next(
                (
                    n.title for n in flat
                    if n.slug == closest
                ),
                closest,
            )
            return (
                f"Section '{section_query}' not found "
                f"in manual '{manual_id}'. "
                f"Did you mean: '{title}' "
                f"(slug: {closest})? "
                f"Use get_manual_toc to see all sections."
            )
        return (
            f"Section '{section_query}' not found "
            f"in manual '{manual_id}'. "
            f"Use get_manual_toc to see available "
            f"sections and their slugs."
        )

    section_text = extract_section(
        md_text, target_slug, include_subs,
    )
    if section_text is None:
        return (
            f"Could not extract section '{target_slug}' "
            f"from manual '{manual_id}'."
        )

    # Build multimodal content with images.
    blocks = build_multimodal_section(
        section_text, _MANUAL_DIR,
    )

    # If no images were loaded, return plain string.
    has_images = any(
        b.get("type") == "image_url" for b in blocks
    )
    if not has_images:
        return section_text

    return blocks


def _match_section(
    query: str,
    available_slugs: List[str],
) -> str | None:
    """Try multiple strategies to match a section query.

    1. Exact slug match.
    2. Slugify the query and try again.
    3. Substring match.

    Args:
        query: User-provided section name or slug.
        available_slugs: List of valid slugs.

    Returns:
        Matched slug, or None.
    """
    # Strategy 1: exact match.
    if query in available_slugs:
        return query

    # Strategy 2: slugify and match.
    query_slug = slugify(query)
    if query_slug in available_slugs:
        return query_slug

    # Strategy 3: substring match.
    for slug in available_slugs:
        if query_slug in slug:
            return slug

    return None


def _flatten_for_slugs(
    nodes: List[HeadingNode],
) -> List[HeadingNode]:
    """Flatten heading tree for slug lookups.

    Args:
        nodes: Nested heading tree.

    Returns:
        Flat list of all nodes.
    """
    result: List[HeadingNode] = []
    for node in nodes:
        result.append(node)
        result.extend(
            _flatten_for_slugs(node.children),
        )
    return result


# ── Tool: search_manual_text ──────────────────────────────────────


_HIT_PREVIEW_CHARS = 110


async def search_manual_text(
    input_data: Dict[str, Any],
) -> str:
    """Literal full-text search over one manual (HARNESS-30a).

    Case-insensitive substring search over the manual's markdown
    lines.  Each hit reports the matching line and the slug of its
    enclosing section, so a hit is immediately navigable with
    ``read_manual_section``.

    This is the mandatory absence-check: the agent may only claim
    "the manual does not contain X" after this tool returned zero
    matches for X.  It is deliberately literal (grep), NOT
    semantic retrieval — identifier queries like DTC codes get
    exact answers with no embedding noise (cf. HARNESS-15's
    removal of ``search_manual`` from the manual agent, which
    stays removed).

    Args:
        input_data: ``manual_id``, ``query``, optional
            ``max_hits`` (default 20).

    Returns:
        Formatted hit list with section slugs, or an explicit
        zero-match statement.
    """
    manual_id: str = input_data["manual_id"]
    query: str = input_data["query"]
    max_hits: int = int(input_data.get("max_hits", 20))

    # ── Index track (HARNESS-30 Phase 3): grep the v2 content
    # and attribute hits to index nodes ───────────────────────
    from app.harness_tools.manual_index import load_runtime_index
    rt_index = load_runtime_index(manual_id)
    if rt_index is not None:
        needle = query.casefold()
        # The manual's own TOC pages (dotted-leader lines) match
        # every section title but are never useful evidence —
        # rank real content hits first, listing lines last.
        raw_hits = [
            (idx, line.strip())
            for idx, line in enumerate(rt_index.content_lines)
            if needle in line.casefold()
        ]
        def _low_value(line: str) -> bool:
            # Dotted-leader TOC rows and 參閱 cross-references
            # match every title but carry no content.
            return bool(re.search(r"\.{3,}", line)) \
                or "參閱" in line
        hits = (
            [h for h in raw_hits if not _low_value(h[1])]
            + [h for h in raw_hits if _low_value(h[1])]
        )
        if not hits:
            return (
                f"0 matches for '{query}' in manual "
                f"'{manual_id}'. The manual does not contain "
                f"this text (checked all "
                f"{len(rt_index.content_lines)} lines)."
            )
        shown = hits[:max_hits]
        out_lines = [
            f"{len(hits)} line(s) match '{query}' in manual "
            f"'{manual_id}'"
            + (f" (showing first {len(shown)}):"
               if len(hits) > len(shown) else ":")
        ]
        for idx, text in shown:
            preview = text[:_HIT_PREVIEW_CHARS]
            if len(text) > _HIT_PREVIEW_CHARS:
                preview += "…"
            out_lines.append(
                f"- [node: {rt_index.enclosing_node_id(idx)}] "
                f"{preview}"
            )
        out_lines.append(
            "\nPass a node_id above to read_manual_section "
            "to read the full context of a hit."
        )
        return "\n".join(out_lines)

    md_text = _read_manual_file(manual_id)
    if md_text is None:
        available = [f.stem for f in _scan_manual_files()]
        if available:
            return (
                f"Manual '{manual_id}' not found. "
                f"Available: {', '.join(available)}"
            )
        return f"Manual '{manual_id}' not found."

    lines = md_text.split("\n")
    needle = query.casefold()
    hits = [
        (idx, line.strip())
        for idx, line in enumerate(lines)
        if needle in line.casefold()
    ]

    if not hits:
        return (
            f"0 matches for '{query}' in manual "
            f"'{manual_id}'. The manual does not contain this "
            f"text (checked all {len(lines)} lines)."
        )

    # Map each hit line to its enclosing section slug.  Nodes are
    # flattened in document order, so the enclosing section is the
    # last one starting at or before the hit line.
    flat = _flatten_for_slugs(parse_heading_tree(md_text))
    starts = [(node.line_start, node.slug) for node in flat]

    def _enclosing_slug(line_idx: int) -> str:
        best = "(before first section)"
        for start, slug in starts:
            if start <= line_idx:
                best = slug
            else:
                break
        return best

    shown = hits[:max_hits]
    out: List[str] = [
        f"{len(hits)} line(s) match '{query}' in manual "
        f"'{manual_id}'"
        + (f" (showing first {len(shown)}):"
           if len(hits) > len(shown) else ":")
    ]
    for idx, text in shown:
        preview = text[:_HIT_PREVIEW_CHARS]
        if len(text) > _HIT_PREVIEW_CHARS:
            preview += "…"
        out.append(
            f"- [section: {_enclosing_slug(idx)}] {preview}"
        )
    out.append(
        "\nPass a section slug above to read_manual_section "
        "to read the full context of a hit."
    )
    return "\n".join(out)


# ── ToolDefinition exports ────────────────────────────────────────


LIST_MANUALS_DEF = ToolDefinition(
    name="list_manuals",
    description=(
        "List available service manuals. Returns manual "
        "IDs, vehicle models, page counts, and section "
        "counts. Use vehicle_model to filter for a "
        "specific vehicle. Call this first to discover "
        "what manuals are available before using "
        "get_manual_toc or read_manual_section."
    ),
    input_schema=ListManualsInput.model_json_schema(),
    handler=list_manuals,
    input_model=ListManualsInput,
    is_read_only=True,
)

GET_MANUAL_TOC_DEF = ToolDefinition(
    name="get_manual_toc",
    description=(
        "Get the table of contents (heading structure) "
        "of a specific manual. Returns section titles "
        "with their slugs and a DTC quick-reference "
        "index. Use this to find the right section slug "
        "before calling read_manual_section. Requires "
        "manual_id from list_manuals."
    ),
    input_schema=GetManualTocInput.model_json_schema(),
    handler=get_manual_toc,
    input_model=GetManualTocInput,
    is_read_only=True,
)

SEARCH_MANUAL_TEXT_DEF = ToolDefinition(
    name="search_manual_text",
    description=(
        "Literal (grep-style) full-text search over one manual. "
        "Case-insensitive substring match; each hit shows the "
        "matching line and its enclosing section slug for "
        "read_manual_section. Use for identifier lookups (DTC "
        "codes, part names, spec labels) and ALWAYS before "
        "concluding the manual does not contain something — "
        "'not in the TOC' or a NOT-INDEXED quick-index mark is "
        "NOT evidence of absence; zero matches here is."
    ),
    input_schema=SearchManualTextInput.model_json_schema(),
    handler=search_manual_text,
    input_model=SearchManualTextInput,
    is_read_only=True,
)

READ_MANUAL_SECTION_DEF = ToolDefinition(
    name="read_manual_section",
    description=(
        "Read a specific section from a service manual "
        "by heading slug or title text. Returns the full "
        "section content including any embedded images "
        "(wiring diagrams, exploded views, diagnostic "
        "flowcharts). Use get_manual_toc first to find "
        "section slugs. Accepts both exact slugs "
        "(e.g. '3-2-fuel-system-troubleshooting') and "
        "heading text (e.g. 'Fuel System "
        "Troubleshooting')."
    ),
    input_schema=(
        ReadManualSectionInput.model_json_schema()
    ),
    handler=read_manual_section,
    input_model=ReadManualSectionInput,
    is_read_only=True,
    max_result_chars=100_000,
)
