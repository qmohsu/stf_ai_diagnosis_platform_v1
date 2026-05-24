"""Unit tests for ``app.services.golden_sync`` (HARNESS-20 + fix).

Focused on the two-pass sync introduced by migration
``b1c2d3e4f5a6`` to correct the prior tier-overwrite bug.
The full ``sync_golden_entries`` end-to-end path talks to
Postgres, so the suite exercises the pure walk + extraction
helpers directly and uses a minimal fake session for the
overlay-update path.

Coverage:

- Candidate-only walk: top-level ``v2/*.jsonl`` files only,
  candidates/ subdir excluded, locked/ subdir excluded.
- Locked-only walk: ``v2/locked/*.jsonl`` only.
- Missing locked/ directory is a silent no-op (not an error).
- ``_extract_entry_fields`` returns content fields with NO
  ``is_locked`` key (the overlay pass is the only writer of
  that flag).
- ``_apply_locked_overlay`` flips ``is_locked`` on matching
  ids and warns on orphan locked-only ids.

Author: Li-Ta Hsu
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from app.services.golden_sync import (
    _apply_locked_overlay,
    _derive_obd_manual_id,
    _extract_entry_fields,
    _is_obd_question_type,
    _iter_candidate_jsonl_files,
    _iter_locked_jsonl_files,
)


def _candidate_entry(entry_id: str) -> Dict[str, object]:
    """Minimal valid entry dict for the extractor."""
    return {
        "id": entry_id,
        "category": "dtc",
        "question_type": "lookup",
        "difficulty": "easy",
        "question": "what does it mean?",
        "golden_summary": "a summary",
        "golden_citations": [
            {
                "manual_id": "MWS150A",
                "slug": "s",
                "quote": "q",
            },
        ],
        "must_contain": [],
    }


def _write_jsonl(path: Path, entries: List[Dict[str, object]]) -> None:
    """Write entries as one-per-line JSONL."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


# ── _extract_entry_fields ────────────────────────────────────


def test_extract_entry_fields_omits_is_locked() -> None:
    """The extractor must NOT touch ``is_locked``.

    The overlay pass is the sole writer of that flag; if the
    extractor set it (even to False), the upsert in pass 1
    would clobber whatever pass 2 set on a previous run when
    re-running against a static corpus.
    """
    fields = _extract_entry_fields(
        _candidate_entry("e1"),
        source_path="v2/mws150a.jsonl",
        line_number=1,
    )
    assert fields is not None
    assert "is_locked" not in fields
    # Sanity: the content fields we DO need are present.
    assert fields["id"] == "e1"
    assert fields["question_en"] == "what does it mean?"


def test_extract_entry_fields_skips_no_id() -> None:
    """Missing id → skip (with a warning)."""
    bad = _candidate_entry("anything")
    bad.pop("id")
    fields = _extract_entry_fields(
        bad, source_path="x.jsonl", line_number=1,
    )
    assert fields is None


def test_extract_entry_fields_skips_missing_required_field() -> None:
    """Missing required content field → skip."""
    bad = _candidate_entry("e1")
    bad.pop("category")
    fields = _extract_entry_fields(
        bad, source_path="x.jsonl", line_number=1,
    )
    assert fields is None


# ── Lane detection + OBD extraction (HARNESS-21 [2b/4]) ──────


def _obd_entry(entry_id: str) -> Dict[str, object]:
    """Minimal valid OBD-lane entry dict for the extractor.

    Deliberately omits ``golden_citations`` to match the real
    PR [2a/4] JSONL shape — OBD entries don't carry manual-side
    slug citations, and ``golden_sync`` defaults the field to
    ``[]`` for OBD lanes (it remains required for manual lanes).
    """
    return {
        "id": entry_id,
        "category": "component",
        "question_type": "signal_statistics",
        "difficulty": "easy",
        "question": "Peak RPM?",
        "golden_summary": "Peak RPM was 3906.",
        "must_contain": ["RPM"],
        "expected_signal_citations": [
            {
                "signal": "A_KL_RPM",
                "stat": "max",
                "value": 3906.0,
                "value_tolerance_rel": 0.01,
            },
        ],
        "expected_dtcs": [],
        "expected_no_evidence": False,
    }


