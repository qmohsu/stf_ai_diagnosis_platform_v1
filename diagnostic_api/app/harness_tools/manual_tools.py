"""Manual filesystem navigation tools for the harness agent loop.

Three tools for structured manual retrieval:
  - ``list_manuals``: discover available manuals
  - ``get_manual_toc``: read a manual's heading structure
  - ``read_manual_section``: read a full section with images

These complement ``search_manual`` (semantic RAG search) with
precise filesystem-based navigation.
"""

from __future__ import annotations

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
)
from app.harness_tools.manual_fs import (
    HeadingNode,
    build_multimodal_section,
    extract_section,
    find_closest_slug,
    parse_frontmatter,
    parse_heading_tree,
    slugify,
)

logger = structlog.get_logger(__name__)

_MANUAL_DIR = Path(settings.manual_storage_path)

# Directories to skip when scanning for .md files.
_SKIP_DIRS = {"images", "uploads", ".queue"}


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
            return md_file.read_text(encoding="utf-8")
    return None


def _format_toc_tree(
    nodes: List[HeadingNode],
    indent: int = 0,
) -> str:
    """Format a heading tree as indented text.

    Args:
        nodes: Heading nodes to format.
        indent: Current indentation level.

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
        if node.children:
            lines.append(
                _format_toc_tree(
                    node.children, indent + 1,
                ),
            )
    return "\n".join(lines)


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

        if vehicle_filter:
            if model.lower() != vehicle_filter.lower():
                continue

        page_count = fm.get("page_count", "?")
        section_count = fm.get("section_count", "?")
        entries.append(
            f"- {md_file.stem}  "
            f"model={model}  "
            f"pages={page_count}  "
            f"sections={section_count}"
        )

    if not entries:
        if vehicle_filter:
            return (
                f"No manuals found for vehicle model "
                f"'{vehicle_filter}'. Use list_manuals "
                f"without a filter to see all available "
                f"manuals."
            )
        return "No manuals found in storage."

    header = f"Available manuals ({len(entries)}):\n"
    return header + "\n".join(entries)


# ── Tool: get_manual_toc ──────────────────────────────────────────


async def get_manual_toc(
    input_data: Dict[str, Any],
) -> str:
    """Get the table of contents for a manual.

    Parses the heading structure and returns a hierarchical
    tree with section slugs for use with ``read_manual_section``.

    Args:
        input_data: Must contain ``manual_id`` (str).

    Returns:
        Indented heading tree with slugs and optional DTC index.
    """
    manual_id: str = input_data["manual_id"]

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

    toc = _format_toc_tree(tree)

    # Append DTC index if present.
    dtc_index = _extract_dtc_index(md_text)
    if dtc_index:
        toc += "\n\nDTC Quick Index:\n" + dtc_index

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
