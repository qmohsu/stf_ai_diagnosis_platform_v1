"""Sync golden Q&A entries from JSONL files into the DB.

The JSONL files under
``tests/harness/evals/golden/v2/*.jsonl`` are the canonical
source of truth for the eval suite.  This module mirrors them
into the ``golden_entries`` table on app startup so the
dashboard can serve / filter / aggregate without re-parsing the
files on every request, and so reviews have a stable foreign-key
target.

## Two-tier corpus (HARNESS-20)

The v2 corpus has two tiers on disk:

- **Candidate** — ``golden/v2/*.jsonl``.  Mutable.  The
  dashboard reflects this content.  Each row's content (text,
  citations, must_contain, etc.) lives in the DB.
- **Locked** — ``golden/v2/locked/*.jsonl``.  Append-only.
  The eval harness reads it directly from the filesystem.
  Locked-tier entries share their ``id`` with the candidate
  they were promoted from (the locked line is a verbatim copy).

The sync runs in **two passes** to handle this id sharing
correctly:

1. **Candidate pass** — walk ``v2/*.jsonl`` only (NOT
   recursing into ``locked/``).  Upsert each row with full
   content and ``is_locked=False``.
2. **Locked-overlay pass** — walk ``v2/locked/*.jsonl``.  For
   each id present, UPDATE the existing row's ``is_locked``
   flag to ``True``.  Content is NOT overwritten — the DB
   keeps the candidate's current content (which is what the
   dashboard should show), and the locked file remains the
   eval-canonical content on disk.

This ordering matters because an earlier (and buggy) version
of this module did a single recursive walk plus upsert.  The
two tiers' shared ids meant the second upsert overwrote the
first, leaving the ``tier`` column wrong in every row.  See
migration ``b1c2d3e4f5a6`` for the correction.

## Other behaviour

- Idempotent: re-running upserts based on entry ``id``.
- Tolerant of malformed lines: a parse error on one line is
  logged and that entry is skipped; the rest still sync.
- Bilingual fields are nullable in the DB; the JSONL fields
  ``question_zh`` / ``golden_summary_zh`` are optional and
  default to None when absent.
- ``candidates/`` subdirectories (raw author drafts pre-review)
  are excluded — those aren't part of the canonical corpus.

Author: Li-Ta Hsu
Date: May 2026
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import structlog
from sqlalchemy import update
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

# Subdirectory name housing the locked tier.  Used for both
# path-classification and discovery.  Centralised so the
# two-pass walk and any future tooling stay in lockstep.
_LOCKED_SUBDIR = "locked"


# ── Field extraction helpers ─────────────────────────────────


_OBD_QUESTION_TYPES = frozenset({
    "signal_statistics",
    "event_finding",
    "dtc_enumeration",
    "dtc_decode",
    "compound_obd",
    "adversarial_obd",
})
"""Question-type values that identify an OBD-lane entry.

