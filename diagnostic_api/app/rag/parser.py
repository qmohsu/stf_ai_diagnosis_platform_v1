"""Document parser for manuals and maintenance logs.

Splits documents on markdown headings, extracts DTC codes and vehicle models.
"""

import re
from pathlib import Path
from typing import List, Optional

from pydantic import BaseModel

# Regex patterns
DTC_PATTERN = re.compile(r"\b[PBCU]\d{4}\b")
VEHICLE_MODEL_PATTERN = re.compile(r"\bSTF[-\s]?\d{3,4}\b", re.IGNORECASE)
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


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


def _extract_vehicle_model(text: str) -> str:
    """Extract vehicle model (STF-NNN pattern) from text."""
    match = VEHICLE_MODEL_PATTERN.search(text)
    if match:
        # Normalize to "STF-NNNN" format
        raw = match.group()
        digits = re.search(r"\d{3,4}", raw).group()
        return f"STF-{digits}"
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
    # Extract document-level vehicle model (often in first line / title)
    doc_vehicle_model = _extract_vehicle_model(text)

    headings = list(HEADING_PATTERN.finditer(text))

    if not headings:
        # No headings found: single section from the whole document
        title = Path(filename).stem if filename else "Document"
        return [
            Section(
                title=title,
                level=0,
                body=text.strip(),
                vehicle_model=doc_vehicle_model,
                dtc_codes=_extract_dtc_codes(text),
            )
        ]

    sections: List[Section] = []

    # If there is text before the first heading, capture it as a preamble
    preamble = text[: headings[0].start()].strip()
    if preamble:
        title = Path(filename).stem if filename else "Introduction"
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
        title = match.group(2).strip()

        # Body runs from end of this heading line to start of next heading (or EOF)
        body_start = match.end()
        body_end = headings[idx + 1].start() if idx + 1 < len(headings) else len(text)
        body = text[body_start:body_end].strip()

        # Section-level overrides: check section text for vehicle model
        section_vehicle = _extract_vehicle_model(title + " " + body)
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

    vehicle_model = _extract_vehicle_model(text)
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
