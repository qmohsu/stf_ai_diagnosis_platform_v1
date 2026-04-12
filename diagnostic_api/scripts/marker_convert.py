#!/usr/bin/env python3
"""Convert a PDF to structured markdown using marker-pdf.

Standalone script for evaluating marker-pdf as a replacement for the
PyMuPDF pipeline (Issue #49).  Supports LLM-assisted conversion via
any OpenAI-compatible API endpoint (e.g. OpenRouter).

The output conforms to the manual viewer schema: a ``.md`` file with
YAML frontmatter plus an ``images/{stem}/`` subdirectory.

Usage::

    # Without LLM (layout models only)
    python marker_convert.py \\
        --pdf /path/to/manual.pdf \\
        --output /app/data/manuals

    # With LLM via OpenRouter
    python marker_convert.py \\
        --pdf /path/to/manual.pdf \\
        --output /app/data/manuals \\
        --use-llm \\
        --openai-base-url https://openrouter.ai/api/v1 \\
        --openai-api-key "$OPENROUTER_API_KEY" \\
        --openai-model qwen/qwen3.5-flash-02-23

    # With custom suffix (to keep both PyMuPDF and marker versions)
    python marker_convert.py \\
        --pdf /path/to/manual.pdf \\
        --output /app/data/manuals \\
        --suffix _marker_llm \\
        --use-llm ...

Dependencies::

    pip install 'marker-pdf>=1.10.2' PyMuPDF
"""

import argparse
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ConversionResult:
    """Metadata returned after a successful conversion."""

    output_path: Path
    vehicle_model: str
    language: str
    page_count: int
    section_count: int
    image_count: int
    dtc_codes: list[str] = field(default_factory=list)


# ── Vehicle-model detection (copied from app.rag.parser) ────────
_VEHICLE_MODEL_PATTERNS = [
    (re.compile(r"\bSTF[-\s]?\d{3,4}\b", re.I), "STF-{digits}"),
    (re.compile(r"\bMWS[-\s]?\d{2,4}[-\s]?[A-Z]?\b", re.I), "{raw}"),
    (re.compile(r"\bTRICITY\s*\d{2,3}\b", re.I), "{raw}"),
    (re.compile(r"\bNMAX\s*\d{2,3}\b", re.I), "{raw}"),
    (re.compile(r"\bXMAX\s*\d{2,3}\b", re.I), "{raw}"),
]

_MANUAL_SUFFIX_RE = re.compile(
    r"[_\-\s]+"
    r"(?:Owners?|Service|Workshop|Repair|Shop|"
    r"Maintenance|Factory|Technical|User)"
    r"(?:[_\-\s]+(?:Manual|Guide|Handbook|Book))?"
    r"$",
    re.I,
)

_DTC_RE = re.compile(r"\b[PBCU]\d{4}\b")


def _extract_vehicle_model(text: str) -> str:
    """Return normalised vehicle-model string or 'Generic'."""
    for pattern, fmt in _VEHICLE_MODEL_PATTERNS:
        m = pattern.search(text)
        if m:
            raw = re.sub(r"[-\s]+", "-", m.group().strip()).upper()
            digits = (re.search(r"\d{2,4}", raw) or type(
                "X", (), {"group": lambda self: ""},
            )()).group()
            return fmt.format(raw=raw, digits=digits)
    return "Generic"


def _clean_filename_stem(stem: str) -> str:
    """Strip manual-related suffixes from a filename stem."""
    cleaned = _MANUAL_SUFFIX_RE.sub("", stem)
    cleaned = re.sub(r"[_\-]+", " ", cleaned).strip()
    return cleaned if cleaned else stem


def _resolve_vehicle_model(
    md_text: str,
    filename: str,
    stem: str,
) -> str:
    """Best-effort vehicle-model resolution."""
    model = _extract_vehicle_model(md_text[:5000])
    if model != "Generic":
        return model
    model = _extract_vehicle_model(filename)
    if model != "Generic":
        return model
    return _clean_filename_stem(stem)


def _has_cjk(text: str) -> bool:
    """Return True if *text* contains CJK ideographs."""
    for ch in text[:3000]:
        if "\u2e80" <= ch <= "\u9fff" or "\uf900" <= ch <= "\ufaff":
            return True
    return False


