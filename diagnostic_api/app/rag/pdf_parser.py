"""PDF text extraction using PyMuPDF (fitz).

Extracts text from PDF files page-by-page, preserving structure markers
for downstream parsing. Supports large files (50MB+) efficiently.

TODOs from code review:
- TODO(1): Add file size validation to prevent memory exhaustion on large/malicious files
- TODO(2): Add PDF magic byte validation before opening (defense-in-depth)
- TODO(5): Add exception handling for corrupt/password-protected PDFs (fitz.FileDataError, etc.)
- TODO(6): Remove parse_pdf() or use it in ingest.py - currently dead code
- TODO(7): Add progress logging for large PDFs (log every N pages)
- TODO(8): Use context manager pattern (with fitz.open()) instead of try/finally
"""

import fitz  # PyMuPDF
from pathlib import Path
from typing import List

from .parser import Section, parse_manual


def extract_text_from_pdf(file_path: Path) -> str:
    """Extract all text from a PDF file, preserving structure.

    Processes the PDF page-by-page and adds page markers to help
    with downstream chunking and reference tracking.

    Args:
        file_path: Path to the PDF file.

    Returns:
        Extracted text with [Page N] markers.

    Raises:
        FileNotFoundError: If the file does not exist.
        fitz.FileDataError: If the file is not a valid PDF.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"PDF file not found: {file_path}")

    doc = fitz.open(file_path)
    text_parts = []

    try:
        for page_num, page in enumerate(doc, start=1):
            # Extract text with layout preservation
            text = page.get_text("text")
            if text.strip():
                text_parts.append(f"[Page {page_num}]\n{text}")
    finally:
        doc.close()

    return "\n\n".join(text_parts)


def parse_pdf(file_path: Path) -> List[Section]:
    """Parse a PDF file into Section objects.

    Extracts text from the PDF and then applies the existing
    markdown/manual parser logic to identify sections, DTC codes,
    and vehicle models.

    Args:
        file_path: Path to the PDF file.

    Returns:
        List of Section objects extracted from the PDF.
    """
    text = extract_text_from_pdf(file_path)
    return parse_manual(text, file_path.name)