Mirrors ``OBD_QUESTION_TYPES`` in ``tests/harness/evals/schemas.py``
(the eval suite's source-of-truth literal).  Duplicated here as a
frozenset rather than imported to keep the app→tests dependency
direction one-way.  If the eval suite ever widens the literal,
update both places.
"""


def _is_obd_question_type(question_type: str) -> bool:
    """Return True if ``question_type`` is an OBD-lane value."""
    return question_type in _OBD_QUESTION_TYPES


def _derive_obd_manual_id(source_path: str) -> str:
    """Synthesize a stable ``manual_id`` for an OBD-lane entry.

    OBD entries have no service-manual UUID — they reference an
    OBD log fixture instead.  We use the JSONL filename stem
    (without ``.jsonl``) as the synthetic ``manual_id`` so:

    - The NOT NULL constraint on ``golden_entries.manual_id`` is
      satisfied without a sentinel string.
    - All entries from the same fixture file share the same
      ``manual_id``, so the dashboard can group them naturally
      (mirrors how manual entries group by their service-manual
      UUID).
    - The value is deterministic across machines and runs.

    For ``golden/v2/yamaha_road_test.jsonl`` this returns
    ``"yamaha_road_test"``.

    Args:
        source_path: Source file path string (the value that
            will land in ``source_jsonl_path``).  Either
            relative or absolute; only the filename stem matters.

    Returns:
        Synthetic manual_id string.
    """
    return Path(source_path).stem


def _extract_entry_fields(
    raw: Dict[str, Any],
    source_path: str,
    line_number: int,
) -> Optional[Dict[str, Any]]:
    """Pull the fields we care about out of a raw JSONL line.

    Returns a dict suitable for INSERT/UPDATE on
    ``golden_entries``, or ``None`` if the line is malformed
    enough that we should skip it (and warn).

    The returned dict deliberately does NOT include
    ``is_locked``.  That flag is set by the locked-overlay
    pass (``_apply_locked_overlay``), not by the per-row
    content extraction — content extraction must stay
    side-effect-free across the two passes so the locked pass
    can't accidentally re-write candidate content.

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

    # Required fields — bail with a warning if missing.  Note
    # that ``golden_citations`` is required for **manual** entries
    # (the manual eval rubric grades against slug citations) but
    # is meaningfully empty for **OBD** entries (which carry
    # signal/DTC citations in their own fields).  We check the
    # question_type first to decide whether golden_citations is
    # required, defaulting OBD entries to ``[]`` so authors don't
    # need to spell out the empty list.
    question_type = raw.get("question_type")
    if not question_type:
        logger.warning(
            "golden_sync.skip_missing_field",
            entry_id=entry_id,
            missing="question_type",
        )
        return None

    is_obd = _is_obd_question_type(question_type)
    lane = "obd" if is_obd else "manual"

    required = (
        "category",
        "difficulty",
        "question",
        "golden_summary",
    )
    if not is_obd:
        # Manual lane: golden_citations is part of the schema
        # contract.  Adversarial-manual entries may have an
        # empty list, but the field must be present.
        required = required + ("golden_citations",)
    for field in required:
        if field not in raw:
            logger.warning(
                "golden_sync.skip_missing_field",
                entry_id=entry_id,
                missing=field,
            )
            return None

    # manual_id derivation differs by lane:
    # - Manual: nested inside ``golden_citations[0].manual_id``
    #   per the manual-eval JSONL schema; fall back to a sentinel
    #   for adversarial entries that have empty citations.
    # - OBD: derive a synthetic id from the source filename stem
    #   so all entries from the same fixture share the same
    #   manual_id (deterministic; satisfies NOT NULL).
    if is_obd:
        manual_id = _derive_obd_manual_id(source_path)
    else:
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
        "question_type": question_type,
        "difficulty": raw["difficulty"],
        "question_en": raw["question"],
        # Bilingual fields — nullable, None if absent.  OBD
        # entries are English-only at v1; ``question_zh`` /
        # ``golden_summary_zh`` stay None.
        "question_zh": raw.get("question_zh"),
        "obd_context": raw.get("obd_context"),
        "golden_summary_en": raw["golden_summary"],
        "golden_summary_zh": raw.get("golden_summary_zh"),
        # OBD entries don't ship a golden_citations field; default
        # to empty list to satisfy the NOT NULL JSONB column.
        "golden_citations": raw.get("golden_citations", []),
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
        # HARNESS-21 [2b/4]: lane + OBD-specific columns.
        # Manual entries get empty defaults; OBD entries pull
        # from the v1-style fields the eval-side authoring
        # convention (see golden/v1/yamaha_road_test.jsonl).
        "lane": lane,
        "expected_signal_citations": raw.get(
            "expected_signal_citations", [],
        ),
        "expected_dtcs": raw.get("expected_dtcs", []),
        "expected_no_evidence": bool(
            raw.get("expected_no_evidence", False),
        ),
    }


# ── Per-tier walkers ─────────────────────────────────────────


def _iter_candidate_jsonl_files(
    root: Path,
) -> Iterator[Tuple[Path, int, Dict[str, Any]]]:
    """Walk **candidate-tier** JSONL files only.

    Yields ``(path, line_number, parsed_dict)`` tuples for every
    parseable line in every ``*.jsonl`` file at ``root`` that
    is NOT under the ``locked/`` subdirectory and NOT under any
    ``candidates/`` subdirectory.

    Non-recursive on purpose: only top-level ``v2/*.jsonl``
    files count as canonical candidates.  Locked-tier files are
    handled separately by ``_iter_locked_jsonl_files``.
    """
    if not root.is_dir():
        logger.warning(
            "golden_sync.root_missing",
            root=str(root),
        )
        return

    for jsonl_path in sorted(root.glob("*.jsonl")):
        yield from _read_jsonl_lines(jsonl_path)


def _iter_locked_jsonl_files(
    root: Path,
) -> Iterator[Tuple[Path, int, Dict[str, Any]]]:
    """Walk **locked-tier** JSONL files only.

    Same shape as ``_iter_candidate_jsonl_files`` but scoped
    to ``root/locked/*.jsonl``.  Empty / missing ``locked/``
    is a normal state (no entries promoted yet) and yields
    nothing without warning.
    """
    locked_dir = root / _LOCKED_SUBDIR
    if not locked_dir.is_dir():
        # No locked tier yet — entirely normal state for a
        # fresh repo or before the first promotion.  Stay quiet.
        return

    for jsonl_path in sorted(locked_dir.glob("*.jsonl")):
        yield from _read_jsonl_lines(jsonl_path)


