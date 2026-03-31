"""PDF text extraction using PyMuPDF (fitz).

Extracts text from PDF files page-by-page, preserving structure markers
for downstream parsing. Supports large files (50MB+) efficiently.

When ``describe_images=True`` is passed to the async variant, images are
extracted from each page and described via a local Ollama vision model.
The descriptions are inserted inline using ``[Image N, Page M]`` markers
so the downstream pipeline (chunker -> embedder -> pgvector) works unchanged.

The ``extract_pdf_sections()`` function provides a higher-level API that
uses font-size metadata from PyMuPDF to detect heading hierarchy and
returns structured ``Section`` objects directly, bypassing the markdown
parser.  This is essential for real-world PDF manuals that do not use
markdown headings.

TODOs from code review:
- TODO(1): Add file size validation to prevent memory exhaustion on large/malicious files
- TODO(2): Add PDF magic byte validation before opening (defense-in-depth)
- TODO(5): Add exception handling for corrupt/password-protected PDFs (fitz.FileDataError, etc.)
- TODO(7): Add progress logging for large PDFs (log every N pages)
- TODO(8): Use context manager pattern (with fitz.open()) instead of try/finally
- TODO(11): Offload blocking fitz I/O to a thread pool (run_in_executor) to avoid
  blocking the asyncio event loop in extract_text_from_pdf_async
"""

import asyncio
import re
from collections import Counter
from typing import List

import fitz  # PyMuPDF
from pathlib import Path
import structlog

from app.rag.parser import (
    Section,
    _extract_dtc_codes,
    extract_vehicle_model,
)

logger = structlog.get_logger(__name__)

# Minimum dimensions to consider an image meaningful (skip icons/bullets)
_MIN_IMAGE_WIDTH = 50
_MIN_IMAGE_HEIGHT = 50
# Minimum byte size to consider an image meaningful (skip spacers/borders)
_MIN_IMAGE_BYTES = 5 * 1024  # 5 KB

# Max concurrent vision model calls per page
_VISION_CONCURRENCY = 3

# Default DPI for full-page rendering (~0.8 MB/page at 150 DPI)
_DEFAULT_RENDER_DPI = 150

# Minimum number of distinct left-edge x-positions to heuristically
# detect a table layout when page.find_tables() is unavailable.
_TABLE_LEFT_EDGE_THRESHOLD = 3


def extract_images_from_page(
    doc: fitz.Document,
    page: fitz.Page,
    page_num: int,
) -> list[dict]:
    """Extract meaningful images from a PDF page.

    Filters out small or decorative images (icons, bullets, spacers).

    Args:
        doc: The open fitz.Document (needed for xref lookups).
        page: The fitz.Page to extract images from.
        page_num: 1-based page number for logging.

    Returns:
        List of dicts with keys: ``index`` (1-based), ``png_bytes``.
    """
    images = []
    image_index = 0

    for img_info in page.get_images(full=True):
        xref = img_info[0]
        pix = None
        pix_converted = None
        try:
            pix = fitz.Pixmap(doc, xref)

            # Skip small images (icons, bullets, decorative elements)
            if pix.width < _MIN_IMAGE_WIDTH or pix.height < _MIN_IMAGE_HEIGHT:
                continue

            # Convert CMYK or other non-RGB color spaces to RGB
            if pix.n > 4:
                pix_converted = fitz.Pixmap(fitz.csRGB, pix)
                active_pix = pix_converted
            else:
                active_pix = pix

            png_bytes = active_pix.tobytes("png")

            # Skip tiny byte-size images (spacers, borders)
            if len(png_bytes) < _MIN_IMAGE_BYTES:
                continue

            image_index += 1
            images.append({"index": image_index, "png_bytes": png_bytes})

        except Exception as e:
            logger.warning(
                "pdf_parser.image_extraction_error",
                page=page_num,
                xref=xref,
                error=str(e),
            )
        finally:
            # Guarantee native pixmap memory is released
            if pix_converted is not None:
                del pix_converted
            if pix is not None:
                del pix

    return images