def test_is_obd_question_type_recognises_six_obd_values() -> None:
    """The frozenset matches the six OBD literals."""
    for qt in (
        "signal_statistics",
        "event_finding",
        "dtc_enumeration",
        "dtc_decode",
        "compound_obd",
        "adversarial_obd",
    ):
        assert _is_obd_question_type(qt), qt


def test_is_obd_question_type_rejects_manual_values() -> None:
    """Manual question_types must not match."""
    for qt in (
        "lookup",
        "procedural",
        "cross-section",
        "image-required",
        "adversarial",
    ):
        assert not _is_obd_question_type(qt), qt


def test_derive_obd_manual_id_from_path_stem() -> None:
    """Helper returns the filename stem."""
    assert _derive_obd_manual_id(
        "diagnostic_api/tests/harness/evals/golden/v2/"
        "yamaha_road_test.jsonl"
    ) == "yamaha_road_test"
    assert _derive_obd_manual_id(
        "yamaha_road_test.jsonl"
    ) == "yamaha_road_test"
    assert _derive_obd_manual_id(
        "/abs/path/yamaha_road_test.jsonl"
    ) == "yamaha_road_test"


def test_extract_entry_fields_obd_lane_basic() -> None:
    """OBD entry populates lane + OBD fields; manual fields safe."""
    raw = _obd_entry("yamaha-stats-001")
    fields = _extract_entry_fields(
        raw,
        source_path="tests/harness/evals/golden/v2/yamaha_road_test.jsonl",
        line_number=1,
    )
    assert fields is not None
    assert fields["lane"] == "obd"
    # Synthetic manual_id from the source filename stem.
    assert fields["manual_id"] == "yamaha_road_test"
    # OBD-specific fields populated.
    assert fields["expected_signal_citations"] == [
        {
            "signal": "A_KL_RPM",
            "stat": "max",
            "value": 3906.0,
            "value_tolerance_rel": 0.01,
        },
    ]
    assert fields["expected_dtcs"] == []
    assert fields["expected_no_evidence"] is False
    # Manual-specific fields stay at their empty defaults.
    assert fields["golden_citations"] == []
    assert fields["expected_recall_slugs"] == []


def test_extract_entry_fields_obd_lane_adversarial() -> None:
    """expected_no_evidence=True is preserved through extraction."""
    raw = _obd_entry("yamaha-adv-001")
    raw["question_type"] = "adversarial_obd"
    raw["expected_signal_citations"] = []
    raw["expected_no_evidence"] = True
    fields = _extract_entry_fields(
        raw,
        source_path="yamaha_road_test.jsonl",
        line_number=2,
    )
    assert fields is not None
    assert fields["lane"] == "obd"
    assert fields["expected_no_evidence"] is True
    assert fields["expected_signal_citations"] == []


def test_extract_entry_fields_manual_lane_unchanged() -> None:
    """Manual entries get lane='manual' + empty OBD field
    defaults; pre-existing behavior is preserved."""
    raw = _candidate_entry("mws150a-dtc-001")
    fields = _extract_entry_fields(
        raw,
        source_path="golden/v2/mws150a.jsonl",
        line_number=1,
    )
    assert fields is not None
    assert fields["lane"] == "manual"
    # OBD fields default to empty/false even though the raw
    # input didn't supply them.
    assert fields["expected_signal_citations"] == []
    assert fields["expected_dtcs"] == []
    assert fields["expected_no_evidence"] is False
    # Manual extraction unchanged.
    assert fields["manual_id"] == "MWS150A"
    assert fields["golden_citations"] == raw["golden_citations"]


