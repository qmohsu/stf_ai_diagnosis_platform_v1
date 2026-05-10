"""Sync golden Q&A entries from JSONL files into the DB.

The JSONL files under
``tests/harness/evals/golden/v2/*.jsonl`` are the canonical
source of truth for the eval suite.  This module mirrors them
into the ``golden_entries`` table on app startup so the
dashboard can serve / filter / aggregate without re-parsing the
files on every request, and so reviews have a stable foreign-key
target.

Behaviour:

- Idempotent: re-running upserts based on entry ``id``.
- Tolerant of malformed lines: a parse error on one line is
  logged and that entry is skipped; the rest still sync.
- Bilingual fields are nullable in the DB; the JSONL fields
  ``question_zh`` / ``golden_summary_zh`` are optional and
  default to None when absent.

Author: Li-Ta Hsu
Date: May 2026
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import structlog
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models_db import GoldenEntry

logger = structlog.get_logger(__name__)


# Path to the canonical golden directory, relative to the
# repo root.  In production (containerised) this resolves to
# ``/app/tests/harness/evals/golden/v2/``.
_GOLDEN_V2_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "tests" / "harness" / "evals" / "golden" / "v2"
)


# ── Field extraction helpers ─────────────────────────────────


def _extract_entry_fields(
    raw: Dict[str, Any],
    source_path: str,
    line_number: int,
) -> Optional[Dict[str, Any]]:
    """Pull the fields we care about out of a raw JSONL line.

    Returns a dict suitable for INSERT/UPDATE on
    ``golden_entries``, or ``None`` if the line is malformed
    enough that we should skip it (and warn).

    Args:
        raw: Parsed JSON object from one JSONL line.
        source_path: Source file path (relative to repo root).
        line_number: 1-based line number for round-trip.

    Returns:
        Dict of column-value pairs, or ``None`` to skip.
    """
    entry_id = raw.get("id")
    if not entry_id:
        logger.warning(
            "golden_sync.skip_no_id",
            source_path=source_path,
            line=line_number,
        )
        return None

    # Required fields — bail with a warning if missing.
    # Note: manual_id is derived from the first golden_citation
    # rather than expected at the top level (the JSONL schema
    # nests it inside citations).
    required = (
        "category", "question_type",
        "difficulty", "question", "golden_summary",
        "golden_citations",
    )
    for field in required:
        if field not in raw:
            logger.warning(
                "golden_sync.skip_missing_field",
                entry_id=entry_id,
                missing=field,
            )
            return None

    citations = raw.get("golden_citations") or []
    manual_id = ""
    if isinstance(citations, list) and citations:
        first = citations[0]
        if isinstance(first, dict):
            manual_id = str(first.get("manual_id", ""))
    if not manual_id:
        # Adversarial entries can have empty citations; fall
        # back to a sentinel so they still upsert and the
        # dashboard can render them.
        manual_id = raw.get("manual_id", "") or "(none)"

    return {
        "id": entry_id,
        "manual_id": manual_id,
        "category": raw["category"],
        "question_type": raw["question_type"],
        "difficulty": raw["difficulty"],
        "question_en": raw["question"],
        # Bilingual fields — nullable, None if absent.
        "question_zh": raw.get("question_zh"),
        "obd_context": raw.get("obd_context"),
        "golden_summary_en": raw["golden_summary"],
        "golden_summary_zh": raw.get("golden_summary_zh"),
        "golden_citations": raw["golden_citations"],
        "expected_recall_slugs": raw.get(
            "expected_recall_slugs", [],
        ),
        "must_contain": raw.get("must_contain", []),
        "pitfall_directives": raw.get(
            "pitfall_directives", [],
        ),
        "requires_image": bool(
            raw.get("requires_image", False),
        ),
        "notes": raw.get("notes"),
        "source_jsonl_path": source_path,
        "source_jsonl_line": line_number,
    }


def _iter_jsonl_files(
    root: Path,
) -> Iterator[Tuple[Path, int, Dict[str, Any]]]:
    """Walk ``*.jsonl`` files under root, yielding parsed lines.

    Yields ``(path, line_number, parsed_dict)`` tuples.  Lines
    that fail to parse are logged and skipped.

    The candidates directory is excluded — those are author
    drafts not yet promoted into the canonical set.

    Args:
        root: Directory to walk.

    Yields:
        Tuples of (path, 1-based line number, parsed JSON dict).
    """
    if not root.is_dir():
        logger.warning(
            "golden_sync.root_missing",
            root=str(root),
        )
        return

    for jsonl_path in sorted(root.glob("*.jsonl")):
        # Skip files under candidates/ — those are drafts.
        if "candidates" in jsonl_path.parts:
            continue
        try:
            with jsonl_path.open(encoding="utf-8") as f:
                for line_no, line in enumerate(f, start=1):
                    text = line.strip()
                    if not text:
                        continue
                    try:
                        parsed = json.loads(text)
                    except json.JSONDecodeError as exc:
                        logger.warning(
                            "golden_sync.skip_malformed",
                            path=str(jsonl_path),
                            line=line_no,
                            error=str(exc),
                        )
                        continue
                    yield (jsonl_path, line_no, parsed)
        except OSError as exc:
            logger.error(
                "golden_sync.file_read_error",
                path=str(jsonl_path),
                error=str(exc),
            )


# ── Public API ───────────────────────────────────────────────


def sync_golden_entries(
    db: Session,
    root: Optional[Path] = None,
) -> Dict[str, int]:
    """Upsert all golden JSONL entries into the DB.

    Idempotent: safe to run on every startup.  Uses Postgres
    ``INSERT ... ON CONFLICT DO UPDATE`` so existing rows pick
    up content changes without losing their ``id`` (and thus
    their attached reviews).

    Soft-delete is NOT performed here — entries removed from
    JSONL files remain in the DB until manually purged.  Keeps
    cascade-deletes off the hot startup path.

    Args:
        db: Database session.  Caller is responsible for
            committing; this function calls ``db.commit()``
            once at the end.
        root: Optional override for the JSONL root directory.
            Defaults to the package-relative
            ``tests/harness/evals/golden/v2/``.

    Returns:
        ``{"upserted": int, "skipped": int}``: counts for
        log-line summarisation.  Caller logs at INFO level.
    """
    effective_root = root or _GOLDEN_V2_DIR

    upserted = 0
    skipped = 0

    for jsonl_path, line_no, raw in _iter_jsonl_files(
        effective_root,
    ):
        # Source path is logged relative to the repo root if we
        # can manage it, otherwise as-is.
        try:
            rel = str(
                jsonl_path.relative_to(
                    Path(__file__).resolve().parents[3],
                ),
            )
        except ValueError:
            rel = str(jsonl_path)

        fields = _extract_entry_fields(raw, rel, line_no)
        if fields is None:
            skipped += 1
            continue

        # Postgres-flavoured upsert.  ``id`` is the conflict
        # target; everything else updates.
        stmt = pg_insert(GoldenEntry).values(**fields)
        update_cols = {
            k: stmt.excluded[k]
            for k in fields
            if k != "id"
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["id"],
            set_=update_cols,
        )
        db.execute(stmt)
        upserted += 1

    db.commit()

    logger.info(
        "golden_sync.complete",
        root=str(effective_root),
        upserted=upserted,
        skipped=skipped,
    )
    return {"upserted": upserted, "skipped": skipped}
