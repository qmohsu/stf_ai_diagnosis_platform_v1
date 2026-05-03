"""Document parser for manuals and maintenance logs.

Splits documents on markdown headings, extracts DTC codes and vehicle models.
"""

import re
from pathlib import Path
from typing import List, Optional, Tuple

from pydantic import BaseModel

# Regex patterns
DTC_PATTERN = re.compile(r"\b[PBCU]\d{4}\b")
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)

# Marker-pdf embeds HTML page-anchor spans inside headings AND
# body text for the manual viewer (``<span id="page-281-1"></span>``).
# These are pure navigation metadata — useless for retrieval, they
# bloat both heading length and chunk text, and they pollute the
# embedding vector with HTML noise.  Strip them everywhere.
#
# Pattern catches any empty HTML element with optional attributes:
#   <tagname>...</tagname> where the contents are pure whitespace.
# Backreference enforces matching open/close tag names.
_EMPTY_HTML_TAG_RE = re.compile(
    r"<([a-zA-Z][a-zA-Z0-9]*)\b[^>]*>\s*</\1\s*>",
    re.IGNORECASE,
)
# Markdown emphasis markers wrapping an entire heading
# (``**TRICITY155 MWS150-A**``).  These are visual styling, not
# semantic content; strip from titles only.  Body text keeps its
# emphasis because inline ``**bold**`` there is real signal.
_TITLE_EMPHASIS_PREFIX_RE = re.compile(r"^\s*[*_]+")
_TITLE_EMPHASIS_SUFFIX_RE = re.compile(r"[*_]+\s*$")
# Belt-and-braces hard cap: a single section title should never
# need more than this; truncate pathological inputs.
_MAX_SECTION_TITLE_CHARS = 2000


def _strip_empty_html(text: str) -> str:
    """Remove empty HTML elements from arbitrary text.

    Catches ``<span id="x"></span>``, ``<a id="y"></a>``,
    ``<div></div>``, etc. — any tag with optional attributes
    that wraps no visible content.  Content-bearing tags
    (e.g. ``<span>kept</span>``) are left intact.

    Args:
        text: Raw text possibly containing empty HTML tags.

    Returns:
        Text with empty HTML elements removed.
    """
    # Run twice in case empty tags were nested
    # (``<span><a></a></span>``).  Two passes is enough for any
    # plausible nesting depth marker would emit.
    cleaned = _EMPTY_HTML_TAG_RE.sub("", text)
    cleaned = _EMPTY_HTML_TAG_RE.sub("", cleaned)
    return cleaned


def _clean_section_title(raw: str) -> str:
    """Clean a heading captured by ``HEADING_PATTERN``.

    Pipeline:

    1. Strip empty HTML elements (page anchors etc.)
    2. Strip leading/trailing markdown emphasis markers
       (``**``, ``__``, ``*``, ``_``) wrapping the whole title.
    3. Collapse whitespace runs to single spaces.
    4. Cap length at ``_MAX_SECTION_TITLE_CHARS``.

    Args:
        raw: Heading text as captured by ``HEADING_PATTERN``.

    Returns:
        Cleaned title — safe for storage in
        ``rag_chunks.section_title`` and useful as retrieval
        context.
    """
    cleaned = _strip_empty_html(raw)
    cleaned = _TITLE_EMPHASIS_PREFIX_RE.sub("", cleaned)
    cleaned = _TITLE_EMPHASIS_SUFFIX_RE.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > _MAX_SECTION_TITLE_CHARS:
        cleaned = cleaned[:_MAX_SECTION_TITLE_CHARS]
    return cleaned

# YAML frontmatter delimiter pattern.  Marker-pdf-produced manuals
# begin with a fenced ``---`` block; we strip it before heading
# extraction so it does not become a phantom first chunk.
_FRONTMATTER_RE = re.compile(
    r"\A---\s*\r?\n(?P<body>.*?)\r?\n---\s*\r?\n",
    re.DOTALL,
)
# Flat key/value within frontmatter (one line each).  Skips list /
# nested YAML — our schema is intentionally flat.
_FRONTMATTER_KV_RE = re.compile(
    r"^([a-zA-Z_][a-zA-Z0-9_]*):\s*(.*?)\s*$",
    re.MULTILINE,
)


