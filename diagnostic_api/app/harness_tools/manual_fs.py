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

# Marker-pdf occasionally promotes a table row of dense spec text
# to an ``###`` heading.  Real headings are short.  Anything past
# this length is treated as a misclassified paragraph and skipped.
_MAX_HEADING_CHARS = 150
# Marker also promotes numbered procedure steps (``2. 檢查:``,
# ``5. 安裝:``) to deep ``####`` headings.  These have a numeric
# prefix, optional Chinese/English label, and often a trailing
# colon.  Section numbers like ``3.2 Fuel System`` look similar
# but tend to have a multi-part numeric prefix and a longer
# semantic title — the dot+digit-only pattern below avoids them.
_PROCEDURE_STEP_RE = re.compile(
    r"^\s*\d+\.\s+\S{1,40}:?\s*$"
)
# Marker also renders the manual's 注意/警告 callout boxes as
# headings (``### 注 意`` / ``## 警 告``).  They are banners, not
# sections: as level-2/3 "headings" they arbitrarily slice real
# sections' bodies (#186: the promoted bleed-procedure title had an
# EMPTY body because a ``## 警 告`` banner two lines later
# terminated it).  Filtering them lets sections span to the next
# real heading — strictly more complete section text.
_WARNING_BANNER_RE = re.compile(
    r"^\s*(?:注\s*意|警\s*告)\s*$"
)


def _is_real_heading(title: str) -> bool:
    """Filter out marker-pdf misclassified headings.

    Returns ``False`` for:

    * Oversized titles (likely table rows promoted to headings).
    * Numbered procedure steps (``2. 檢查:`` / ``5. 安裝:``)
      that should be list items, not headings.
    * Empty / whitespace-only titles.

    Returns ``True`` otherwise.

    Args:
        title: Heading text after ``HEADING_PATTERN`` extraction.
    """
    stripped = title.strip()
    if not stripped:
        return False
    if len(stripped) > _MAX_HEADING_CHARS:
        return False
    if _PROCEDURE_STEP_RE.match(stripped):
        return False
    if _WARNING_BANNER_RE.match(stripped):
        return False
    return True

# Defensive strip for any empty HTML elements that may linger in
# older .md files on disk — marker-pdf emits page-anchor spans
# like ``<span id="page-4-0"></span>`` that are useless to AI
# consumers.  New uploads come pre-stripped at conversion time
# (see ``scripts/marker_convert.py``); this regex catches any
# legacy artefacts so the agent never sees HTML noise.
_EMPTY_HTML_TAG_RE = re.compile(
    r"<([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>\s*</\1\s*>",
    re.IGNORECASE,
)


def _clean_md(text: str) -> str:
    """Strip empty HTML elements from raw manual markdown.

    Run twice to handle nested empties.  Any reader that returns
    section text to an LLM should pass it through this filter.
    """
    cleaned = _EMPTY_HTML_TAG_RE.sub("", text)
    return _EMPTY_HTML_TAG_RE.sub("", cleaned)


# ── Unheaded-title promotion (HARNESS-24 WP2, #186) ──────────────
#
# Marker-pdf occasionally fails to render a styled section title as
# a markdown heading, emitting it as a bare line prefixed only by
# page-anchor spans, e.g. (source line 2466 of the Yamaha manual):
#
#     <span id="page-91-4"></span><span id="page-91-2"></span>\
#     液壓煞車系統空氣的釋放
#
# Such titles are invisible to ``parse_heading_tree`` (and thus to
# the agent's TOC at ANY depth) and unciteable by slug — the golden
# slug ``液壓煞車系統空氣的釋放`` had no navigable target.
# HARNESS-17 (#101) fixed the same quirk frontend-side; this brings
# the fix to the agent by rewriting qualifying lines into real
# headings BEFORE the page-anchor spans are stripped by
# ``_clean_md`` (order matters: the spans ARE the detection signal).

_SPAN_ANCHORED_TITLE_RE = re.compile(
    r'^(?:<span id="page-[^"]+"></span>\s*)+'
    r"(?P<title>[^<>|]+?)\s*$",
)
"""One or more page-anchor spans followed ONLY by bare text.
``[^<>|]`` excludes further markup and table rows — the manual's
own index table lists the same titles as ``| <title> | 3-13 |``
rows, which must NOT be promoted."""

_MAX_PROMOTED_TITLE_CHARS = 40
"""Real unheaded titles are short.  Longer span-prefixed lines are
body prose that happens to start a page — never promote those."""

_PROMOTED_HEADING_PREFIX = "### "
"""Fixed level 3: visible at ``get_manual_toc``'s default
``max_depth=3`` (a ``####`` promotion would stay hidden — the
exact failure being fixed), while still nesting under the ``#`` /
``##`` chapter headings.  Parent reads with
``include_subsections=true`` keep covering promoted titles."""


