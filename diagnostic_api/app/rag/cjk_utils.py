"""Shared CJK detection utilities for RAG pipeline modules.

Centralises CJK character-range detection and image-marker
patterns used by chunker, translator, and OCR modules.
"""

import re

# CJK Unified Ideographs + Extensions + Compatibility
CJK_RANGE = re.compile(
    r"[\u2E80-\u9FFF\uF900-\uFAFF\U00020000-\U0002FA1F]"
)

# Markers inserted by pdf_parser (vision + OCR enrichment).
# Use with .search() to test, or wrap in a capturing group and
# .split() to break text around markers.
IMAGE_MARKER_PATTERN = (
    r"\[(?:Image \d+, Page \d+"
    r"|OCR, Page \d+"
    r"|Full Page, Page \d+)\]"
)

IMAGE_MARKER = re.compile(IMAGE_MARKER_PATTERN)
IMAGE_MARKER_SPLIT = re.compile(f"({IMAGE_MARKER_PATTERN})")


def has_cjk(text: str) -> bool:
    """Return True if *text* contains any CJK characters."""
    return bool(CJK_RANGE.search(text))


def count_cjk(text: str) -> int:
    """Count CJK characters in *text*."""
    return len(CJK_RANGE.findall(text))
