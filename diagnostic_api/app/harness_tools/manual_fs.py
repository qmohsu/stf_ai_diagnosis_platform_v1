"""Filesystem helpers for structured manual navigation.

Pure functions for parsing manual markdown files, building heading
trees, extracting sections, and loading images as multimodal
content blocks.  Used by the ``manual_tools`` handlers.

The markdown format follows ``docs/manual_markdown_schema.md``:
  - YAML frontmatter between ``---`` markers
  - Headings at levels ``#`` through ``####``
  - Images as ``![alt](images/{stem}/p{page}-{idx}.png)``
  - Optional ``*Vision description: ...`` after images
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import structlog
import yaml

logger = structlog.get_logger(__name__)

_SLUG_MAX_LEN = 80
_IMAGE_MAX_BYTES = 5 * 1024 * 1024  # 5 MB per image.
_IMAGE_REF_PATTERN = re.compile(
    r"!\[([^\]]*)\]\(([^)]+)\)",
)
_HEADING_PATTERN = re.compile(
    r"^(#{1,6})\s+(.+)$", re.MULTILINE,
)


# ── Data models ───────────────────────────────────────────────────


@dataclass
class HeadingNode:
    """A node in the manual heading tree.

    Attributes:
        title: Raw heading text (without ``#`` prefix).
        slug: URL-safe anchor (computed via ``slugify``).
        level: Heading depth (1 = ``#``, 2 = ``##``, etc.).
        line_start: 0-based line index of the heading.
        line_end: 0-based line index of the last content
            line before the next same-or-higher-level heading
            (exclusive).
        children: Direct child headings.
    """

    title: str
    slug: str
    level: int
    line_start: int
    line_end: int
    children: List[HeadingNode] = field(
        default_factory=list,
    )


# ── Slug generation ──────────────────────────────────────────────


def slugify(title: str) -> str:
    """Convert heading text to a URL-safe slug.

    Implements the algorithm from ``manual_markdown_schema.md``
    section 4.1:

    1. Lowercase.
    2. Replace runs of non-alphanumeric characters (except
       hyphens) with a single hyphen.
    3. Strip leading/trailing hyphens.
    4. Truncate to 80 characters at a hyphen boundary.

    Does NOT handle duplicate suffixes (``-2``, ``-3``) — that
    is done by ``parse_heading_tree`` which tracks all slugs.

    Args:
        title: Heading text (without ``#`` prefix).

    Returns:
        URL-safe slug string.
    """
    slug = title.lower()
    slug = re.sub(r"[^a-z0-9-]+", "-", slug)
    slug = slug.strip("-")
    if len(slug) > _SLUG_MAX_LEN:
        truncated = slug[:_SLUG_MAX_LEN]
        last_hyphen = truncated.rfind("-")
        if last_hyphen > 0:
            slug = truncated[:last_hyphen]
        else:
            slug = truncated
    return slug


# ── Frontmatter parsing ──────────────────────────────────────────


def parse_frontmatter(md_text: str) -> Dict[str, Any]:
    """Extract YAML frontmatter from a markdown string.

    Looks for content between the first pair of ``---`` markers
    at the start of the file.

    Args:
        md_text: Full markdown file content.

    Returns:
        Parsed frontmatter dict, or empty dict if none found.
    """
    stripped = md_text.lstrip()
    if not stripped.startswith("---"):
        return {}
    # Find the closing marker.
    end_idx = stripped.find("---", 3)
    if end_idx < 0:
        return {}
    yaml_block = stripped[3:end_idx]
    try:
        parsed = yaml.safe_load(yaml_block)
        if isinstance(parsed, dict):
            return parsed
        return {}
    except yaml.YAMLError:
        logger.warning(
            "frontmatter_parse_error",
            preview=yaml_block[:100],
        )
        return {}


# ── Heading tree ──────────────────────────────────────────────────


def parse_heading_tree(
    md_text: str,
) -> List[HeadingNode]:
    """Build a hierarchical heading tree from markdown text.

    Walks all headings (``#`` through ``######``), assigns slugs
    with duplicate suffixes (``-2``, ``-3``), and computes line
    ranges for each section.

    Args:
        md_text: Full markdown file content.

    Returns:
        Top-level heading nodes with nested children.
    """
    lines = md_text.split("\n")
    # Collect raw heading positions.
    raw_headings: List[Tuple[int, int, str]] = []
    for line_idx, line in enumerate(lines):
        match = _HEADING_PATTERN.match(line)
        if match:
            level = len(match.group(1))
            title = match.group(2).strip()
            raw_headings.append((line_idx, level, title))

    if not raw_headings:
        return []

    # Assign slugs with deduplication.
    slug_counts: Dict[str, int] = {}
    nodes: List[HeadingNode] = []
    for i, (line_idx, level, title) in enumerate(
        raw_headings,
    ):
        base_slug = slugify(title)
        count = slug_counts.get(base_slug, 0)
        if count == 0:
            slug = base_slug
        else:
            slug = f"{base_slug}-{count + 1}"
        slug_counts[base_slug] = count + 1

        # line_end: up to the next heading or EOF.
        if i + 1 < len(raw_headings):
            line_end = raw_headings[i + 1][0]
        else:
            line_end = len(lines)

        nodes.append(HeadingNode(
            title=title,
            slug=slug,
            level=level,
            line_start=line_idx,
            line_end=line_end,
        ))

    # Build hierarchy: nest children under parents.
    return _nest_headings(nodes)


def _nest_headings(
    flat: List[HeadingNode],
) -> List[HeadingNode]:
    """Convert a flat heading list into a nested tree.

    A heading becomes a child of the nearest preceding heading
    with a smaller level number.

    Args:
        flat: Flat list of heading nodes sorted by line_start.

    Returns:
        List of top-level nodes with children populated.
    """
    if not flat:
        return []

    root: List[HeadingNode] = []
    stack: List[HeadingNode] = []

    for node in flat:
        # Pop stack until we find a parent (lower level).
        while stack and stack[-1].level >= node.level:
            stack.pop()
        if stack:
            stack[-1].children.append(node)
        else:
            root.append(node)
        stack.append(node)

    return root


# ── Section extraction ────────────────────────────────────────────


def extract_section(
    md_text: str,
    target_slug: str,
    include_subsections: bool = True,
) -> Optional[str]:
    """Extract section content by slug from markdown text.

    Args:
        md_text: Full markdown file content.
        target_slug: Slug to match (from ``parse_heading_tree``).
        include_subsections: If True, includes all content until
            the next heading at the same or higher level.  If
            False, stops at the first child heading.

    Returns:
        Section text (including the heading line), or None if
        the slug is not found.
    """
    lines = md_text.split("\n")
    flat_nodes = _flatten_tree(parse_heading_tree(md_text))

    target_node = None
    target_idx = -1
    for i, node in enumerate(flat_nodes):
        if node.slug == target_slug:
            target_node = node
            target_idx = i
            break

    if target_node is None:
        return None

    if include_subsections:
        end_line = target_node.line_end
        # Extend to cover all child line ranges.
        for j in range(target_idx + 1, len(flat_nodes)):
            child = flat_nodes[j]
            if child.level <= target_node.level:
                break
            end_line = max(end_line, child.line_end)
    else:
        # Stop at first child heading.
        end_line = target_node.line_end
        for j in range(target_idx + 1, len(flat_nodes)):
            child = flat_nodes[j]
            if child.level > target_node.level:
                end_line = child.line_start
                break

    section_lines = lines[target_node.line_start:end_line]
    return "\n".join(section_lines).rstrip()


def _flatten_tree(
    nodes: List[HeadingNode],
) -> List[HeadingNode]:
    """Flatten a nested heading tree to a sorted list.

    Args:
        nodes: Nested heading tree (from ``parse_heading_tree``).

    Returns:
        Flat list sorted by ``line_start``.
    """
    result: List[HeadingNode] = []
    for node in nodes:
        result.append(node)
        result.extend(_flatten_tree(node.children))
    return sorted(result, key=lambda n: n.line_start)


# ── Slug matching ─────────────────────────────────────────────────


def find_closest_slug(
    query: str,
    available: List[str],
    threshold: float = 0.4,
) -> Optional[str]:
    """Find the closest slug match for a query string.

    Tries substring match first, then falls back to
    SequenceMatcher similarity.

    Args:
        query: User-provided section name or slug.
        available: List of valid slugs.
        threshold: Minimum similarity ratio (0.0 to 1.0).

    Returns:
        Best matching slug, or None if nothing is close
        enough.
    """
    if not available:
        return None

    query_slug = slugify(query)

    # Exact match.
    if query_slug in available:
        return query_slug

    # Substring match.
    for slug in available:
        if query_slug in slug or slug in query_slug:
            return slug

    # Similarity match.
    best_slug = None
    best_ratio = 0.0
    for slug in available:
        ratio = SequenceMatcher(
            None, query_slug, slug,
        ).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_slug = slug

    if best_ratio >= threshold:
        return best_slug
    return None


# ── Image resolution and loading ──────────────────────────────────


def resolve_image_refs(
    section_text: str,
    manual_dir: Path,
) -> List[Tuple[str, Path]]:
    """Find image references in section text and resolve paths.

    Matches ``![alt](path)`` patterns and resolves each path
    relative to ``manual_dir``.

    Args:
        section_text: Markdown section content.
        manual_dir: Base directory containing the manual file
            (e.g. ``/app/data/manuals``).

    Returns:
        List of ``(full_markdown_ref, absolute_path)`` tuples.
        Only includes references whose files exist on disk.
    """
    refs: List[Tuple[str, Path]] = []
    for match in _IMAGE_REF_PATTERN.finditer(section_text):
        full_ref = match.group(0)
        rel_path = match.group(2)
        abs_path = (manual_dir / rel_path).resolve()
        # Security: ensure path stays under manual_dir.
        try:
            abs_path.relative_to(manual_dir.resolve())
        except ValueError:
            logger.warning(
                "image_path_traversal_blocked",
                rel_path=rel_path,
            )
            continue
        if abs_path.is_file():
            refs.append((full_ref, abs_path))
    return refs


def load_image_as_content_block(
    image_path: Path,
) -> Optional[Dict[str, Any]]:
    """Load a PNG image from disk as an OpenAI content block.

    Args:
        image_path: Absolute path to the image file.

    Returns:
        Content block dict with ``type: "image_url"`` and
        base64-encoded data URI, or None if the file cannot
        be read or exceeds the size limit.
    """
    if not image_path.is_file():
        logger.warning(
            "image_not_found",
            path=str(image_path),
        )
        return None

    file_size = image_path.stat().st_size
    if file_size > _IMAGE_MAX_BYTES:
        logger.warning(
            "image_too_large",
            path=str(image_path),
            size_bytes=file_size,
            max_bytes=_IMAGE_MAX_BYTES,
        )
        return None

    with open(image_path, "rb") as f:
        data = f.read()

    encoded = base64.b64encode(data).decode("ascii")

    # Detect MIME type from extension.
    suffix = image_path.suffix.lower()
    mime_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    mime_type = mime_map.get(suffix, "image/png")

    return {
        "type": "image_url",
        "image_url": {
            "url": f"data:{mime_type};base64,{encoded}",
        },
    }


# ── Multimodal section builder ────────────────────────────────────


def build_multimodal_section(
    section_text: str,
    manual_dir: Path,
) -> List[Dict[str, Any]]:
    """Convert a markdown section to multimodal content blocks.

    Splits the section text at image references, loads each
    referenced image from disk, and interleaves text and image
    content blocks.  If an image cannot be loaded, the markdown
    reference is kept as-is in the text.

    Args:
        section_text: Markdown section content (from
            ``extract_section``).
        manual_dir: Base directory containing the manual file.

    Returns:
        List of content blocks: ``{"type": "text", ...}`` and
        ``{"type": "image_url", ...}``.
    """
    image_refs = resolve_image_refs(
        section_text, manual_dir,
    )

    if not image_refs:
        return [{"type": "text", "text": section_text}]

    blocks: List[Dict[str, Any]] = []
    remaining = section_text

    for full_ref, abs_path in image_refs:
        idx = remaining.find(full_ref)
        if idx < 0:
            continue

        # Text before this image.
        before = remaining[:idx].rstrip()
        if before:
            blocks.append({
                "type": "text",
                "text": before,
            })

        # Load and insert the image.
        image_block = load_image_as_content_block(abs_path)
        if image_block is not None:
            blocks.append(image_block)

        # Advance past the image reference.
        remaining = remaining[idx + len(full_ref):]

    # Any trailing text after the last image.
    trailing = remaining.strip()
    if trailing:
        blocks.append({"type": "text", "text": trailing})

    # Safety: if no blocks were produced, return text-only.
    if not blocks:
        return [{"type": "text", "text": section_text}]

    return blocks