def _promotable_title(line: str) -> Optional[str]:
    """Return the bare title when ``line`` is an unheaded section
    title, else ``None``.

    Guardrails (mirrors HARNESS-17's frontend heuristics):

    * must be a span-anchored bare line (see
      ``_SPAN_ANCHORED_TITLE_RE``); table rows and lines with any
      further markup never match;
    * short (``<= _MAX_PROMOTED_TITLE_CHARS``) and contains at
      least one word character;
    * not a numbered procedure step (``_PROCEDURE_STEP_RE``) and
      not a list/image/link line.

    Args:
        line: One raw (pre-``_clean_md``) markdown line.

    Returns:
        The title text to promote, or ``None``.
    """
    if line.startswith("#"):
        return None
    match = _SPAN_ANCHORED_TITLE_RE.match(line)
    if not match:
        return None
    title = match.group("title").strip()
    if not title or len(title) > _MAX_PROMOTED_TITLE_CHARS:
        return None
    if not re.search(r"\w", title, re.UNICODE):
        return None
    if _PROCEDURE_STEP_RE.match(title):
        return None
    if _WARNING_BANNER_RE.match(title):
        return None
    if title[0] in "-*•![(":
        return None
    # Instruction sentences, not titles (live census, #186):
    # lettered sub-steps ("a. 用數位三用電錶量測…"), tool-callout
    # sentences with quoted part numbers (以轉向螺帽扳手 "3" 拆卸…),
    # and imperative sentences ending with a CJK full stop.
    if re.match(r"^[a-zA-Z][.、]", title):
        return None
    if '"' in title:
        return None
    if title.endswith("。"):
        return None
    return title


def promote_unheaded_titles(md_text: str) -> str:
    """Rewrite span-anchored bare-title lines into real headings.

    Must run on RAW manual markdown, BEFORE ``_clean_md`` strips
    the page-anchor spans that identify the pattern.  Idempotent —
    promoted lines start with ``#`` and are skipped on re-runs.

    Args:
        md_text: Raw manual markdown.

    Returns:
        Markdown with qualifying titles promoted to ``####``
        headings (anchors dropped; ``slugify`` then produces the
        natural slug, e.g. ``液壓煞車系統空氣的釋放``).
    """
    out: List[str] = []
    promoted = 0
    for line in md_text.split("\n"):
        title = _promotable_title(line)
        if title is not None:
            out.append(f"{_PROMOTED_HEADING_PREFIX}{title}")
            promoted += 1
        else:
            out.append(line)
    if promoted:
        logger.debug(
            "unheaded_titles_promoted", count=promoted,
        )
    return "\n".join(out)


_IMAGE_REF_PATTERN = re.compile(
    r"!\[([^\]]*)\]\(([^)]+)\)",
)
_HEADING_PATTERN = re.compile(
    r"^(#{1,6})\s+(.+)$", re.MULTILINE,
)

# ── Caption-stub demotion (HARNESS-24, #195) ──────────────────────
#
# Marker-pdf sometimes renders a figure CAPTION as a heading (e.g.
# ``### 前煞車`` directly over the front-brake photo on page 142).
# The stub grabs the canonical slug; the REAL chapter one page
# later is deduped to ``前煞車-2``, so any agent or golden citing
# the natural title lands on a ~136-char stub.  A heading whose
# body — measured to the next raw heading of ANY level — contains
# no prose beyond image references, optional ``*Vision
# description:`` paragraphs, page-break markers, and whitespace is
# a caption, not a section: it is dropped from the heading tree so
# the real chapter keeps the canonical slug.  The caption's lines
# (heading text and image included) remain in the markdown, so the
# preceding surviving section's span extends over them — the image
# stays readable via the parent.

_PAGE_BREAK_MARKER_RE = re.compile(
    r"^(?:<!--\s*page:\s*\d+\s*-->|\{\d+\}-{4,})$"
)
"""Page-boundary furniture: schema-style ``<!-- page:42 -->``
comments and marker-pdf ``paginate_output`` separators
(``{142}------``)."""

_VISION_DESC_START_RE = re.compile(
    r"^\*Vision description:"
)
"""Start of a vision-generated figure description.  The italic
paragraph may wrap across lines until one ending with ``*``."""


