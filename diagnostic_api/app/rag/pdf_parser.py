"""PDF text extraction using PyMuPDF (fitz).

Extracts text from PDF files page-by-page, preserving structure markers
for downstream parsing. Supports large files (50MB+) efficiently.

When ``describe_images=True`` is passed to the async variant, images are
extracted from each page and described via a local Ollama vision model.
The descriptions are inserted inline using ``[Image N, Page M]`` markers
so the downstream pipeline (chunker -> embedder -> Weaviate) works unchanged.

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

import fitz  # PyMuPDF
from pathlib import Path
import structlog

logger = structlog.get_logger(__name__)

# Minimum dimensions to consider an image meaningful (skip icons/bullets)
_MIN_IMAGE_WIDTH = 50
_MIN_IMAGE_HEIGHT = 50
# Minimum byte size to consider an image meaningful (skip spacers/borders)
_MIN_IMAGE_BYTES = 5 * 1024  # 5 KB

# Max concurrent vision model calls per page
_VISION_CONCURRENCY = 3


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
