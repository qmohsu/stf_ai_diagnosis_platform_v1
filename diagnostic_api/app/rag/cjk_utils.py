"""Shared CJK and markdown utilities for the RAG chunker.

Centralises CJK character-range detection and the markdown
image-marker pattern used by the chunker.
"""

import re

# CJK Unified Ideographs + Extensions + Compatibility
CJK_RANGE = re.compile(
    r"[\u2E80-\u9FFF\uF900-\uFAFF\U00020000-\U0002FA1F]"
)

# Standard markdown image syntax: ``![alt text](path/to/image.png)``.
# Marker-pdf produces this format for every extracted figure;
# the chunker uses it to flag chunks containing images and to
# keep image-reference paragraphs atomic during splitting.
IMAGE_MARKER = re.compile(r"!\[[^\]]*\]\([^)]+\)")


def has_cjk(text: str) -> bool:
    """Return True if *text* contains any CJK characters."""
    return bool(CJK_RANGE.search(text))


def count_cjk(text: str) -> int:
    """Count CJK characters in *text*."""
    return len(CJK_RANGE.findall(text))
