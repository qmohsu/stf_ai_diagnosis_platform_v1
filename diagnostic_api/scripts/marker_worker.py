#!/usr/bin/env python3
"""Host-side worker that watches for marker-pdf conversion requests.

Designed to run as a systemd user service on the PolyU server
where marker-pdf (+ PyTorch) is installed via ``pip3 --user``.
The diagnostic-api container communicates via JSON files in a
shared Docker volume.

Protocol:
  1. Container writes ``{id}.request.json`` to the queue dir
  2. This worker picks it up, runs ``marker_convert.convert()``
  3. Worker writes ``{id}.result.json`` with conversion metadata
  4. Worker deletes the ``.request.json``

Only one conversion runs at a time (single-threaded, GPU-bound).

Usage::

    python3 marker_worker.py \\
        --watch-dir /path/to/volume/.queue \\
        --output-dir /path/to/volume

    # With LLM-assisted conversion
    python3 marker_worker.py \\
        --watch-dir /path/to/volume/.queue \\
        --output-dir /path/to/volume \\
        --use-llm

Systemd service:
    See ``marker-worker.service`` for the unit file.
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

# Ensure the scripts/ directory is importable so we can
# use marker_convert.convert() directly.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s [marker-worker] "
        "%(levelname)s %(message)s"
    ),
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("marker_worker")


def _process_request(
    req_path: Path,
    output_dir: str,
) -> None:
    """Process a single conversion request.

    Reads the request JSON, runs marker-pdf conversion,
    and writes a result JSON.

    Args:
        req_path: Path to the ``.request.json`` file.
        output_dir: Base output directory for converted
            markdown and images.
    """
    manual_id = req_path.stem.replace(".request", "")
    res_path = req_path.with_name(
        f"{manual_id}.result.json",
    )

    logger.info("Processing %s", req_path.name)

    try:
        with open(req_path, "r", encoding="utf-8") as f:
            request = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.error(
            "Failed to read request: %s", exc,
        )
        _write_result(res_path, {
            "status": "error",
            "message": f"Invalid request file: {exc}",
        })
        _safe_unlink(req_path)
        return

    pdf_rel = request.get("pdf_path", "")
    use_llm = request.get("use_llm", False)
    vehicle_model_subdir = request.get(
        "vehicle_model_subdir", True,
    )
    openai_api_key = request.get("openai_api_key", "")
    openai_base_url = request.get("openai_base_url", "")
    openai_model = request.get("openai_model", "")

    # Resolve the absolute PDF path.
    pdf_abs = os.path.join(output_dir, pdf_rel)
    if not os.path.isfile(pdf_abs):
        msg = f"PDF not found: {pdf_abs}"
        logger.error(msg)
        _write_result(res_path, {
            "status": "error",
            "message": msg,
        })
        _safe_unlink(req_path)
        return

    # Run marker-pdf conversion (LLM mode with fallback).
    from marker_convert import convert

    result = None
    used_llm = False

    if use_llm:
        try:
            logger.info(
                "Attempting LLM-assisted conversion "
                "(model=%s)", openai_model,
            )
            result = convert(
                pdf_path=pdf_abs,
                output_dir=output_dir,
                use_llm=True,
                openai_api_key=openai_api_key,
                openai_base_url=openai_base_url,
                openai_model=openai_model,
                vehicle_model_subdir=vehicle_model_subdir,
            )
            used_llm = True
            logger.info("LLM-assisted conversion succeeded")
        except Exception as exc:
            logger.warning(
                "LLM conversion failed, falling back to "
                "non-LLM mode: %s", exc,
            )

    if result is None:
        try:
            result = convert(
                pdf_path=pdf_abs,
                output_dir=output_dir,
                use_llm=False,
                vehicle_model_subdir=vehicle_model_subdir,
            )
        except Exception as exc:
            msg = f"Conversion failed: {exc}"
            logger.error(msg, exc_info=True)
            _write_result(res_path, {
                "status": "error",
                "message": msg[:1000],
            })
            _safe_unlink(req_path)
            return

    # Build relative output path for the container.
    rel_output = os.path.relpath(
        str(result.output_path), output_dir,
    )

    converter_label = (
        f"marker-pdf (LLM: {openai_model})"
        if used_llm
        else "marker-pdf"
    )
    if use_llm and not used_llm:
        converter_label = "marker-pdf (LLM fallback)"

    _write_result(res_path, {
        "status": "ok",
        "output_path": rel_output,
        "vehicle_model": result.vehicle_model,
        "language": result.language,
        "page_count": result.page_count,
        "section_count": result.section_count,
        "image_count": result.image_count,
        "dtc_codes": result.dtc_codes,
        "converter": converter_label,
    })
    logger.info(
        "Conversion complete: %s → %s "
        "(model=%s, pages=%d, llm=%s)",
        pdf_rel,
        rel_output,
        result.vehicle_model,
        result.page_count,
        used_llm,
    )

    # Delete the request file after processing.
    _safe_unlink(req_path)


def _write_result(path: Path, data: dict) -> None:
    """Write result JSON atomically via temp file."""
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(str(tmp), str(path))


def _safe_unlink(path: Path) -> None:
    """Delete a file, ignoring errors."""
    try:
        os.unlink(path)
    except OSError:
        pass


def main() -> None:
    """Watch the queue directory and process requests."""
    parser = argparse.ArgumentParser(
        description="Marker-pdf conversion worker.",
    )
    parser.add_argument(
        "--watch-dir", required=True,
        help="Directory to watch for .request.json files.",
    )
    parser.add_argument(
        "--output-dir", required=True,
        help=(
            "Base output directory for converted files "
            "(same as the Docker volume root)."
        ),
    )
    parser.add_argument(
        "--poll-interval", type=float, default=2.0,
        help="Seconds between queue directory scans.",
    )
    parser.add_argument(
        "--use-llm", action="store_true",
        help="Enable LLM-assisted conversion globally.",
    )
    args = parser.parse_args()

    watch_dir = Path(args.watch_dir)
    watch_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Started. Watching %s (poll=%.1fs)",
        watch_dir, args.poll_interval,
    )

    while True:
        try:
            requests = sorted(
                watch_dir.glob("*.request.json"),
            )
            for req_path in requests:
                _process_request(req_path, args.output_dir)
        except Exception as exc:
            logger.error(
                "Scan error: %s", exc, exc_info=True,
            )

        time.sleep(args.poll_interval)


if __name__ == "__main__":
    main()
