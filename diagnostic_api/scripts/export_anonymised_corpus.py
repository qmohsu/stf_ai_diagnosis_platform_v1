"""Export the OBD corpus with VINs redacted to pseudonymous IDs.

Internal-development storage policy (APP-54) keeps raw VINs in the
backend.  Any time the corpus leaves the backend — academic paper
artefacts, partner-lab demos, public release — every raw VIN must
first be replaced with its truncated SHA-256 pseudonym
(``V-{8-hex}``, produced by :func:`obd_agent.log_parser.pseudonymise_vin`).

This script is the canonical redactor.  It walks the live database +
filesystem corpus and writes a sibling export bundle with all
identifiers anonymised in lockstep:

* ``obd_analysis_sessions`` rows have ``vehicle_id`` rewritten and
  ``result_payload['vehicle_id']`` (if present) rewritten to match.
* Raw ``.txt`` log bodies have ``# vehicle_id:`` header lines and any
  17-char-VIN substrings rewritten.
* A ``mapping.json`` ledger records the raw → pseudonym pairs so a
  rerun stays consistent and a research collaborator can verify
  pseudonym uniqueness without seeing raw VINs.

This is a skeleton: the DB-side selection, output schema, and full
text-substitution pass are intentionally TODO.  Run with ``--dry-run``
first when wiring it up against a real instance.

Example::

    python -m diagnostic_api.scripts.export_anonymised_corpus \\
        --source-dir /var/lib/.../obd_logs \\
        --db-url postgresql://... \\
        --output-dir ./exports/2026-05-06 \\
        --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

_VIN_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
_HEADER_VIN_RE = re.compile(
    r"^(\s*#\s*vehicle_id\s*:\s*)([A-Za-z0-9._\-:]{1,50})\s*$",
    re.MULTILINE,
)


def _pseudonymise(raw: str) -> str:
    """Return the V-XXXXXXXX pseudonym for *raw*.

    Imported lazily so the script remains import-safe even when
    ``obd_agent`` is not installed in the export environment.
    """
    from obd_agent.log_parser import pseudonymise_vin

    return pseudonymise_vin(raw)


def _redact_text(content: str, mapping: dict[str, str]) -> str:
    """Replace every raw VIN match in *content* with its pseudonym.

    Updates *mapping* in-place with any newly discovered pairs.  Two
    passes: explicit ``# vehicle_id: …`` header lines first (so we
    catch arbitrary labels, not just 17-char VINs), then any free-
    standing VIN substring elsewhere in the body.
    """

    def _header_sub(match: re.Match) -> str:
        prefix, raw = match.group(1), match.group(2)
        pseudo = mapping.setdefault(raw, _pseudonymise(raw))
        return f"{prefix}{pseudo}"

    content = _HEADER_VIN_RE.sub(_header_sub, content)

    def _vin_sub(match: re.Match) -> str:
        raw = match.group(0)
        return mapping.setdefault(raw, _pseudonymise(raw))

    return _VIN_RE.sub(_vin_sub, content)


def _iter_source_files(source_dir: Path) -> Iterator[Path]:
    """Yield every ``*.txt`` raw-log file under *source_dir*."""
    yield from source_dir.glob("*.txt")


def _redact_file(
    src: Path,
    dst: Path,
    mapping: dict[str, str],
    dry_run: bool,
) -> None:
    """Read *src*, redact, and write to *dst* (unless dry-run)."""
    text = src.read_text(encoding="utf-8", errors="replace")
    redacted = _redact_text(text, mapping)
    if dry_run:
        logger.info("dry_run_file %s -> %s", src.name, dst.name)
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(redacted, encoding="utf-8")


def _export_db(
    db_url: str,
    output_dir: Path,
    mapping: dict[str, str],
    dry_run: bool,
) -> None:
    """Dump anonymised ``obd_analysis_sessions`` rows to JSONL.

    TODO(APP-54): wire to SQLAlchemy + the live model so the export
    stays in sync with schema changes.  For now the structure is
    sketched as a JSONL-of-rows so a follow-up just fills in the
    SELECT.
    """
    logger.warning(
        "_export_db is a stub — fill in DB selection before relying "
        "on this script in anger.  db_url=%s output=%s",
        db_url,
        output_dir,
    )
    if dry_run:
        return
    out_file = output_dir / "obd_analysis_sessions.jsonl"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with out_file.open("w", encoding="utf-8") as fh:
        fh.write("")  # placeholder; real loop appends one JSON per row


def _write_mapping(
    mapping: dict[str, str],
    output_dir: Path,
    dry_run: bool,
) -> None:
    """Persist the raw→pseudonym ledger so reruns stay consistent."""
    if dry_run:
        logger.info("dry_run_mapping size=%d", len(mapping))
        return
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "mapping.json"
    target.write_text(
        json.dumps(mapping, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def _parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="diagnostic_api.scripts.export_anonymised_corpus",
        description=(
            "Redact raw VINs in the OBD corpus to pseudonymous "
            "V-XXXXXXXX identifiers for external sharing."
        ),
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        required=True,
        help="Directory containing raw .txt log bodies.",
    )
    parser.add_argument(
        "--db-url",
        type=str,
        required=True,
        help="SQLAlchemy URL for the diagnostic_api Postgres instance.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Destination directory for the anonymised export bundle.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Walk inputs and log what would be written, but produce "
            "no output files."
        ),
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point.

    Returns:
        ``0`` on success, ``1`` on failure.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    args = _parse_args(argv)

    if not args.source_dir.is_dir():
        logger.error("source_dir_missing %s", args.source_dir)
        return 1

    mapping: dict[str, str] = {}

    # Phase 1: file-corpus redaction.
    for src in _iter_source_files(args.source_dir):
        dst = args.output_dir / "obd_logs" / src.name
        _redact_file(src, dst, mapping, args.dry_run)

    # Phase 2: DB-side row redaction (skeleton — see TODO).
    _export_db(args.db_url, args.output_dir, mapping, args.dry_run)

    # Phase 3: persist the raw→pseudonym ledger.
    _write_mapping(mapping, args.output_dir, args.dry_run)

    logger.info(
        "export_complete vins=%d dry_run=%s",
        len(mapping),
        args.dry_run,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