def render_page_image(
    page: fitz.Page,
    dpi: int = _DEFAULT_RENDER_DPI,
) -> bytes:
    """Render a full PDF page as a PNG image.

    Uses PyMuPDF's pixmap rendering at the specified DPI.  At
    150 DPI a typical A4 page produces ~0.8 MB of PNG data,
    suitable for both vision-model description and OCR.

    Args:
        page: A ``fitz.Page`` object to render.
        dpi: Dots per inch for the rendering (default 150).

    Returns:
        PNG image bytes of the rendered page.

    Raises:
        ValueError: If *dpi* is not positive.
    """
    if dpi <= 0:
        raise ValueError(f"dpi must be positive, got {dpi}")

    scale = dpi / 72.0
    matrix = fitz.Matrix(scale, scale)
    pix = page.get_pixmap(matrix=matrix)
    try:
        png_bytes = pix.tobytes("png")
    finally:
        del pix
    return png_bytes


def has_tables_on_page(page: fitz.Page) -> bool:
    """Detect whether a PDF page likely contains tabular data.

    First tries ``page.find_tables()`` (PyMuPDF ≥ 1.23.0).  If
    the method is unavailable or raises, falls back to a heuristic
    that counts distinct left-edge x-positions of text blocks — a
    simple proxy for columnar / tabular layout.

    Args:
        page: A ``fitz.Page`` object to inspect.

    Returns:
        ``True`` if the page appears to contain at least one table.
    """
    # ---- Primary method: PyMuPDF's built-in table finder ----
    try:
        tables = page.find_tables()
        if tables and len(tables.tables) > 0:
            return True
        return False
    except AttributeError:
        # find_tables() not available in this PyMuPDF version
        pass
    except Exception as exc:
        logger.debug(
            "pdf_parser.find_tables_fallback",
            error=str(exc),
        )

    # ---- Fallback heuristic: distinct left-edge x-positions ----
    page_dict = page.get_text("dict")
    left_edges: set[float] = set()
    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        bbox = block.get("bbox")
        if bbox:
            # Round to nearest 5pt to tolerate minor alignment jitter
            left_edges.add(round(bbox[0] / 5.0) * 5.0)

    return len(left_edges) >= _TABLE_LEFT_EDGE_THRESHOLD


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


