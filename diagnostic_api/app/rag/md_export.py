"""PDF-to-structured-markdown converter.

Converts PDF service manuals to structured markdown files following
the schema defined in ``docs/manual_markdown_schema.md`` (Issue #33).

The converter reuses the existing RAG ingestion pipeline for PDF
parsing, OCR, vision descriptions, and translation, but outputs
a single ``.md`` file per PDF instead of vector-store chunks.

Usage::

    python -m app.rag.md_export --dir /app/data --output /app/data/manuals
    python -m app.rag.md_export --dir /app/data --output /app/data/manuals \
        --enable-translation --enable-ocr --describe-images
"""

import argparse
import asyncio
import re
from datetime import datetime, timezone
from pathlib import Path

import fitz
import structlog

from app.rag.cjk_utils import has_cjk
from app.rag.parser import Section, extract_vehicle_model
from app.rag.pdf_parser import (
    build_page_to_section_map,
    compute_body_font_size,
    extract_images_from_page,
    extract_pdf_sections_async,
)

logger = structlog.get_logger(__name__)

_SLUG_MAX_LEN = 80
_VISION_CONCURRENCY = 3
_LANG_SAMPLE_CHARS = 2000

# Common suffixes found in service manual filenames.
# Matched case-insensitively and stripped to extract the
# vehicle model portion of the filename.
_MANUAL_SUFFIX_PATTERN = re.compile(
    r"[_\-\s]+"
    r"(?:Owners?|Service|Workshop|Repair|Shop|"
    r"Maintenance|Factory|Technical|User)"
    r"(?:[_\-\s]+(?:Manual|Guide|Handbook|Book))?"
    r"$",
    re.IGNORECASE,
)


# ------------------------------------------------------------------
# Pure helpers
# ------------------------------------------------------------------


def _clean_filename_stem(stem: str) -> str:
    """Extract a human-readable vehicle model from a filename stem.

    Strips common manual-related suffixes (e.g.
    ``Owners_Manual``, ``Service_Guide``) and normalises
    separators to spaces.

    Examples::

        >>> _clean_filename_stem("2016_Jazz_Owners_Manual")
        '2016 Jazz'
        >>> _clean_filename_stem("Honda_Civic_2020_Service_Manual")
        'Honda Civic 2020'
        >>> _clean_filename_stem("random_document")
        'random document'

    Args:
        stem: Filename stem (no extension).

    Returns:
        Cleaned string with underscores/hyphens replaced by
        spaces and manual suffixes removed.  Returns the
        original stem (space-normalised) if no suffix matched.
    """
    cleaned = _MANUAL_SUFFIX_PATTERN.sub("", stem)
    cleaned = re.sub(r"[_\-]+", " ", cleaned).strip()
    return cleaned if cleaned else stem


