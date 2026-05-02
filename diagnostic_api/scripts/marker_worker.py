#!/usr/bin/env python3
"""Host-side worker that watches for marker-pdf conversion requests.

Designed to run as a systemd user service on the PolyU server
where marker-pdf (+ PyTorch) is installed via ``pip3 --user``.
The diagnostic-api container communicates via JSON files in a
shared Docker volume.

Protocol:
  1. Container writes ``{id}.request.json`` to the queue dir
  2. This worker picks it up, runs ``marker_convert.convert()``
  3. While converting, worker writes ``{id}.progress.json`` with
     ``{processed, total, phase}`` so the API polling loop can
     surface per-page progress in the UI
  4. Worker writes ``{id}.result.json`` with conversion metadata
  5. Worker deletes the ``.request.json`` and ``.progress.json``

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
from typing import Optional

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


# Minimum seconds between consecutive .progress.json writes.
# Throttles fs hammering on large PDFs where pages tick rapidly.
_PROGRESS_THROTTLE_SECONDS = 1.5


class _ProgressReporter:
    """Atomic-write helper for ``{manual_id}.progress.json``.

    Throttled to at most one write per page or per
    ``_PROGRESS_THROTTLE_SECONDS`` seconds, whichever is longer.
    Atomic = write to ``.tmp`` then ``os.replace`` so the API
    never reads a half-written file.
    """

    def __init__(self, progress_path: Path) -> None:
        """Initialize the reporter.

        Args:
            progress_path: Final path of the ``.progress.json``
                file to write to.
        """
        self._path = progress_path
        self._tmp_path = progress_path.with_suffix(".tmp")
        self._last_write_ts: float = 0.0
        self._last_processed: int = -1

    def report(
        self,
        processed: int,
        total: int,
        phase: str = "",
        force: bool = False,
    ) -> None:
        """Write a progress update if not throttled.

        Args:
            processed: Number of pages processed so far.
            total: Total pages (best-known estimate).
            phase: Optional phase label
                (``layout`` / ``ocr`` / ``llm`` / ``table`` / ``done``).
            force: Bypass the throttle (used at conversion start
                and when the page counter advances).
        """
        now = time.monotonic()
        page_advanced = processed > self._last_processed
        time_elapsed = (
            now - self._last_write_ts
            >= _PROGRESS_THROTTLE_SECONDS
        )

        # Skip unless: forced, or both a new page AND throttle expired,
        # or first ever write (last_write_ts == 0.0 implies neither).
        if not force:
            if not (page_advanced and time_elapsed):
                return

        payload: dict = {
            "processed": int(processed),
            "total": int(total),
        }
        if phase:
            payload["phase"] = phase

        try:
            with open(
                self._tmp_path, "w", encoding="utf-8",
            ) as f:
                json.dump(payload, f)
            os.replace(str(self._tmp_path), str(self._path))
            self._last_write_ts = now
            self._last_processed = processed
        except OSError as exc:
            logger.warning(
                "progress write failed: %s", exc,
            )


def _install_tqdm_hook(
    reporter: _ProgressReporter,
) -> "Optional[object]":
    """Monkey-patch tqdm so each ``update()`` writes progress.

    Marker-pdf does not expose a clean per-page callback hook on
    ``PdfConverter``; its internal processors drive a ``tqdm``
    progress bar instead.  We wrap ``tqdm.tqdm.update`` (which is
    the parent class used by every ``trange`` and bare-``tqdm``)
    so that each tick mirrors to our progress file.

    The first ``tqdm`` instance we see drives the progress
    estimate.  We use the bar's ``total`` as ``pages_total`` and
    its running ``n`` as ``pages_processed``.  This is good
    enough for a UI ETA — the page count from marker's final
    ``result.json`` is the authoritative number.

    Returns:
        The original ``tqdm.update`` method so the caller can
        restore it after conversion.  ``None`` if tqdm is not
        importable (in which case progress reporting is a no-op).
    """
    try:
        import tqdm as _tqdm_mod
    except ImportError:
        logger.warning(
            "tqdm not installed; progress reporting disabled",
        )
        return None

    original_update = _tqdm_mod.tqdm.update

    def _patched_update(self, n: int = 1) -> None:
        # Defer to the real implementation first so ``self.n``
        # reflects post-update state.
        result = original_update(self, n)
        try:
            total = getattr(self, "total", None)
            current = getattr(self, "n", None)
            if total and current is not None:
                reporter.report(
                    processed=int(current),
                    total=int(total),
                )
        except (TypeError, ValueError):
            # Defensive: never let progress reporting break the
            # underlying conversion.
            pass
        return result

    _tqdm_mod.tqdm.update = _patched_update
    return original_update


def _restore_tqdm_hook(original_update: "Optional[object]") -> None:
    """Undo the tqdm monkey-patch.

    Args:
        original_update: The pre-patch ``tqdm.update`` method,
            as returned by ``_install_tqdm_hook``.  May be
            ``None`` if the install was a no-op.
    """
    if original_update is None:
        return
    try:
        import tqdm as _tqdm_mod
        _tqdm_mod.tqdm.update = original_update
    except ImportError:
        pass


def _process_request(
    req_path: Path,
    output_dir: str,
) -> None:
    """Process a single conversion request.

    Reads the request JSON, runs marker-pdf conversion,
    and writes a result JSON.  While converting, also writes a
    ``.progress.json`` file with per-page progress, then removes
    it once the result file is committed.

    Args:
        req_path: Path to the ``.request.json`` file.
        output_dir: Base output directory for converted
            markdown and images.
    """
    manual_id = req_path.stem.replace(".request", "")
    res_path = req_path.with_name(
        f"{manual_id}.result.json",
    )
    progress_path = req_path.with_name(
        f"{manual_id}.progress.json",
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
        _safe_unlink(progress_path)
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
        _safe_unlink(progress_path)
        return

    # Install the tqdm progress hook before any marker import
    # walks the bar.  Total is unknown until marker reports it,
    # so we don't write an initial 0/0 stub.
    reporter = _ProgressReporter(progress_path)
    original_update = _install_tqdm_hook(reporter)

    # Run marker-pdf conversion (LLM mode with fallback).
    from marker_convert import convert

    result = None
    used_llm = False

    try:
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
                _safe_unlink(progress_path)
                return
    finally:
        _restore_tqdm_hook(original_update)

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

    # Final progress report so the UI shows N/N briefly before
    # the API picks up the result file and transitions to
    # 'chunking'.
    if result.page_count > 0:
        reporter.report(
            processed=result.page_count,
            total=result.page_count,
            phase="done",
            force=True,
        )

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

    # Delete the request + progress files after processing.
    # The API polling loop also cleans up progress on its side
    # but we belt-and-braces here in case the API is slow.
    _safe_unlink(req_path)
    _safe_unlink(progress_path)


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