async def extract_text_from_pdf_async(
    file_path: Path,
    *,
    describe_images: bool = False,
) -> str:
    """Async variant of extract_text_from_pdf with optional image description.

    When *describe_images* is ``True``, images are extracted from each page,
    sent to the vision service for description, and the descriptions are
    appended after the page text using ``[Image N, Page M]`` markers.

    Note: fitz operations are synchronous and block the event loop.
    See TODO(11) for planned thread-pool offloading.

    Args:
        file_path: Path to the PDF file.
        describe_images: If True, extract and describe images via vision model.

    Returns:
        Extracted text (with image descriptions when enabled) and [Page N] markers.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"PDF file not found: {file_path}")

    doc = fitz.open(file_path)
    text_parts = []

    try:
        if describe_images:
            from .vision import get_vision_service
            vision_svc = get_vision_service()

        for page_num, page in enumerate(doc, start=1):
            page_text = page.get_text("text")
            page_content = ""

            if page_text.strip():
                page_content = f"[Page {page_num}]\n{page_text}"

            # Extract and describe images if enabled
            if describe_images:
                page_images = extract_images_from_page(doc, page, page_num)
                if page_images:
                    page_context = page_text[:500] if page_text else ""
                    sem = asyncio.Semaphore(_VISION_CONCURRENCY)

                    async def _describe(img: dict) -> tuple[dict, str]:
                        async with sem:
                            try:
                                desc = await vision_svc.describe_image(
                                    img["png_bytes"],
                                    context=page_context,
                                )
                            except Exception as e:
                                logger.warning(
                                    "pdf_parser.vision_error",
                                    page=page_num,
                                    image_index=img["index"],
                                    error=str(e),
                                )
                                desc = ""
                            return img, desc

                    results = await asyncio.gather(
                        *(_describe(img) for img in page_images),
                    )
                    for img, description in results:
                        if description:
                            img_header = (
                                f"[Image {img['index']}, Page {page_num}]"
                            )
                            page_content += (
                                f"\n\n{img_header}\nDescription: {description}"
                            )

            if page_content.strip():
                text_parts.append(page_content)
    finally:
        doc.close()

    return "\n\n".join(text_parts)


# ------------------------------------------------------------------
# Font-based structured section extraction
# ------------------------------------------------------------------

# Thresholds for heading detection relative to body font size.
# Calibrated against Yamaha MWS150-A service manual (FrameMaker).
_HEADING_L1_RATIO = 1.5   # >= body * 1.5 → chapter heading (17pt/10.5)
_HEADING_L2_RATIO = 1.25  # >= body * 1.25 → section heading (14pt/10.5)

# Page number pattern (e.g., "1-1", "3-22", "4-92")
_PAGE_NUM_PATTERN = re.compile(r"^\d{1,2}-\d{1,3}$")

# EAS/EWA/ECA reference codes (metadata, not heading)
_EAS_CODE_PATTERN = re.compile(r"^E[ACW][AS]\d{5}$")

# Standalone page numbers (e.g., "10", "33", "141", "280").
# Complements _PAGE_NUM_PATTERN which handles "X-Y" format.
_STANDALONE_PAGE_NUM = re.compile(r"^\d{1,4}$")

# Breadcrumb / navigation headers in Honda-style PDFs
# (e.g., "uuFor Safe DrivinguImportant Safety Precautions")
_BREADCRUMB_PATTERN = re.compile(r"^uu\w.*u\w")

# Letter in any script (Latin, CJK, Cyrillic, etc.) but
# not a digit.  Used to guard heading classification.
_HAS_LETTER_RE = re.compile(r"[^\d\W]", re.UNICODE)

# Minimum text length for a section body to be kept (skip tiny fragments)
_MIN_SECTION_BODY_LEN = 20

# Maximum pages to sample when computing body font size mode
_FONT_SAMPLE_PAGES = 50


def compute_body_font_size(doc: fitz.Document) -> float:
    """Determine the most common (body) font size in the document.

    Samples up to ``_FONT_SAMPLE_PAGES`` pages spread across the
    document and returns the mode of span-level font sizes weighted
    by character count.

    Args:
        doc: An open fitz.Document.

    Returns:
        The body font size in points.  Falls back to 10.0 if no
        text is found.
    """
    size_counts: Counter = Counter()
    total_pages = doc.page_count
    step = max(1, total_pages // _FONT_SAMPLE_PAGES)

    for page_idx in range(0, total_pages, step):
        page = doc[page_idx]
        page_dict = page.get_text("dict")
        for block in page_dict.get("blocks", []):
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if text:
                        size = round(span.get("size", 0), 1)
                        size_counts[size] += len(text)

    if not size_counts:
        return 10.0
    return size_counts.most_common(1)[0][0]


def _extract_page_lines(
    page: fitz.Page,
) -> List[dict]:
    """Extract text lines from a page with font metadata.

    Each returned dict contains:
      - ``text``: stripped line text
      - ``font_size``: maximum span font size on the line
      - ``is_bold``: True if any span on the line is bold

    Args:
        page: A fitz.Page object.

    Returns:
        List of line dicts, excluding blank lines.
    """
    page_dict = page.get_text("dict")
    lines: List[dict] = []

    for block in page_dict.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            line_text_parts: List[str] = []
            max_size = 0.0
            bold = False
            for span in line.get("spans", []):
                line_text_parts.append(span.get("text", ""))
                span_size = span.get("size", 0)
                if span_size > max_size:
                    max_size = span_size
                # fitz flags: bit 4 (16) = bold
                if span.get("flags", 0) & 16:
                    bold = True

            text = "".join(line_text_parts).strip()
            if text:
                lines.append({
                    "text": text,
                    "font_size": round(max_size, 1),
                    "is_bold": bold,
                })

    return lines


def _classify_line(
    line: dict,
    body_size: float,
) -> str:
    """Classify a line as heading, page_num, etc.

    Args:
        line: Dict with ``text``, ``font_size``, ``is_bold``.
        body_size: The document's body font size.

    Returns:
        One of ``"heading_l1"``, ``"heading_l2"``,
        ``"page_num"``, ``"eas_code"``, ``"breadcrumb"``,
        or ``"body"``.
    """
    text = line["text"]
    size = line["font_size"]

    # Skip page number lines (e.g., "3-22")
    if _PAGE_NUM_PATTERN.match(text):
        return "page_num"

    # Skip standalone page numbers (e.g., "10", "141")
    if _STANDALONE_PAGE_NUM.match(text):
        return "page_num"

    # Skip EAS/EWA/ECA reference codes
    if _EAS_CODE_PATTERN.match(text):
        return "eas_code"

    # Skip breadcrumb / navigation headers
    if _BREADCRUMB_PATTERN.match(text):
        return "breadcrumb"

    # Headings must contain at least one letter (any
    # script: Latin, CJK, Cyrillic, etc.).  Prevents
    # pure symbols or digits from becoming headings.
    has_letter = bool(_HAS_LETTER_RE.search(text))

    # Chapter heading: significantly larger than body
    if size >= body_size * _HEADING_L1_RATIO and has_letter:
        return "heading_l1"

    # Section heading: moderately larger than body
    if (
        size >= body_size * _HEADING_L2_RATIO
        and has_letter
    ):
        return "heading_l2"

    return "body"


def extract_pdf_sections(
    file_path: Path,
    filename: str = "",
) -> List[Section]:
    """Extract structured sections from a PDF using font metadata.

    Uses PyMuPDF's ``get_text("dict")`` to obtain per-span font
    sizes, then classifies lines as headings or body text based
    on size relative to the document's body font.  Groups body
    text under the nearest preceding heading to form ``Section``
    objects with meaningful titles and hierarchy.

    Falls back to page-level sections when no font-size variation
    is detected (e.g., scanned/OCR PDFs).

    Args:
        file_path: Path to the PDF file.
        filename: Original filename (used for doc-level metadata
            extraction and as fallback title).

    Returns:
        List of Section objects with title, level, body,
        vehicle_model, and dtc_codes populated.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"PDF file not found: {file_path}")

    if not filename:
        filename = file_path.name

    doc = fitz.open(file_path)
    try:
        body_size = compute_body_font_size(doc)
        logger.info(
            "pdf_parser.body_font_size",
            file=filename,
            body_size=body_size,
        )

        # Extract document-level vehicle model from filename
        doc_vehicle = extract_vehicle_model(filename)

        # Collect all lines across all pages with classification
        sections: List[Section] = []
        current_title = ""
        current_level = 0
        current_body_parts: List[str] = []

        def _flush_section() -> None:
            """Flush accumulated body text into a Section."""
            nonlocal current_body_parts
            body = "\n".join(current_body_parts).strip()
            if not body or len(body) < _MIN_SECTION_BODY_LEN:
                return

            title = current_title if current_title else (
                Path(filename).stem
            )

            # Extract metadata from the section body
            full_text = title + "\n" + body
            section_vehicle = extract_vehicle_model(full_text)
            if section_vehicle == "Generic":
                section_vehicle = doc_vehicle

            sections.append(Section(
                title=title,
                level=current_level,
                body=body,
                vehicle_model=section_vehicle,
                dtc_codes=_extract_dtc_codes(full_text),
            ))

        for page_idx in range(doc.page_count):
            page = doc[page_idx]
            lines = _extract_page_lines(page)

            for line in lines:
                cls = _classify_line(line, body_size)

                if cls in ("heading_l1", "heading_l2"):
                    # Flush previous section
                    _flush_section()
                    current_title = line["text"]
                    current_level = (
                        1 if cls == "heading_l1" else 2
                    )
                    current_body_parts = []

                elif cls == "body":
                    current_body_parts.append(line["text"])

                elif cls == "eas_code":
                    # Include EAS codes in body for traceability
                    current_body_parts.append(line["text"])

                # page_num and breadcrumb lines skip

        # Flush the last section
        _flush_section()

        # Fallback: if no headings were detected, split by pages
        if not sections:
            logger.warning(
                "pdf_parser.no_headings_detected",
                file=filename,
                fallback="page_level",
            )
            sections = _fallback_page_sections(
                doc, filename, doc_vehicle,
            )

        logger.info(
            "pdf_parser.sections_extracted",
            file=filename,
            section_count=len(sections),
        )
        return sections

    finally:
        doc.close()