def _yaml_escape(value: str) -> str:
    """Quote a YAML value if it contains special chars."""
    if re.search(r"[:{}\[\]#&*!|>'\"%@`,\n\r]", value):
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _build_frontmatter(
    *,
    source_pdf: str,
    vehicle_model: str,
    language: str,
    page_count: int,
    section_count: int,
    converter: str,
) -> str:
    """Build YAML frontmatter block for the manual viewer."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lines = [
        "---",
        f"source_pdf: {_yaml_escape(source_pdf)}",
        f"vehicle_model: {_yaml_escape(vehicle_model)}",
        f"language: {_yaml_escape(language)}",
        f'exported_at: "{now}"',
        f"page_count: {page_count}",
        f"section_count: {section_count}",
        f"converter: {converter}",
        "---",
    ]
    return "\n".join(lines)


def _count_headings(md_text: str) -> int:
    """Count top-level (##) headings in markdown."""
    return len(re.findall(r"^##\s", md_text, re.MULTILINE))


def _build_dtc_index(md_text: str) -> str | None:
    """Build a DTC cross-reference appendix if codes exist."""
    codes = sorted(set(_DTC_RE.findall(md_text)))
    if not codes:
        return None
    lines = [
        "\n## Appendix: DTC Index\n",
        "| DTC | Occurrences |",
        "|-----|-------------|",
    ]
    for code in codes:
        count = len(re.findall(re.escape(code), md_text))
        lines.append(f"| {code} | {count} |")
    return "\n".join(lines)


def _get_page_count(pdf_path: str) -> int:
    """Get PDF page count via PyMuPDF."""
    import fitz
    doc = fitz.open(pdf_path)
    count = doc.page_count
    doc.close()
    return count


def _rewrite_image_paths(
    md_text: str,
    stem: str,
    suffix: str,
) -> str:
    """Rewrite marker's image paths to match viewer convention.

    marker outputs ``![alt](image_name.ext)`` with images saved
    alongside the markdown.  The viewer expects
    ``![alt](images/{stem}/image_name.ext)``.
    """
    def _replace(m: re.Match) -> str:
        alt = m.group(1)
        img_path = m.group(2)
        # Skip already-absolute or URL paths
        if img_path.startswith(("http", "/", "images/")):
            return m.group(0)
        return f"![{alt}](images/{stem}{suffix}/{img_path})"

    return re.sub(
        r"!\[([^\]]*)\]\(([^)]+)\)",
        _replace,
        md_text,
    )


def convert(
    pdf_path: str,
    output_dir: str,
    *,
    use_llm: bool = False,
    openai_base_url: str = "",
    openai_api_key: str = "",
    openai_model: str = "",
    suffix: str = "",
    vehicle_model_subdir: bool = False,
) -> ConversionResult:
    """Convert a PDF using marker-pdf and post-process.

    Args:
        pdf_path: Path to source PDF.
        output_dir: Base directory for output .md + images/.
        use_llm: Enable LLM-assisted conversion.
        openai_base_url: OpenAI-compatible API base URL.
        openai_api_key: API key for the LLM service.
        openai_model: Model identifier.
        suffix: Filename suffix (e.g. '_marker_llm').
        vehicle_model_subdir: If True, nest output under
            ``{output_dir}/{vehicle_model}/``.

    Returns:
        ConversionResult with output path and metadata.
    """
    from marker.converters.pdf import PdfConverter
    from marker.config.parser import ConfigParser
    from marker.models import create_model_dict
    from marker.output import text_from_rendered

    pdf_path_obj = Path(pdf_path)
    base_dir = Path(output_dir)
    base_dir.mkdir(parents=True, exist_ok=True)

    stem = pdf_path_obj.stem

    # ── Build marker config ──────────────────────────────
    config: dict = {
        "output_format": "markdown",
        "paginate_output": True,
    }
    if use_llm:
        config["use_llm"] = True
        if openai_api_key:
            config["openai_api_key"] = openai_api_key
        if openai_base_url:
            config["openai_base_url"] = openai_base_url
        if openai_model:
            config["openai_model"] = openai_model

    config_parser = ConfigParser(config)

    logger.info("Loading marker models...")
    t0 = time.time()
    artifact_dict = create_model_dict()
    logger.info(
        "Models loaded in %.1fs", time.time() - t0,
    )

    llm_service = None
    if use_llm:
        llm_service = (
            "marker.services.openai.OpenAIService"
        )

    converter = PdfConverter(
        config=config_parser.generate_config_dict(),
        artifact_dict=artifact_dict,
        processor_list=config_parser.get_processors(),
        renderer=config_parser.get_renderer(),
        llm_service=llm_service,
    )

    # ── Run conversion ───────────────────────────────────
    logger.info("Converting %s...", pdf_path_obj.name)
    t0 = time.time()
    rendered = converter(str(pdf_path_obj))
    elapsed = time.time() - t0
    logger.info(
        "Conversion done in %.1fs", elapsed,
    )

    text, ext, images = text_from_rendered(rendered)

    # ── Resolve vehicle model early (needed for subdir) ──
    md_text = text
    page_count = _get_page_count(str(pdf_path_obj))
    vehicle_model = _resolve_vehicle_model(
        md_text, pdf_path_obj.name, stem,
    )

    # Determine actual output directory
    if vehicle_model_subdir:
        out_dir = base_dir / vehicle_model
    else:
        out_dir = base_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── Save images ──────────────────────────────────────
    img_dir = out_dir / "images" / f"{stem}{suffix}"
    if images:
        img_dir.mkdir(parents=True, exist_ok=True)
        for img_name, img_obj in images.items():
            img_path = img_dir / img_name
            if hasattr(img_obj, "save"):
                # PIL Image — convert RGBA to RGB for JPEG
                if img_obj.mode == "RGBA":
                    img_obj = img_obj.convert("RGB")
                img_obj.save(str(img_path))
            else:
                # Raw bytes fallback
                img_path.write_bytes(img_obj)
        logger.info(
            "Saved %d images to %s",
            len(images), img_dir,
        )

    # ── Post-process markdown ────────────────────────────
    md_text = _rewrite_image_paths(md_text, stem, suffix)

    language = "zh-CN" if _has_cjk(md_text) else "en"
    section_count = _count_headings(md_text)
    converter_label = (
        f"marker-pdf (LLM: {openai_model})"
        if use_llm
        else "marker-pdf"
    )

    # Build frontmatter
    frontmatter = _build_frontmatter(
        source_pdf=pdf_path_obj.name,
        vehicle_model=vehicle_model,
        language=language,
        page_count=page_count,
        section_count=section_count,
        converter=converter_label,
    )

    # Build DTC index
    dtc_index = _build_dtc_index(md_text)
    dtc_codes = sorted(set(_DTC_RE.findall(md_text)))

    # Assemble final markdown
    final = frontmatter + "\n\n" + md_text
    if dtc_index:
        final += "\n" + dtc_index + "\n"

    # ── Write output ─────────────────────────────────────
    output_path = out_dir / f"{stem}{suffix}.md"
    output_path.write_text(final, encoding="utf-8")

    logger.info(
        "Output: %s  model=%s  lang=%s  "
        "pages=%d  sections=%d  images=%d",
        output_path, vehicle_model, language,
        page_count, section_count, len(images),
    )

    return ConversionResult(
        output_path=output_path,
        vehicle_model=vehicle_model,
        language=language,
        page_count=page_count,
        section_count=section_count,
        image_count=len(images),
        dtc_codes=dtc_codes,
    )


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description=(
            "Convert PDF to structured markdown using "
            "marker-pdf (Issue #49 evaluation)."
        ),
    )
    parser.add_argument(
        "--pdf", required=True,
        help="Path to source PDF file.",
    )
    parser.add_argument(
        "--output", required=True,
        help=(
            "Output directory for .md + images/. "
            "Typically /app/data/manuals."
        ),
    )
    parser.add_argument(
        "--suffix", default="",
        help=(
            "Filename suffix, e.g. '_marker_llm'. "
            "Output will be {stem}{suffix}.md"
        ),
    )
    parser.add_argument(
        "--use-llm", action="store_true",
        help="Enable LLM-assisted conversion.",
    )
    parser.add_argument(
        "--openai-base-url", default="",
        help=(
            "Base URL for OpenAI-compatible API. "
            "E.g. https://openrouter.ai/api/v1"
        ),
    )
    parser.add_argument(
        "--openai-api-key", default="",
        help=(
            "API key. Can also be set via "
            "OPENAI_API_KEY env var."
        ),
    )
    parser.add_argument(
        "--openai-model", default="",
        help=(
            "Model identifier. "
            "E.g. qwen/qwen3.5-flash-02-23"
        ),
    )
    args = parser.parse_args()

    # Fallback: read API key from env
    api_key = (
        args.openai_api_key
        or os.getenv("OPENAI_API_KEY", "")
        or os.getenv("OPENROUTER_API_KEY", "")
        or os.getenv("PREMIUM_LLM_API_KEY", "")
    )

    if args.use_llm and not api_key:
        print(
            "ERROR: --use-llm requires an API key. "
            "Pass --openai-api-key or set "
            "OPENAI_API_KEY / PREMIUM_LLM_API_KEY.",
            file=sys.stderr,
        )
        sys.exit(1)

    result = convert(
        pdf_path=args.pdf,
        output_dir=args.output,
        use_llm=args.use_llm,
        openai_base_url=args.openai_base_url,
        openai_api_key=api_key,
        openai_model=args.openai_model,
        suffix=args.suffix,
    )
    print(f"Output: {result.output_path}")
    print(f"Vehicle model: {result.vehicle_model}")
    print(f"Language: {result.language}")
    print(f"Pages: {result.page_count}")
    print(f"Sections: {result.section_count}")
    print(f"Images: {result.image_count}")
    if result.dtc_codes:
        print(f"DTC codes: {len(result.dtc_codes)}")


if __name__ == "__main__":
    main()