def _read_jsonl_lines(
    jsonl_path: Path,
) -> Iterator[Tuple[Path, int, Dict[str, Any]]]:
    """Yield ``(path, line_no, parsed_dict)`` for one JSONL file.

    Skips blank lines silently; logs and skips lines that fail
    to parse so one bad line never stops the whole sync.

    Shared helper between the two per-tier walkers so file IO
    + error-handling stay identical across tiers (and so the
    log signature is consistent).
    """
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
    """Upsert all golden JSONL entries into the DB (two-pass).

    Idempotent: safe to run on every startup.  Uses Postgres
    ``INSERT ... ON CONFLICT DO UPDATE`` so existing rows pick
    up content changes without losing their ``id`` (and thus
    their attached reviews).

    Pass 1 mirrors the candidate tier.  Pass 2 flips the
    ``is_locked`` flag on rows whose ``id`` also appears in
    the locked tier.  Content is never overwritten by the
    locked pass — the locked tier's content stays on the
    filesystem and is read directly by the eval harness.

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
        ``{"upserted": int, "skipped": int, "locked_flagged":
        int, "locked_orphans": int}``: counts for log-line
        summarisation.

        - ``upserted`` — candidate rows written
        - ``skipped`` — malformed candidate rows skipped
        - ``locked_flagged`` — rows whose ``is_locked`` flag
          flipped to True in pass 2
        - ``locked_orphans`` — locked entries with no matching
          candidate row.  Non-fatal but a data-integrity
          warning: a locked id should always exist as a
          candidate too (locks come FROM candidates).  Logged
          per-id.
    """
    effective_root = root or _GOLDEN_V2_DIR
    repo_root = Path(__file__).resolve().parents[3]

    # ── Pass 1: candidate tier ─────────────────────────────
    upserted = 0
    skipped = 0
    candidate_ids: List[str] = []

    for jsonl_path, line_no, raw in _iter_candidate_jsonl_files(
        effective_root,
    ):
        try:
            rel = str(jsonl_path.relative_to(repo_root))
        except ValueError:
            rel = str(jsonl_path)

        fields = _extract_entry_fields(raw, rel, line_no)
        if fields is None:
            skipped += 1
            continue

        # Candidate-pass writes content + is_locked=False.  A
        # later pass-2 update flips is_locked to True for any
        # id also present in the locked tier.  Listing
        # ``is_locked`` here without it in the upsert payload
        # would let it drift between candidate-only and
        # post-locked runs.
        fields_for_upsert = {**fields, "is_locked": False}

        stmt = pg_insert(GoldenEntry).values(**fields_for_upsert)
        update_cols = {
            k: stmt.excluded[k]
            for k in fields_for_upsert
            if k != "id"
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["id"],
            set_=update_cols,
        )
        db.execute(stmt)
        upserted += 1
        candidate_ids.append(fields["id"])

    # ── Pass 2: locked-overlay ──────────────────────────────
    locked_flagged, locked_orphans = _apply_locked_overlay(
        db, effective_root, candidate_ids,
    )

    db.commit()

    logger.info(
        "golden_sync.complete",
        root=str(effective_root),
        upserted=upserted,
        skipped=skipped,
        locked_flagged=locked_flagged,
        locked_orphans=locked_orphans,
    )
    return {
        "upserted": upserted,
        "skipped": skipped,
        "locked_flagged": locked_flagged,
        "locked_orphans": locked_orphans,
    }


def _apply_locked_overlay(
    db: Session,
    root: Path,
    candidate_ids: List[str],
) -> Tuple[int, int]:
    """Flip ``is_locked=True`` on rows whose id is in the locked
    tier; warn about locked-only orphans.

    Returns ``(flagged_count, orphan_count)``.  Does NOT commit
    — the caller batches that with the candidate-pass commit.
    """
    candidate_id_set = set(candidate_ids)
    flagged = 0
    orphans = 0

    locked_ids: List[str] = []
    for _path, _line, raw in _iter_locked_jsonl_files(root):
        entry_id = raw.get("id")
        if not entry_id:
            # Locked JSONL with a missing id is suspicious —
            # promote_golden refuses to write such lines, but
            # log it if it ever shows up.
            logger.warning("golden_sync.locked_skip_no_id")
            continue
        locked_ids.append(entry_id)

        if entry_id not in candidate_id_set:
            orphans += 1
            logger.warning(
                "golden_sync.locked_orphan",
                entry_id=entry_id,
                hint=(
                    "locked entry has no matching candidate row;"
                    " dashboard will not surface it"
                ),
            )

    if locked_ids:
        # Batch UPDATE: one query flips the flag for every
        # locked id that has a candidate row.  Orphan ids in
        # the IN-list simply update zero rows (harmless).
        stmt = (
            update(GoldenEntry)
            .where(GoldenEntry.id.in_(locked_ids))
            .values(is_locked=True)
        )
        result = db.execute(stmt)
        flagged = result.rowcount or 0

    return flagged, orphans