def _fallback_page_sections(
    doc: fitz.Document,
    filename: str,
    doc_vehicle: str,
) -> List[Section]:
    """Create one section per page as a fallback.

    Used when font-based heading detection finds no variation
    (e.g., scanned PDFs or single-font documents).

    Args:
        doc: An open fitz.Document.
        filename: Original filename for metadata.
        doc_vehicle: Document-level vehicle model.

    Returns:
        List of Section objects, one per non-empty page.
    """
    sections: List[Section] = []
    stem = Path(filename).stem

    for page_idx in range(doc.page_count):
        page = doc[page_idx]
        text = page.get_text("text").strip()
        if not text or len(text) < _MIN_SECTION_BODY_LEN:
            continue

        page_num = page_idx + 1
        # Use first non-trivial line as title
        first_lines = [
            ln.strip() for ln in text.split("\n")
            if ln.strip()
            and not _PAGE_NUM_PATTERN.match(ln.strip())
            and not _STANDALONE_PAGE_NUM.match(
                ln.strip()
            )
            and not _EAS_CODE_PATTERN.match(ln.strip())
            and not _BREADCRUMB_PATTERN.match(
                ln.strip()
            )
        ]
        title = first_lines[0] if first_lines else (
            f"{stem} p{page_num}"
        )
        # Truncate overly long titles
        if len(title) > 60:
            title = title[:57] + "..."

        full_text = title + "\n" + text
        section_vehicle = extract_vehicle_model(full_text)
        if section_vehicle == "Generic":
            section_vehicle = doc_vehicle

        sections.append(Section(
            title=title,
            level=0,
            body=text,
            vehicle_model=section_vehicle,
            dtc_codes=_extract_dtc_codes(full_text),
        ))

    return sections