def test_extract_entry_fields_obd_omits_golden_citations_ok() -> None:
    """OBD entry without ``golden_citations`` should NOT be skipped.

    Regression test for the deploy hotfix: the production OBD
    JSONL doesn't carry ``golden_citations`` (the field is
    manual-lane-specific), but the original required-fields
    check insisted on it being present and skipped all 15
    entries on the first deploy of [2b/4].  golden_sync now
    treats ``golden_citations`` as optional for OBD entries and
    defaults to ``[]`` in the persisted row.
    """
    raw = _obd_entry("yamaha-noref-001")
    assert "golden_citations" not in raw  # sanity
    fields = _extract_entry_fields(
        raw, source_path="yamaha_road_test.jsonl", line_number=1,
    )
    assert fields is not None
    assert fields["lane"] == "obd"
    assert fields["golden_citations"] == []


def test_extract_entry_fields_manual_still_requires_golden_citations() -> None:
    """Manual entries without ``golden_citations`` are still
    skipped — the schema contract is unchanged for manual lane.
    """
    raw = _candidate_entry("manual-noref-001")
    raw.pop("golden_citations")
    fields = _extract_entry_fields(
        raw, source_path="golden/v2/mws150a.jsonl", line_number=1,
    )
    assert fields is None


def test_extract_entry_fields_obd_lane_dtc_decode() -> None:
    """dtc_decode entry pulls expected_dtcs through."""
    raw = _obd_entry("yamaha-decode-001")
    raw["question_type"] = "dtc_decode"
    raw["expected_signal_citations"] = []
    raw["expected_dtcs"] = [
        {"code": "87F11043000000000000CB", "status": "stored"},
    ]
    fields = _extract_entry_fields(
        raw,
        source_path="yamaha_road_test.jsonl",
        line_number=3,
    )
    assert fields is not None
    assert fields["lane"] == "obd"
    assert fields["expected_dtcs"] == [
        {"code": "87F11043000000000000CB", "status": "stored"},
    ]


# ── _iter_candidate_jsonl_files ──────────────────────────────


def test_candidate_walk_picks_up_top_level_only(
    tmp_path: Path,
) -> None:
    """Top-level ``v2/*.jsonl`` files yield; subdirectories don't."""
    _write_jsonl(tmp_path / "mws150a.jsonl",
                 [_candidate_entry("cand-1")])
    _write_jsonl(tmp_path / "locked" / "mws150a.jsonl",
                 [_candidate_entry("lock-1")])

    rows = list(_iter_candidate_jsonl_files(tmp_path))
    ids = {raw["id"] for (_p, _ln, raw) in rows}
    assert ids == {"cand-1"}


def test_candidate_walk_excludes_candidates_subdir(
    tmp_path: Path,
) -> None:
    """A ``candidates/`` subdir (raw author drafts pre-review)
    stays excluded.  Same invariant as before the refactor."""
    _write_jsonl(tmp_path / "mws150a.jsonl",
                 [_candidate_entry("real-1")])
    _write_jsonl(
        tmp_path / "candidates" / "draft.jsonl",
        [_candidate_entry("draft-1")],
    )

    rows = list(_iter_candidate_jsonl_files(tmp_path))
    ids = {raw["id"] for (_p, _ln, raw) in rows}
    # Note: ``candidates/draft.jsonl`` would only show up if we
    # recursed.  We don't, so the only id is the real one.
    assert ids == {"real-1"}


def test_candidate_walk_handles_missing_root(
    tmp_path: Path,
) -> None:
    """A non-existent root path yields nothing without raising."""
    rows = list(
        _iter_candidate_jsonl_files(tmp_path / "does-not-exist"),
    )
    assert rows == []


# ── _iter_locked_jsonl_files ─────────────────────────────────


def test_locked_walk_picks_up_locked_subdir(
    tmp_path: Path,
) -> None:
    """``v2/locked/*.jsonl`` files yield; top-level files don't."""
    _write_jsonl(tmp_path / "mws150a.jsonl",
                 [_candidate_entry("cand-1")])
    _write_jsonl(tmp_path / "locked" / "mws150a.jsonl",
                 [_candidate_entry("lock-1")])

    rows = list(_iter_locked_jsonl_files(tmp_path))
    ids = {raw["id"] for (_p, _ln, raw) in rows}
    assert ids == {"lock-1"}