def _is_caption_stub(body_lines: List[str]) -> bool:
    """Return True when a heading's body is only figure furniture.

    A caption stub must contain at least one image reference and
    nothing else except blank lines, page-break markers,
    ``*Vision description: ...*`` paragraphs, and page-anchor
    spans (stripped per line via ``_clean_md``).  Any other
    non-empty line is prose and disqualifies the heading from
    demotion.  A body with no image at all is never a caption
    (e.g. a chapter heading directly followed by its first
    subsection).

    Args:
        body_lines: Raw markdown lines strictly between the
            heading line and the next raw heading of any level.

    Returns:
        True when the body is image-only caption furniture.
    """
    has_image = False
    in_vision_desc = False
    for raw_line in body_lines:
        line = _clean_md(raw_line).strip()
        if not line:
            continue
        if in_vision_desc:
            if line.endswith("*"):
                in_vision_desc = False
            continue
        if _PAGE_BREAK_MARKER_RE.match(line):
            continue
        if _VISION_DESC_START_RE.match(line):
            if not line.endswith("*"):
                in_vision_desc = True
            continue
        if _IMAGE_REF_PATTERN.search(line):
            residue = _IMAGE_REF_PATTERN.sub(
                "", line,
            ).strip()
            if residue:
                return False
            has_image = True
            continue
        return False
    return has_image


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
    """Convert heading text to a stable, agent-friendly slug.

    Pipeline:

    1. Lowercase ASCII.
    2. Keep ASCII alphanumerics, hyphens, AND CJK ideographs
       (``⺀-鿿``, ``豈-﫿``).  CJK characters
       carry their own meaning and don't need to be transliterated
       — preserving them keeps slugs mnemonic for Chinese / Japanese
       / Korean manuals (e.g. ``電裝系統`` stays addressable as
       itself rather than collapsing to ``""`` and being
       autosuffixed ``-N``).
    3. Replace anything else with a single hyphen.
    4. Strip leading/trailing hyphens.
    5. Truncate to ``_SLUG_MAX_LEN`` chars at a hyphen boundary
       when possible.

    Slugs are passed as JSON in tool inputs and rendered in
    citations like ``doc_id#slug`` — neither path needs URL
    encoding, so unicode is fine.

    Does NOT handle duplicate suffixes (``-2``, ``-3``) — that
    is done by ``parse_heading_tree`` which tracks all slugs.

    Args:
        title: Heading text (without ``#`` prefix).

    Returns:
        Slug string, possibly containing CJK characters.
    """
    slug = title.lower()
    # Keep ASCII a-z0-9, hyphens, and the main CJK Unicode blocks.
    slug = re.sub(
        r"[^a-z0-9⺀-鿿豈-﫿-]+",
        "-",
        slug,
    )
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

    Walks all headings (``#`` through ``######``), demotes
    caption-stub headings (#195), assigns slugs with duplicate
    suffixes (``-2``, ``-3``), and computes line ranges for each
    section.

    Args:
        md_text: Full markdown file content.

    Returns:
        Top-level heading nodes with nested children.
    """
    lines = md_text.split("\n")
    # Collect raw heading positions.  Skip marker-pdf
    # misclassified pseudo-headings (oversized table rows,
    # numbered procedure steps) so they don't pollute the TOC.
    raw_headings: List[Tuple[int, int, str]] = []
    for line_idx, line in enumerate(lines):
        match = _HEADING_PATTERN.match(line)
        if match:
            level = len(match.group(1))
            title = match.group(2).strip()
            if not _is_real_heading(title):
                continue
            raw_headings.append((line_idx, level, title))

    if not raw_headings:
        return []

    # Second pass (HARNESS-24, #195): demote caption-stub
    # headings.  Needs the section BODY (to the next raw heading
    # of ANY level), so it runs after collection but BEFORE slug
    # assignment — otherwise the real chapter would still get the
    # ``-2`` suffix.  A heading directly followed by a DEEPER
    # heading is a structural parent whose own body is legally
    # empty or figure-only; demoting it would re-parent its
    # children, so it is always kept.
    kept: List[Tuple[int, int, str]] = []
    demoted = 0
    for i, (line_idx, level, title) in enumerate(
        raw_headings,
    ):
        if i + 1 < len(raw_headings):
            next_line, next_level, _ = raw_headings[i + 1]
        else:
            next_line, next_level = len(lines), 0
        is_parent = next_level > level
        if not is_parent and _is_caption_stub(
            lines[line_idx + 1:next_line],
        ):
            demoted += 1
            logger.debug(
                "caption_stub_demoted", title=title,
            )
            continue
        kept.append((line_idx, level, title))
    if demoted:
        logger.debug(
            "caption_stubs_demoted", count=demoted,
        )
    if not kept:
        return []

    # Assign slugs with deduplication.
    slug_counts: Dict[str, int] = {}
    nodes: List[HeadingNode] = []
    for i, (line_idx, level, title) in enumerate(
        kept,
    ):
        base_slug = slugify(title)
        count = slug_counts.get(base_slug, 0)
        if count == 0:
            slug = base_slug
        else:
            slug = f"{base_slug}-{count + 1}"
        slug_counts[base_slug] = count + 1

        # line_end: up to the next surviving heading or EOF, so
        # a demoted caption's lines (image included) stay inside
        # the preceding section's span.
        if i + 1 < len(kept):
            line_end = kept[i + 1][0]
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
    return _clean_md("\n".join(section_lines).rstrip())


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