def _strip_yaml_frontmatter(text: str) -> Tuple[dict, str]:
    """Extract and strip a leading YAML frontmatter block.

    Marker-pdf-produced manuals begin with a fenced ``---`` block
    containing ``source_pdf``, ``vehicle_model``, ``language`` and
    similar metadata.  Without stripping, the parser would treat
    the block as content and emit a junk first chunk.

    Args:
        text: Full document text (possibly with frontmatter).

    Returns:
        Tuple of ``(frontmatter_dict, body)``.  When no frontmatter
        is present, returns ``({}, text)`` unchanged.
    """
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return {}, text

    fm: dict = {}
    for key, value in _FRONTMATTER_KV_RE.findall(match.group("body")):
        # Strip surrounding ASCII quotes if present.
        v = value.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        fm[key] = v

    return fm, text[match.end():]

# Vehicle model patterns: (compiled regex, normalization format).
# Format placeholders: {raw} = matched text as-is,
#                      {digits} = first digit group extracted.
# Evaluated in order; first match wins.
VEHICLE_MODEL_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (
        re.compile(r"\bSTF[-\s]?\d{3,4}\b", re.IGNORECASE),
        "STF-{digits}",
    ),
    (
        re.compile(
            r"\bMWS[-\s]?\d{2,4}[-\s]?[A-Z]?\b", re.IGNORECASE,
        ),
        "{raw}",
    ),
    (
        re.compile(r"\bTRICITY\s*\d{2,3}\b", re.IGNORECASE),
        "{raw}",
    ),
    (
        re.compile(r"\bNMAX\s*\d{2,3}\b", re.IGNORECASE),
        "{raw}",
    ),
    (
        re.compile(r"\bXMAX\s*\d{2,3}\b", re.IGNORECASE),
        "{raw}",
    ),
]


class Section(BaseModel):
    """A parsed section of a document."""

    title: str
    level: int  # heading level (1-6), 0 for root/fallback
    body: str
    vehicle_model: str = "Generic"
    dtc_codes: List[str] = []


def _extract_dtc_codes(text: str) -> List[str]:
    """Extract unique DTC codes from text."""
    return sorted(set(DTC_PATTERN.findall(text)))


def extract_vehicle_model(text: str) -> str:
    """Extract and normalize a vehicle model identifier from text.

    Tries each pattern in ``VEHICLE_MODEL_PATTERNS`` in order and
    returns the first match, normalized according to its format
    string.  Returns ``"Generic"`` if no pattern matches.

    Args:
        text: Text to search (may include filename, section body,
            or title).

    Returns:
        Normalized vehicle model string, or ``"Generic"``.
    """
    for pattern, fmt in VEHICLE_MODEL_PATTERNS:
        match = pattern.search(text)
        if match:
            raw = match.group().strip()
            # Normalize whitespace/dashes
            raw = re.sub(r"[-\s]+", "-", raw).upper()
            digits_match = re.search(r"\d{2,4}", raw)
            digits = digits_match.group() if digits_match else ""
            return fmt.format(raw=raw, digits=digits)
    return "Generic"