def test_locked_walk_missing_dir_is_silent(
    tmp_path: Path,
) -> None:
    """No ``locked/`` directory at all → empty iterator, no warning.

    A fresh repo or a pre-first-promotion state is normal; we
    don't want to fill the logs with warnings about it.
    """
    _write_jsonl(tmp_path / "mws150a.jsonl",
                 [_candidate_entry("cand-1")])
    # No locked/ created.

    rows = list(_iter_locked_jsonl_files(tmp_path))
    assert rows == []


def test_locked_walk_handles_empty_locked_file(
    tmp_path: Path,
) -> None:
    """An empty locked file (no entries promoted yet) yields
    nothing without crashing — same shape as the directory-
    missing case."""
    (tmp_path / "locked").mkdir(parents=True)
    (tmp_path / "locked" / "mws150a.jsonl").write_text(
        "", encoding="utf-8",
    )

    rows = list(_iter_locked_jsonl_files(tmp_path))
    assert rows == []


# ── _apply_locked_overlay ────────────────────────────────────


class _FakeExecResult:
    """Stand-in for SQLAlchemy ``CursorResult`` — only needs
    ``rowcount`` for our use case."""

    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _FakeDB:
    """Records executed statements and returns canned rowcounts.

    Enough surface for ``_apply_locked_overlay`` (it only calls
    ``db.execute(stmt)`` once per overlay).
    """

    def __init__(self, rowcount_to_return: int = 0) -> None:
        self.executed = []
        self._rowcount = rowcount_to_return

    def execute(self, stmt):
        self.executed.append(stmt)
        return _FakeExecResult(self._rowcount)


def test_locked_overlay_flips_flag_for_matching_ids(
    tmp_path: Path,
) -> None:
    """The overlay UPDATE fires once with the locked-id list and
    the returned ``rowcount`` becomes ``locked_flagged``."""
    _write_jsonl(tmp_path / "mws150a.jsonl",
                 [_candidate_entry("e1"),
                  _candidate_entry("e2")])
    _write_jsonl(tmp_path / "locked" / "mws150a.jsonl",
                 [_candidate_entry("e1")])

    # Pretend Postgres reports 1 row updated.
    db = _FakeDB(rowcount_to_return=1)
    flagged, orphans = _apply_locked_overlay(
        db, tmp_path, candidate_ids=["e1", "e2"],
    )
    assert flagged == 1
    assert orphans == 0
    assert len(db.executed) == 1


def test_locked_overlay_warns_on_orphan_locked_id(
    tmp_path: Path,
) -> None:
    """A locked entry whose id has no candidate row increments
    ``locked_orphans`` and logs a warning.

    The UPDATE still fires (with the orphan id in the IN-list);
    Postgres simply matches zero rows for that id.  The
    in-memory fake rowcount reflects only the non-orphan
    matches.
    """
    _write_jsonl(tmp_path / "mws150a.jsonl",
                 [_candidate_entry("e1")])
    _write_jsonl(tmp_path / "locked" / "mws150a.jsonl",
                 [_candidate_entry("e1"),
                  _candidate_entry("ghost-1")])

    db = _FakeDB(rowcount_to_return=1)
    flagged, orphans = _apply_locked_overlay(
        db, tmp_path, candidate_ids=["e1"],
    )
    assert flagged == 1
    assert orphans == 1


def test_locked_overlay_no_locked_tier_is_noop(
    tmp_path: Path,
) -> None:
    """No locked file → no UPDATE issued, counts are zero."""
    _write_jsonl(tmp_path / "mws150a.jsonl",
                 [_candidate_entry("e1")])

    db = _FakeDB()
    flagged, orphans = _apply_locked_overlay(
        db, tmp_path, candidate_ids=["e1"],
    )
    assert flagged == 0
    assert orphans == 0
    assert db.executed == []