def _slugify(title: str) -> str:
    """Convert section title to a URL-safe slug.

    Implements the algorithm from ``manual_markdown_schema.md``
    section 4.1:

    1. Lowercase.
    2. Replace runs of non-alphanumeric characters (except
       hyphens) with a single hyphen.
    3. Strip leading/trailing hyphens.
    4. Truncate to 80 characters at a hyphen boundary if
       possible.

    Note: Duplicate-suffix handling (``-2``, ``-3``) is a
    runtime concern for navigation tools, not the converter.

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


def _detect_language(sections: list[Section]) -> str:
    """Detect source language from section content.

    Samples up to 2000 characters of body text and checks for
    CJK character presence.

    Args:
        sections: List of Section objects to analyse.

    Returns:
        BCP 47 language tag (``"zh-CN"`` or ``"en"``).
    """
    sample = ""
    for sec in sections:
        sample += sec.body
        if len(sample) >= _LANG_SAMPLE_CHARS:
            break
    return "zh-CN" if has_cjk(sample) else "en"


def _heading_prefix(level: int) -> str:
    """Map Section.level to markdown heading prefix.

    Args:
        level: Section level (0=root, 1=chapter, 2=section,
            3+=subsection).

    Returns:
        Markdown heading string (``"##"``, ``"###"``, etc.).
    """
    if level <= 1:
        return "##"
    if level == 2:
        return "###"
    return "####"


def _yaml_escape(value: str) -> str:
    """Quote a YAML string value if it contains special chars.

    Args:
        value: Raw string value.

    Returns:
        Quoted string safe for YAML frontmatter.
    """
    if re.search(r"[:{}\[\]#&*!|>'\"%@`,\n\r]", value):
        escaped = value.replace('"', '\\"')
        escaped = escaped.replace("\n", "\\n")
        escaped = escaped.replace("\r", "\\r")
        return f'"{escaped}"'
    return value


# ------------------------------------------------------------------
# Markdown assembly (extracted sub-functions)
# ------------------------------------------------------------------


def _build_frontmatter(
    *,
    source_pdf: str,
    vehicle_model: str,
    language: str,
    translated: bool,
    page_count: int,
    chapter_count: int,
) -> str:
    """Build YAML frontmatter block.

    Args:
        source_pdf: Original PDF filename.
        vehicle_model: Normalised vehicle model string.
        language: BCP 47 source language tag.
        translated: Whether machine translation was applied.
        page_count: Total pages in the source PDF.
        chapter_count: Number of ``##`` chapter headings.

    Returns:
        Frontmatter string including ``---`` delimiters.
    """
    now_utc = datetime.now(timezone.utc).isoformat(
        timespec="seconds",
    )
    lines = [
        "---",
        f"source_pdf: {_yaml_escape(source_pdf)}",
        f"vehicle_model: {_yaml_escape(vehicle_model)}",
        f"language: {_yaml_escape(language)}",
    ]
    if translated:
        lines.append("translated: true")
    lines += [
        f'exported_at: "{now_utc}"',
        f"page_count: {page_count}",
        f"section_count: {chapter_count}",
        "---",
    ]
    return "\n".join(lines)


def _build_dtc_index(
    sections: list[Section],
) -> str | None:
    """Build DTC cross-reference index appendix.

    Args:
        sections: All document sections.

    Returns:
        Markdown table string, or ``None`` if no DTC codes.
    """
    all_dtcs: list[tuple[str, str, str]] = []
    for section in sections:
        if not section.dtc_codes:
            continue
        slug = _slugify(section.title)
        for code in section.dtc_codes:
            all_dtcs.append(
                (code, section.title, slug)
            )

    if not all_dtcs:
        return None

    seen: set[str] = set()
    unique: list[tuple[str, str, str]] = []
    for code, sec_title, slug in sorted(all_dtcs):
        if code not in seen:
            seen.add(code)
            unique.append((code, sec_title, slug))

    lines = [
        "\n## Appendix: DTC Index",
        "",
        "| DTC | Section |",
        "|-----|---------|",
    ]
    for code, sec_title, slug in unique:
        link = f"[{sec_title}](#{slug})"
        lines.append(f"| {code} | {link} |")

    return "\n".join(lines)


def _sections_to_markdown(
    sections: list[Section],
    *,
    source_pdf: str,
    vehicle_model: str,
    language: str,
    translated: bool,
    page_count: int,
    section_to_page: dict[int, int],
    section_images: dict[int, list[dict]],
    image_descriptions: dict[str, str],
    stem: str,
) -> str:
    """Convert Section objects to markdown with YAML frontmatter.

    Produces a complete ``.md`` file conforming to the schema in
    ``docs/manual_markdown_schema.md``.

    Args:
        sections: Parsed Section objects (possibly translated).
        source_pdf: Original PDF filename.
        vehicle_model: Normalised vehicle model string.
        language: BCP 47 source language tag.
        translated: Whether machine translation was applied.
        page_count: Total pages in the source PDF.
        section_to_page: Maps section index (0-based) to first
            page index (0-based).
        section_images: Maps section index to list of image dicts
            with keys ``index``, ``page_num``, ``path_relative``.
        image_descriptions: Maps image key
            (``"p{page:03d}-{index}"``) to vision description.
        stem: PDF filename stem (for image path construction).

    Returns:
        Complete markdown string ready to write to file.
    """
    parts: list[str] = []

    chapter_count = sum(
        1 for s in sections if s.level <= 1
    )
    parts.append(_build_frontmatter(
        source_pdf=source_pdf,
        vehicle_model=vehicle_model,
        language=language,
        translated=translated,
        page_count=page_count,
        chapter_count=chapter_count,
    ))

    # Document title (single # heading)
    if vehicle_model and vehicle_model != "Generic":
        title = f"{vehicle_model} Service Manual"
    elif sections:
        title = sections[0].title
    else:
        title = stem
    parts.append(f"\n# {title}\n")

    # Sections
    for idx, section in enumerate(sections):
        body = section.body.strip()
        if not body:
            continue

        block: list[str] = []

        if idx in section_to_page:
            page_num = section_to_page[idx] + 1
            block.append(f"<!-- page:{page_num} -->")

        prefix = _heading_prefix(section.level)
        block.append(f"{prefix} {section.title}")
        block.append(f"\n{body}")

        for img in section_images.get(idx, []):
            pnum = img["page_num"]
            iidx = img["index"]
            rel = img["path_relative"]
            alt = f"Image {iidx} from page {pnum}"
            block.append(f"\n![{alt}]({rel})")
            key = f"p{pnum:03d}-{iidx}"
            desc = image_descriptions.get(key)
            if desc:
                block.append(
                    f"\n*Vision description: {desc}*"
                )

        parts.append("\n".join(block))

    dtc_index = _build_dtc_index(sections)
    if dtc_index:
        parts.append(dtc_index)

    return "\n\n".join(parts) + "\n"


# ------------------------------------------------------------------
# Image extraction + vision description
# ------------------------------------------------------------------


def _extract_and_save_images(
    doc: fitz.Document,
    output_dir: Path,
    stem: str,
) -> dict[int, list[dict]]:
    """Extract images from every PDF page and save as PNG.

    Args:
        doc: Open fitz document.
        output_dir: Root output directory.
        stem: PDF filename stem for subdirectory naming.

    Returns:
        Mapping of ``page_num`` (1-based) to list of image
        entry dicts (keys: ``index``, ``page_num``,
        ``path_relative``, ``png_bytes``).
    """
    image_dir = output_dir / "images" / stem
    created = False
    image_map: dict[int, list[dict]] = {}

    for page_idx in range(doc.page_count):
        page = doc[page_idx]
        page_num = page_idx + 1
        page_images = extract_images_from_page(
            doc, page, page_num,
        )
        if not page_images:
            continue

        if not created:
            image_dir.mkdir(parents=True, exist_ok=True)
            created = True

        entries: list[dict] = []
        for img in page_images:
            fname = (
                f"p{page_num:03d}-{img['index']}.png"
            )
            (image_dir / fname).write_bytes(
                img["png_bytes"],
            )
            entries.append({
                "index": img["index"],
                "page_num": page_num,
                "path_relative": (
                    f"images/{stem}/{fname}"
                ),
                "png_bytes": img["png_bytes"],
            })
        image_map[page_num] = entries

    return image_map


async def _describe_images(
    doc: fitz.Document,
    image_map: dict[int, list[dict]],
) -> dict[str, str]:
    """Describe images via the Ollama vision model.

    Frees ``png_bytes`` from each entry as soon as the
    description completes to limit peak memory usage.

    Args:
        doc: Open fitz document (for page text context).
        image_map: Image map from :func:`_extract_and_save_images`.

    Returns:
        Mapping of image key (``"p{page:03d}-{index}"``) to
        vision description string.
    """
    from app.rag.vision import get_vision_service

    vision_svc = get_vision_service()
    sem = asyncio.Semaphore(_VISION_CONCURRENCY)
    descriptions: dict[str, str] = {}

    async def _one(
        entry: dict, page_text: str,
    ) -> tuple[str, str]:
        key = (
            f"p{entry['page_num']:03d}"
            f"-{entry['index']}"
        )
        png = entry.pop("png_bytes", b"")
        async with sem:
            try:
                desc = await vision_svc.describe_image(
                    png, context=page_text[:500],
                )
            except Exception as exc:
                logger.warning(
                    "md_export.vision_error",
                    page=entry["page_num"],
                    index=entry["index"],
                    error=str(exc),
                )
                desc = ""
        return key, desc

    tasks = []
    for page_num, entries in image_map.items():
        ptxt = doc[page_num - 1].get_text("text")
        for entry in entries:
            tasks.append(_one(entry, ptxt))

    for key, desc in await asyncio.gather(*tasks):
        if desc:
            descriptions[key] = desc

    return descriptions


# ------------------------------------------------------------------
# Vehicle model resolution
# ------------------------------------------------------------------


def _resolve_vehicle_model(
    sections: list[Section],
    filename: str,
    stem: str,
    override: str = "",
) -> str:
    """Determine the best vehicle model string.

    Priority:

    1. Explicit *override* (from ``--vehicle-model`` CLI).
    2. First non-"Generic" section ``vehicle_model``.
    3. Domain-specific regex match via
       :func:`extract_vehicle_model`.
    4. Cleaned filename stem (manual suffixes stripped).

    Args:
        sections: Parsed sections.
        filename: Original PDF filename.
        stem: PDF filename stem.
        override: Optional explicit model string from CLI.

    Returns:
        Resolved vehicle model string (never ``"Generic"``).
    """
    if override:
        return override
    for sec in sections:
        if sec.vehicle_model != "Generic":
            return sec.vehicle_model
    model = extract_vehicle_model(filename)
    if model != "Generic":
        return model
    return _clean_filename_stem(stem)


# ------------------------------------------------------------------
# Main orchestrator
# ------------------------------------------------------------------


async def export_pdf_to_markdown(
    file_path: Path,
    output_dir: Path,
    *,
    describe_images: bool = False,
    enable_ocr: bool = False,
    enable_translation: bool = False,
    vehicle_model: str = "",
) -> Path:
    """Convert a single PDF to structured markdown.

    Opens the PDF to extract metadata and images, then calls
    :func:`extract_pdf_sections_async` for structured sections,
    optionally translates, and writes the final ``.md`` file.

    Args:
        file_path: Path to the source PDF.
        output_dir: Directory for output ``.md`` and images.
        describe_images: If True, describe images via the
            Ollama vision model.
        enable_ocr: If True, run OCR on PDF images.
        enable_translation: If True, translate Chinese sections
            to English.
        vehicle_model: Optional explicit vehicle model string.
            When provided, takes priority over all auto-detection
            (``--vehicle-model`` CLI flag).

    Returns:
        Path to the written ``.md`` file.

    Raises:
        FileNotFoundError: If *file_path* does not exist.
    """
    if not file_path.exists():
        raise FileNotFoundError(
            f"PDF file not found: {file_path}"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    stem = file_path.stem

    # Phase 1: fitz pass (metadata + images)
    doc = fitz.open(file_path)
    try:
        page_count = doc.page_count
        body_size = compute_body_font_size(doc)
        page_to_section = build_page_to_section_map(
            doc, body_size,
        )
        image_map = _extract_and_save_images(
            doc, output_dir, stem,
        )
        image_descriptions: dict[str, str] = {}
        if describe_images and image_map:
            image_descriptions = await _describe_images(
                doc, image_map,
            )
    finally:
        doc.close()

    # Free any remaining png_bytes
    for entries in image_map.values():
        for entry in entries:
            entry.pop("png_bytes", None)

    # Phase 2: section extraction
    sections = await extract_pdf_sections_async(
        file_path,
        filename=file_path.name,
        enable_ocr=enable_ocr,
    )

    # Phase 3: language detection (before translation)
    language = _detect_language(sections)

    # Phase 4: optional translation
    translated = False
    if enable_translation:
        from app.rag.translator import (
            get_translation_service,
        )

        translator = get_translation_service()
        try:
            sections = (
                await translator.translate_sections(
                    sections,
                )
            )
            translated = True
        finally:
            await translator.close()

    # Phase 5: resolve vehicle model
    resolved_model = _resolve_vehicle_model(
        sections, file_path.name, stem,
        override=vehicle_model,
    )

    # Phase 6: build mappings
    section_to_page: dict[int, int] = {}
    for page_idx, sec_idx in sorted(
        page_to_section.items(),
    ):
        if sec_idx not in section_to_page:
            section_to_page[sec_idx] = page_idx

    section_images: dict[int, list[dict]] = {}
    for page_num, entries in image_map.items():
        page_idx = page_num - 1
        sec_idx = page_to_section.get(page_idx, 0)
        section_images.setdefault(sec_idx, []).extend(
            entries,
        )

    # Phase 7: build and write markdown
    markdown = _sections_to_markdown(
        sections,
        source_pdf=file_path.name,
        vehicle_model=resolved_model,
        language=language,
        translated=translated,
        page_count=page_count,
        section_to_page=section_to_page,
        section_images=section_images,
        image_descriptions=image_descriptions,
        stem=stem,
    )

    output_path = output_dir / f"{stem}.md"
    output_path.write_text(markdown, encoding="utf-8")

    logger.info(
        "md_export.complete",
        output=str(output_path),
        sections=len(sections),
        images=sum(
            len(v) for v in image_map.values()
        ),
    )
    return output_path


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------


def _build_arg_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser.

    Returns:
        Configured ``ArgumentParser`` instance.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Convert PDF service manuals to structured "
            "markdown."
        ),
    )
    parser.add_argument(
        "--dir",
        type=str,
        required=True,
        help="Directory containing PDF files.",
    )
    parser.add_argument(
        "--output",
        type=str,
        required=True,
        help="Output directory for markdown files.",
    )
    parser.add_argument(
        "--describe-images",
        action="store_true",
        help=(
            "Describe images via Ollama vision model."
        ),
    )
    parser.add_argument(
        "--enable-ocr",
        action="store_true",
        help=(
            "Run OCR on PDF images to extract text "
            "(part numbers, torque, etc.)."
        ),
    )
    parser.add_argument(
        "--enable-translation",
        action="store_true",
        help=(
            "Translate Chinese sections to English."
        ),
    )
    parser.add_argument(
        "--vehicle-model",
        type=str,
        default="",
        help=(
            "Explicit vehicle model string "
            "(overrides auto-detection). "
            'E.g. "Honda Jazz 2016".'
        ),
    )
    return parser


async def main() -> None:
    """CLI: ``python -m app.rag.md_export --dir ... --output ...``

    Converts all PDF files in *--dir* to structured markdown
    files in *--output*.
    """
    args = _build_arg_parser().parse_args()

    data_dir = Path(args.dir)
    output_dir = Path(args.output)

    if not data_dir.exists():
        logger.error(
            "md_export.dir_not_found",
            dir=str(data_dir),
        )
        return

    pdf_files = sorted(data_dir.glob("*.pdf"))
    logger.info(
        "md_export.files_found",
        count=len(pdf_files),
    )

    for pdf_path in pdf_files:
        try:
            out = await export_pdf_to_markdown(
                pdf_path,
                output_dir,
                describe_images=args.describe_images,
                enable_ocr=args.enable_ocr,
                enable_translation=(
                    args.enable_translation
                ),
                vehicle_model=args.vehicle_model,
            )
            logger.info(
                "md_export.file_done",
                output=str(out),
            )
        except Exception as exc:
            logger.error(
                "md_export.file_error",
                file=pdf_path.name,
                error=str(exc),
            )

    logger.info("md_export.all_done")


if __name__ == "__main__":
    asyncio.run(main())
