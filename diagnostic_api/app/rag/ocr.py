"""OCR service for extracting text from PDF images.

Uses easyocr for CJK (Traditional Chinese) + English text
recognition on images extracted from automotive service manual
PDFs.  Designed to capture part numbers, torque specifications,
and dimensional callouts that are rendered inside diagrams and
invisible to the PDF text layer.

The module provides:
- ``ocr_image_bytes``: raw OCR on image bytes
- ``ocr_extract_structured``: categorised results (part numbers,
  torque values, dimensions)
- ``compute_text_overlap``: deduplication against existing text

The easyocr reader is lazily initialised as a module-level
singleton to avoid the 2-5 s startup cost on every call.

Author: Li-Ta Hsu
"""

import io
import re
from typing import List, Optional

import structlog
from PIL import Image

logger = structlog.get_logger(__name__)

# ------------------------------------------------------------------
# Lazy-loaded easyocr reader (heavy init ~2-5 s, ~200 MB RAM)
# ------------------------------------------------------------------
_reader: Optional[object] = None

# ------------------------------------------------------------------
# Regex patterns for structured extraction
# ------------------------------------------------------------------
_PART_NUMBER_RE = re.compile(
    r"\b\d{5}-[A-Z0-9]{5,7}\b"
)
_TORQUE_RE = re.compile(
    r"\d+(?:\.\d+)?\s*"
    r"(?:N[·.·]?m|kgf[·.·]?m|lb[·.\-]?ft)"
    r"(?:\s*[（(]\s*\d+(?:\.\d+)?\s*"
    r"(?:N[·.·]?m|kgf[·.·]?m|lb[·.\-]?ft)"
    r"\s*[)）])?",
    re.IGNORECASE,
)
_DIMENSION_RE = re.compile(
    r"[ø\u00d8]?\s*\d+(?:\.\d+)?\s*(?:mm|cm)\b",
    re.IGNORECASE,
)

# Minimum confidence threshold for OCR detections
_MIN_CONFIDENCE = 0.3


def _get_reader() -> object:
    """Return a lazily-initialised easyocr Reader.

    Supports Traditional Chinese (``ch_tra``) and English.
    GPU is disabled for portability (local-only deployment).

    Returns:
        An ``easyocr.Reader`` instance.

    Raises:
        ImportError: If easyocr is not installed.
    """
    global _reader
    if _reader is None:
        import easyocr  # type: ignore[import-untyped]

        import torch

        use_gpu = torch.cuda.is_available()
        _reader = easyocr.Reader(
            ["ch_tra", "en"],
            gpu=use_gpu,
        )
        logger.info(
            "ocr.gpu_status",
            gpu_available=use_gpu,
        )
        logger.info("ocr.reader_initialized")
    return _reader


def ocr_image_bytes(
    image_bytes: bytes,
    detail: int = 1,
) -> List[dict]:
    """Run OCR on raw image bytes.

    Args:
        image_bytes: PNG or JPEG image bytes.
        detail: easyocr detail level (0=simple, 1=full).

    Returns:
        List of dicts with keys ``text``, ``confidence``,
        and ``bbox`` (list of 4 ``[x, y]`` corner points).
        Detections below ``_MIN_CONFIDENCE`` are excluded.
    """
    if not image_bytes:
        return []

    reader = _get_reader()
    # easyocr accepts bytes, numpy arrays, or file paths — not PIL
    # Image objects.  Convert to numpy via PIL for reliable decoding.
    import numpy as np

    img = Image.open(io.BytesIO(image_bytes))
    img_array = np.array(img)

    try:
        results = reader.readtext(
            img_array,
            detail=detail,
            paragraph=False,
        )
    except Exception as exc:
        logger.warning("ocr.readtext_error", error=str(exc))
        return []

    extracted: List[dict] = []
    for bbox, text, conf in results:
        if conf < _MIN_CONFIDENCE:
            continue
        extracted.append({
            "text": text.strip(),
            "confidence": round(conf, 3),
            "bbox": bbox,
        })
    return extracted


def ocr_extract_structured(
    image_bytes: bytes,
) -> dict:
    """Run OCR and categorise results into structured fields.

    Args:
        image_bytes: PNG or JPEG image bytes.

    Returns:
        Dict with keys:
        - ``raw_texts``: list of all detected text strings.
        - ``part_numbers``: list of part-number strings
          matching ``\\d{5}-[A-Z0-9]{5,7}``.
        - ``torque_values``: list of torque specification
          strings (e.g. ``"75 N·m"``).
        - ``dimensions``: list of dimension strings
          (e.g. ``"ø38 mm"``).
        - ``full_text``: all detected texts joined by space.
    """
    detections = ocr_image_bytes(image_bytes)
    raw_texts = [d["text"] for d in detections]
    full_text = " ".join(raw_texts)

    return {
        "raw_texts": raw_texts,
        "part_numbers": _PART_NUMBER_RE.findall(full_text),
        "torque_values": _TORQUE_RE.findall(full_text),
        "dimensions": _DIMENSION_RE.findall(full_text),
        "full_text": full_text,
    }


_CJK_RANGE_RE = re.compile(
    r"[\u2E80-\u9FFF\uF900-\uFAFF\U00020000-\U0002FA1F]"
)


def _tokenize(text: str) -> set[str]:
    """Tokenise text for overlap comparison.

    CJK characters are split into individual characters (since
    Chinese has no whitespace boundaries), while Latin/digit
    runs are kept as whole tokens via ``\\w+``.

    Args:
        text: Input text (lowercased by caller).

    Returns:
        Set of unique tokens.
    """
    tokens: set[str] = set()
    for tok in re.findall(r"\w+", text):
        if _CJK_RANGE_RE.search(tok):
            # Split CJK token into individual characters
            tokens.update(ch for ch in tok if _CJK_RANGE_RE.match(ch))
        else:
            tokens.add(tok)
    return tokens


def compute_text_overlap(
    ocr_text: str,
    page_text: str,
    threshold: float = 0.8,
) -> bool:
    """Check whether OCR text is mostly redundant with page text.

    Tokenises both texts and computes the fraction of OCR tokens
    already present in the page text layer.  CJK characters are
    compared individually (not as whole runs) because the OCR and
    PDF text layers may have different whitespace patterns.

    Args:
        ocr_text: Text extracted by OCR.
        page_text: Text from the PDF text layer.
        threshold: Overlap fraction at or above which OCR is
            considered redundant (default 0.8).

    Returns:
        ``True`` if OCR text is redundant (should be skipped),
        ``False`` if it contains enough new information.
    """
    ocr_tokens = _tokenize(ocr_text.lower())
    if not ocr_tokens:
        return True

    page_tokens = _tokenize(page_text.lower())
    overlap = len(ocr_tokens & page_tokens)
    return (overlap / len(ocr_tokens)) >= threshold
