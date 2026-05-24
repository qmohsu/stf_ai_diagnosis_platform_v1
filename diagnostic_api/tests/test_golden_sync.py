"""Unit tests for ``app.services.golden_sync`` (HARNESS-20).

Focused on the new tier-detection path introduced for the
two-tier corpus.  The sync routine itself talks to Postgres, so
the tests exercise the pure helpers (``_iter_jsonl_files``,
``_extract_entry_fields``, ``_tier_for_path``) rather than the
DB.  Coverage:

- Files at the v2 root resolve to ``tier='candidate'``.
- Files under ``v2/locked/`` resolve to ``tier='locked'``.
- The recursive walk picks up both tiers in one pass.
- Files under any ``candidates/`` subdir are excluded.
- ``_extract_entry_fields`` propagates the ``tier`` kwarg into
  the row payload.

Author: Li-Ta Hsu
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from app.services.golden_sync import (
    _extract_entry_fields,
    _iter_jsonl_files,
    _tier_for_path,
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


# ── _tier_for_path ───────────────────────────────────────────


def test_tier_for_path_root_is_candidate(tmp_path: Path) -> None:
    """Anything not under a ``locked/`` segment is candidate."""
    p = tmp_path / "v2" / "mws150a.jsonl"
    assert _tier_for_path(p) == "candidate"


def test_tier_for_path_under_locked_is_locked(
    tmp_path: Path,
) -> None:
    """``locked`` segment anywhere in the parts list triggers
    the locked tier classification."""
    p = tmp_path / "v2" / "locked" / "mws150a.jsonl"
    assert _tier_for_path(p) == "locked"


# ── _extract_entry_fields ────────────────────────────────────


def test_extract_entry_fields_defaults_to_candidate() -> None:
    """Without an explicit ``tier`` kwarg, the row defaults to
    candidate — keeps older callers safe."""
    fields = _extract_entry_fields(
        _candidate_entry("e1"),
        source_path="v2/mws150a.jsonl",
        line_number=1,
    )
    assert fields is not None
    assert fields["tier"] == "candidate"


def test_extract_entry_fields_honours_locked_tier() -> None:
    """When ``tier='locked'`` is passed, the row carries it."""
    fields = _extract_entry_fields(
        _candidate_entry("e1"),
        source_path="v2/locked/mws150a.jsonl",
        line_number=1,
        tier="locked",
    )
    assert fields is not None
    assert fields["tier"] == "locked"


# ── _iter_jsonl_files ────────────────────────────────────────


def test_iter_jsonl_files_yields_both_tiers(
    tmp_path: Path,
) -> None:
    """A v2 dir containing both a candidate file and a locked
    file yields both, with the correct tier label per row."""
    candidate = tmp_path / "mws150a.jsonl"
    locked = tmp_path / "locked" / "mws150a.jsonl"
    _write_jsonl(candidate, [_candidate_entry("cand-1")])
    _write_jsonl(locked, [_candidate_entry("lock-1")])

    rows = list(_iter_jsonl_files(tmp_path))
    tier_by_id = {
        raw["id"]: tier for (_p, _ln, raw, tier) in rows
    }
    assert tier_by_id == {
        "cand-1": "candidate",
        "lock-1": "locked",
    }


def test_iter_jsonl_files_skips_candidates_subdir(
    tmp_path: Path,
) -> None:
    """A ``candidates/`` subdirectory (used for raw author drafts
    before they're folded into the main candidate set) stays
    excluded from the sync."""
    real = tmp_path / "mws150a.jsonl"
    draft = tmp_path / "candidates" / "draft.jsonl"
    _write_jsonl(real, [_candidate_entry("real-1")])
    _write_jsonl(draft, [_candidate_entry("draft-1")])

    rows = list(_iter_jsonl_files(tmp_path))
    ids = {raw["id"] for (_p, _ln, raw, _tier) in rows}
    assert ids == {"real-1"}


def test_iter_jsonl_files_handles_empty_locked_file(
    tmp_path: Path,
) -> None:
    """An empty ``locked/mws150a.jsonl`` (the shipped initial
    state) walks cleanly: no rows yielded for it, candidate
    rows still picked up."""
    candidate = tmp_path / "mws150a.jsonl"
    locked = tmp_path / "locked" / "mws150a.jsonl"
    _write_jsonl(candidate, [_candidate_entry("cand-1")])
    locked.parent.mkdir(parents=True, exist_ok=True)
    locked.write_text("", encoding="utf-8")

    rows = list(_iter_jsonl_files(tmp_path))
    ids = {raw["id"] for (_p, _ln, raw, _tier) in rows}
    assert ids == {"cand-1"}