def build_page_to_section_map(
    doc: fitz.Document,
    body_size: float,
) -> dict[int, int]:
    """Map each page index to its nearest preceding section index.

    Re-scans headings to build a page → section_idx mapping so
    that image descriptions and OCR blocks can be appended to the
    correct section.

    Args:
        doc: An open fitz.Document.
        body_size: The document's body font size.

    Returns:
        Dict mapping page_idx (0-based) → section_idx (0-based).
    """
    page_to_section: dict[int, int] = {}
    current_section_idx = -1

    for page_idx in range(doc.page_count):
        page = doc[page_idx]
        lines = _extract_page_lines(page)
        for line in lines:
            cls = _classify_line(line, body_size)
            if cls in ("heading_l1", "heading_l2"):
                current_section_idx += 1
                break
        page_to_section[page_idx] = max(
            current_section_idx, 0,
        )

    return page_to_section


def _append_to_section(
    sections: List[Section],
    section_idx: int,
    text_block: str,
) -> None:
    """Append a text block to a section body (in-place replacement).

    Creates a new ``Section`` object with the appended text since
    ``Section`` instances may be frozen / immutable.

    Args:
        sections: The section list to mutate.
        section_idx: Index of the target section.
        text_block: Text to append (should include leading newline).
    """
    if section_idx >= len(sections):
        return
    sec = sections[section_idx]
    sections[section_idx] = Section(
        title=sec.title,
        level=sec.level,
        body=sec.body + text_block,
        vehicle_model=sec.vehicle_model,
        dtc_codes=sec.dtc_codes,
    )


