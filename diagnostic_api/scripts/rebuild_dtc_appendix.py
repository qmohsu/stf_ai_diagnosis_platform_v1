#!/usr/bin/env python3
"""Rebuild the DTC appendix on an existing converted manual.

Cheap, idempotent post-processing step that re-applies the latest
``_DTC_RE`` and ``_build_dtc_index`` from :mod:`marker_convert` to a
markdown file already produced by ``marker_convert.py``.  Avoids the
LLM-billed cost of a full PDF re-conversion when only the appendix
generation logic has changed.

Behaviour:
    1. Reads the target ``.md`` file.
    2. Strips any existing ``## Appendix: DTC Index`` block (and
       everything after it, since the appendix is always last).
    3. Rebuilds the appendix from the body using the current regex.
    4. Writes the file back atomically.

Usage::

    python rebuild_dtc_appendix.py --md /app/data/manuals/<model>/<id>.md

    # Dry-run (don't write, just diff stats)
    python rebuild_dtc_appendix.py --md ... --dry-run

    # Whole directory
    python rebuild_dtc_appendix.py --dir /app/data/manuals
"""

import argparse
import logging
import os
import sys
import tempfile
from pathlib import Path

# Allow running from anywhere by making the sibling module importable.
_THIS_DIR = Path(__file__).resolve().parent
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from marker_convert import _DTC_RE  # noqa: E402
from marker_convert import _build_dtc_index  # noqa: E402
from marker_convert import _normalize_dtc  # noqa: E402

logger = logging.getLogger(__name__)

_APPENDIX_HEADING = "## Appendix: DTC Index"


def _strip_existing_appendix(md_text: str) -> str:
    """Return *md_text* with any trailing DTC appendix removed."""
    idx = md_text.rfind(_APPENDIX_HEADING)
    if idx < 0:
        return md_text
    return md_text[:idx].rstrip() + "\n"


def _rebuild_one(
    md_path: Path,
    *,
    dry_run: bool = False,
) -> tuple[int, int, list[str]]:
    """Rebuild the DTC appendix for a single markdown file.

    Args:
        md_path: Path to the markdown file to rewrite.
        dry_run: If True, do not write the file back.

    Returns:
        Tuple of (old_unique_count, new_unique_count, new_codes_added).
    """
    text = md_path.read_text(encoding="utf-8")
    body = _strip_existing_appendix(text)

    # Count what was previously listed (best-effort: parse any old
    # appendix table to recover the old code set, so we can diff).
    old_codes: set[str] = set()
    idx = text.rfind(_APPENDIX_HEADING)
    if idx >= 0:
        for line in text[idx:].splitlines():
            stripped = line.strip()
            if not stripped.startswith("|"):
                continue
            cell = stripped.split("|")[1].strip() if "|" in stripped else ""
            if cell and cell.upper() != "DTC" and not cell.startswith("-"):
                old_codes.add(_normalize_dtc(cell))

    new_codes = sorted({
        _normalize_dtc(c) for c in _DTC_RE.findall(body)
    })
    new_appendix = _build_dtc_index(body)

    if new_appendix:
        final = body.rstrip() + "\n" + new_appendix + "\n"
    else:
        final = body

    added = sorted(set(new_codes) - old_codes)

    if not dry_run and final != text:
        # Atomic write: tmp file in same dir, then os.replace.
        # Preserve the source file's mode/uid/gid — `tempfile`
        # defaults to 0600, which would lock out the Nginx user
        # serving these files at /manuals/data/.
        try:
            src_stat = md_path.stat()
        except FileNotFoundError:
            src_stat = None
        tmp = tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=str(md_path.parent),
            delete=False,
            suffix=".tmp",
        )
        try:
            tmp.write(final)
            tmp.close()
            if src_stat is not None:
                try:
                    os.chmod(tmp.name, src_stat.st_mode & 0o777)
                except OSError:
                    pass
                if hasattr(os, "chown"):
                    try:
                        os.chown(
                            tmp.name,
                            src_stat.st_uid,
                            src_stat.st_gid,
                        )
                    except (OSError, PermissionError):
                        pass
            os.replace(tmp.name, md_path)
        except Exception:
            Path(tmp.name).unlink(missing_ok=True)
            raise

    return len(old_codes), len(new_codes), added


def _iter_manuals(root: Path) -> list[Path]:
    """Find ``.md`` files under *root* (excluding the uploads dir)."""
    out: list[Path] = []
    for p in root.rglob("*.md"):
        if "uploads" in p.parts:
            continue
        out.append(p)
    return out


def main() -> None:
    """CLI entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    parser = argparse.ArgumentParser(
        description=(
            "Rebuild DTC appendix in an existing converted manual."
        ),
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--md",
        help="Path to a single .md file.",
    )
    group.add_argument(
        "--dir",
        help=(
            "Root directory containing converted manuals "
            "(e.g. /app/data/manuals). Recurses into subdirs."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute diff but do not write files.",
    )
    args = parser.parse_args()

    if args.md:
        targets = [Path(args.md)]
    else:
        targets = _iter_manuals(Path(args.dir))

    if not targets:
        logger.warning("No markdown files found.")
        sys.exit(0)

    grand_added: list[str] = []
    for md_path in targets:
        try:
            old_n, new_n, added = _rebuild_one(
                md_path, dry_run=args.dry_run,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "Failed on %s: %s", md_path, exc,
            )
            continue
        verb = "WOULD UPDATE" if args.dry_run else "UPDATED"
        logger.info(
            "%s %s  old=%d new=%d added=%s",
            verb,
            md_path,
            old_n,
            new_n,
            added if added else "[]",
        )
        grand_added.extend(added)

    if grand_added:
        logger.info(
            "Total newly-surfaced DTC codes across run: %d (%s)",
            len(grand_added),
            ", ".join(sorted(set(grand_added))),
        )


if __name__ == "__main__":
    main()