def parse_manual(text: str, filename: str = "") -> List[Section]:
    """Parse a manual document by splitting on markdown headings.

    Splits on ## and ### headings, preserving hierarchy.
    Extracts DTC codes and vehicle model from each section
    and from the document header.

    Args:
        text: Full document text.
        filename: Original filename (used as fallback title).

    Returns:
        List of Section objects.
    """
    # Strip YAML frontmatter so it doesn't pollute heading extraction
    # or become a phantom first chunk.  The frontmatter's metadata
    # (especially ``vehicle_model``) is treated as a high-priority
    # source for the doc-level vehicle model when the body regex
    # comes up empty.
    frontmatter, text = _strip_yaml_frontmatter(text)

    # Document-level vehicle model resolution priority:
    #   1. body regex match (specific OEM patterns)
    #   2. frontmatter ``vehicle_model`` field (if it isn't itself
    #      ``"Generic"`` or an obviously non-model placeholder).
    doc_vehicle_model = extract_vehicle_model(text)
    if doc_vehicle_model == "Generic":
        fm_model = frontmatter.get("vehicle_model", "").strip()
        if fm_model and fm_model.lower() != "generic":
            doc_vehicle_model = fm_model

    headings = list(HEADING_PATTERN.finditer(text))

    if not headings:
        # No headings found: single section from the whole document
        title = Path(filename).stem if filename else "Document"
        body = _strip_empty_html(text.strip())
        return [
            Section(
                title=title,
                level=0,
                body=body,
                vehicle_model=doc_vehicle_model,
                dtc_codes=_extract_dtc_codes(body),
            )
        ]

    sections: List[Section] = []

    # If there is text before the first heading, capture it as a preamble
    preamble = text[: headings[0].start()].strip()
    if preamble:
        title = Path(filename).stem if filename else "Introduction"
        preamble = _strip_empty_html(preamble)
        sections.append(
            Section(
                title=title,
                level=0,
                body=preamble,
                vehicle_model=doc_vehicle_model,
                dtc_codes=_extract_dtc_codes(preamble),
            )
        )

    for idx, match in enumerate(headings):
        level = len(match.group(1))  # number of '#' chars
        title = _clean_section_title(match.group(2))

        # Body runs from end of this heading line to start of next heading (or EOF)
        body_start = match.end()
        body_end = headings[idx + 1].start() if idx + 1 < len(headings) else len(text)
        body = _strip_empty_html(text[body_start:body_end].strip())

        # Section-level overrides: check section text for vehicle model
        section_vehicle = extract_vehicle_model(title + " " + body)
        if section_vehicle == "Generic":
            section_vehicle = doc_vehicle_model

        sections.append(
            Section(
                title=title,
                level=level,
                body=body,
                vehicle_model=section_vehicle,
                dtc_codes=_extract_dtc_codes(title + " " + body),
            )
        )

    return sections


def parse_log(text: str, filename: str = "") -> List[Section]:
    """Parse a maintenance log as a single section.

    Extracts title from Date + Service headers if present.
    Extracts DTC codes and vehicle model.

    Args:
        text: Full log text.
        filename: Original filename (used as fallback title).

    Returns:
        List containing a single Section.
    """
    # Logs typically don't carry frontmatter, but strip defensively
    # in case a future producer prepends one.
    _, text = _strip_yaml_frontmatter(text)

    # Strip any empty HTML elements before further processing.
    text = _strip_empty_html(text)

    # Try to build a title from Date and Service fields
    date_match = re.search(r"\*\*Date:\*\*\s*(.+)", text)
    service_match = re.search(r"\*\*Service:\*\*\s*(.+)", text)

    parts = []
    if date_match:
        parts.append(date_match.group(1).strip())
    if service_match:
        parts.append(service_match.group(1).strip())

    if parts:
        title = " - ".join(parts)
    else:
        title = Path(filename).stem if filename else "Log Entry"

    vehicle_model = extract_vehicle_model(text)
    dtc_codes = _extract_dtc_codes(text)

    return [
        Section(
            title=title,
            level=0,
            body=text.strip(),
            vehicle_model=vehicle_model,
            dtc_codes=dtc_codes,
        )
    ]


def parse_document(text: str, filename: str = "") -> List[Section]:
    """Auto-detect document type and parse accordingly.

    Uses filename heuristics: 'log' in name -> parse_log, otherwise parse_manual.

    Args:
        text: Full document text.
        filename: Original filename.

    Returns:
        List of Section objects.
    """
    name_lower = filename.lower()
    if "log" in name_lower:
        return parse_log(text, filename)
    return parse_manual(text, filename)