async def extract_pdf_sections_async(
    file_path: Path,
    filename: str = "",
    *,
    describe_images: bool = False,
    enable_ocr: bool = False,
    enable_page_render: bool = False,
) -> List[Section]:
    """Async variant of extract_pdf_sections with image enrichment.

    Extracts structured sections using font metadata, then optionally
    enriches them with:

    1. **OCR** (``enable_ocr``): Runs easyocr on individual images to
       extract text invisible to the PDF text layer (part numbers,
       torque specs, dimensions).  Non-redundant results are inserted
       as ``[OCR, Page M]`` blocks.
    2. **Vision model** (``describe_images``): Sends images (with OCR
       text as context) to the Ollama vision model for spatial/
       procedural descriptions, inserted as ``[Image N, Page M]``
       blocks.
    3. **Full-page render** (``enable_page_render``): Renders entire
       pages as images for OCR and/or vision when the page contains
       meaningful images, preserving spatial context lost by
       individual image extraction.

    Merge order per page:  text layer → OCR blocks → image descriptions
    → full-page description.

    Args:
        file_path: Path to the PDF file.
        filename: Original filename.
        describe_images: If True, describe images via vision model.
        enable_ocr: If True, run OCR on images and append
            non-redundant text as ``[OCR, Page M]`` blocks.
        enable_page_render: If True, render full pages as images
            for OCR / vision when the page has meaningful images.

    Returns:
        List of Section objects.

    Raises:
        FileNotFoundError: If the file does not exist.
    """
    # Get structured sections (synchronous fitz I/O)
    sections = extract_pdf_sections(file_path, filename)

    if not (describe_images or enable_ocr or enable_page_render):
        return sections

    if not file_path.exists():
        return sections

    # Lazy imports to avoid import-time side effects
    vision_svc = None
    if describe_images:
        from .vision import get_vision_service
        vision_svc = get_vision_service()

    ocr_func = None
    overlap_func = None
    if enable_ocr:
        from .ocr import ocr_extract_structured, compute_text_overlap
        ocr_func = ocr_extract_structured
        overlap_func = compute_text_overlap

    doc = fitz.open(file_path)
    try:
        body_size = compute_body_font_size(doc)
        page_to_section = build_page_to_section_map(
            doc, body_size,
        )

        for page_idx in range(doc.page_count):
            page = doc[page_idx]
            page_num = page_idx + 1
            page_images = extract_images_from_page(
                doc, page, page_num,
            )
            if not page_images:
                continue

            section_idx = page_to_section.get(page_idx, 0)
            page_text = page.get_text("text")

            # ----- OCR on individual images -----
            if enable_ocr and ocr_func and overlap_func:
                ocr_parts: List[str] = []
                for img in page_images:
                    try:
                        structured = ocr_func(img["png_bytes"])
                    except Exception as exc:
                        logger.warning(
                            "pdf_parser.ocr_error",
                            page=page_num,
                            image_index=img["index"],
                            error=str(exc),
                        )
                        continue

                    ocr_text = structured.get("full_text", "")
                    if not ocr_text.strip():
                        continue

                    # Deduplicate against page text layer
                    if overlap_func(ocr_text, page_text):
                        logger.debug(
                            "pdf_parser.ocr_redundant",
                            page=page_num,
                            image_index=img["index"],
                        )
                        continue

                    # Format structured results
                    parts: List[str] = []
                    pn = structured.get("part_numbers", [])
                    tv = structured.get("torque_values", [])
                    dm = structured.get("dimensions", [])
                    if pn:
                        parts.append(
                            f"Part numbers: {', '.join(pn)}"
                        )
                    if tv:
                        parts.append(
                            f"Torque: {', '.join(tv)}"
                        )
                    if dm:
                        parts.append(
                            f"Dimensions: {', '.join(dm)}"
                        )
                    if parts:
                        ocr_parts.append(
                            "\n".join(parts)
                        )
                    else:
                        # No structured data but has
                        # non-redundant raw text
                        ocr_parts.append(ocr_text[:500])

                if ocr_parts:
                    ocr_block = (
                        f"\n\n[OCR, Page {page_num}]\n"
                        + "\n".join(ocr_parts)
                    )
                    _append_to_section(
                        sections, section_idx, ocr_block,
                    )

            # ----- Vision model on individual images -----
            if describe_images and vision_svc:
                context_text = page_text[:500]
                sem = asyncio.Semaphore(_VISION_CONCURRENCY)

                async def _describe(
                    img: dict,
                    _pnum: int = page_num,
                    _ctx: str = context_text,
                ) -> tuple[dict, str]:
                    async with sem:
                        try:
                            desc = await vision_svc.describe_image(
                                img["png_bytes"],
                                context=_ctx,
                            )
                        except Exception as e:
                            logger.warning(
                                "pdf_parser.vision_error",
                                page=_pnum,
                                image_index=img["index"],
                                error=str(e),
                            )
                            desc = ""
                        return img, desc

                results = await asyncio.gather(
                    *(_describe(img) for img in page_images),
                )

                for img, description in results:
                    if description:
                        marker = (
                            f"\n\n[Image {img['index']}, "
                            f"Page {page_num}]\n"
                            f"Description: {description}"
                        )
                        _append_to_section(
                            sections, section_idx, marker,
                        )

            # ----- Full-page render (OCR + vision) -----
            if enable_page_render:
                try:
                    page_png = render_page_image(page)
                except Exception as exc:
                    logger.warning(
                        "pdf_parser.page_render_error",
                        page=page_num,
                        error=str(exc),
                    )
                    continue

                # OCR on full-page render
                if (
                    enable_ocr
                    and ocr_func
                    and overlap_func
                ):
                    try:
                        fp_structured = ocr_func(page_png)
                    except Exception as exc:
                        logger.warning(
                            "pdf_parser.page_ocr_error",
                            page=page_num,
                            error=str(exc),
                        )
                        fp_structured = {}

                    fp_text = fp_structured.get(
                        "full_text", "",
                    )
                    if (
                        fp_text.strip()
                        and not overlap_func(fp_text, page_text)
                    ):
                        fp_parts: List[str] = []
                        pn = fp_structured.get(
                            "part_numbers", [],
                        )
                        tv = fp_structured.get(
                            "torque_values", [],
                        )
                        dm = fp_structured.get(
                            "dimensions", [],
                        )
                        if pn:
                            fp_parts.append(
                                "Part numbers: "
                                + ", ".join(pn)
                            )
                        if tv:
                            fp_parts.append(
                                "Torque: "
                                + ", ".join(tv)
                            )
                        if dm:
                            fp_parts.append(
                                "Dimensions: "
                                + ", ".join(dm)
                            )
                        if fp_parts:
                            fp_block = (
                                f"\n\n[OCR, Page {page_num}]"
                                f"\n"
                                + "\n".join(fp_parts)
                            )
                            _append_to_section(
                                sections,
                                section_idx,
                                fp_block,
                            )

                # Vision on full-page render
                if describe_images and vision_svc:
                    try:
                        fp_desc = (
                            await vision_svc.describe_image(
                                page_png,
                                context=page_text[:500],
                            )
                        )
                    except Exception as exc:
                        logger.warning(
                            "pdf_parser.page_vision_error",
                            page=page_num,
                            error=str(exc),
                        )
                        fp_desc = ""

                    if fp_desc:
                        fp_marker = (
                            f"\n\n[Full Page, "
                            f"Page {page_num}]\n"
                            f"Description: {fp_desc}"
                        )
                        _append_to_section(
                            sections,
                            section_idx,
                            fp_marker,
                        )

    finally:
        doc.close()

    return sections
