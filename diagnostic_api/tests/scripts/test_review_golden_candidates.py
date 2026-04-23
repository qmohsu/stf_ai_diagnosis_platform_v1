"""Unit tests for the golden-candidate reviewer script.

Exercises the interactive review loop by injecting scripted
``reader`` / ``editor_runner`` callables.  Covers: accept, reject,
skip, quit, edit-success, edit-failure (malformed JSON), schema
re-validation, state persistence across invocations, and
``_infer_target`` path computation.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from scripts.review_golden_candidates import (
    _format_entry_for_display,
    _infer_target,
    _load_candidates,
    _load_state,
    _save_state,
    _validate_entry,
    review_candidates,
)


# ── Fixture data ──────────────────────────────────────────────────


def _valid_entry(idx: int = 1) -> Dict[str, Any]:
    """Minimum-valid GoldenEntry-shaped dict."""
    return {
        "id": f"mws150a-dtc-{idx:03d}",
        "category": "dtc",
        "difficulty": "easy",
        "question": f"What is DTC P017{idx}?",
        "obd_context": None,
        "golden_summary": (
            "Test summary of how to diagnose this code."
        ),
        "golden_citations": [{
            "manual_id": "MWS150A_Service_Manual",
            "slug": "3-2-fuel-system-troubleshooting",
            "quote": "placeholder quote",
        }],
        "expected_tool_trace": [
            "get_manual_toc", "read_manual_section",
        ],
        "must_contain": [f"P017{idx}"],
        "must_not_contain": [],
        "requires_image": False,
        "notes": "test entry",
    }


@pytest.fixture()
def candidates_file(tmp_path: Path) -> Path:
    """Write 3 valid candidate entries to a temp JSONL file."""
    path = tmp_path / "candidates.jsonl"
    with open(path, "w", encoding="utf-8") as handle:
        for i in (1, 2, 3):
            handle.write(
                json.dumps(_valid_entry(i)) + "\n",
            )
    return path


@pytest.fixture()
def target_file(tmp_path: Path) -> Path:
    """Empty target JSONL to append to."""
    return tmp_path / "golden.jsonl"


# ── Scripted helpers ──────────────────────────────────────────────


class _ScriptedReader:
    """Pops one queued input per call, raises when exhausted."""

    def __init__(self, inputs: List[str]) -> None:
        self._queue = list(inputs)

    def __call__(self, _prompt: str) -> str:
        if not self._queue:
            raise RuntimeError(
                "ScriptedReader exhausted — too few inputs",
            )
        return self._queue.pop(0)


class _FakeEditorRunner:
    """Returns pre-queued edited entries (or None) per call."""

    def __init__(
        self, returns: List[Optional[Dict[str, Any]]],
    ) -> None:
        self._queue = list(returns)
        self.call_count = 0

    def __call__(
        self, _entry: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        self.call_count += 1
        if not self._queue:
            return None
        return self._queue.pop(0)


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """Helper: read a JSONL file into a list of dicts."""
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as handle:
        return [
            json.loads(line)
            for line in handle
            if line.strip()
        ]


# ── _validate_entry ───────────────────────────────────────────────


class TestValidateEntry:
    def test_valid_entry_passes(self) -> None:
        ok, err = _validate_entry(_valid_entry())
        assert ok is True
        assert err is None

    def test_missing_category_fails(self) -> None:
        entry = _valid_entry()
        del entry["category"]
        ok, err = _validate_entry(entry)
        assert ok is False
        assert err is not None

    def test_invalid_category_fails(self) -> None:
        entry = _valid_entry()
        entry["category"] = "not_valid"
        ok, err = _validate_entry(entry)
        assert ok is False
        assert err is not None


# ── _format_entry_for_display ─────────────────────────────────────


class TestFormatEntryForDisplay:
    def test_includes_core_fields(self) -> None:
        rendered = _format_entry_for_display(_valid_entry())
        assert "mws150a-dtc-001" in rendered
        assert "category:" in rendered
        assert "What is DTC P0171?" in rendered
        assert "placeholder quote" in rendered

    def test_adversarial_entry_shows_empty_citations(
        self,
    ) -> None:
        entry = _valid_entry()
        entry["golden_citations"] = []
        rendered = _format_entry_for_display(entry)
        assert "(none" in rendered  # "(none — adversarial)"


# ── _infer_target ─────────────────────────────────────────────────


class TestInferTarget:
    def test_strips_candidates_suffix(
        self, tmp_path: Path,
    ) -> None:
        cand = (
            tmp_path / "golden" / "candidates"
            / "mws150a-dtc.jsonl"
        )
        target = _infer_target(cand)
        assert target == (
            tmp_path / "golden" / "v1" / "mws150a.jsonl"
        )


# ── State persistence ────────────────────────────────────────────


class TestStatePersistence:
    def test_load_empty_when_no_file(
        self, tmp_path: Path,
    ) -> None:
        cand = tmp_path / "c.jsonl"
        cand.write_text("", encoding="utf-8")
        state = _load_state(cand)
        assert state == {
            "decisions": {}, "accepted_ids": [],
        }

    def test_save_then_load_roundtrip(
        self, tmp_path: Path,
    ) -> None:
        cand = tmp_path / "c.jsonl"
        cand.write_text("", encoding="utf-8")
        _save_state(cand, {
            "decisions": {"id-1": "accept"},
            "accepted_ids": ["id-1"],
        })
        state = _load_state(cand)
        assert state["decisions"]["id-1"] == "accept"
        assert state["accepted_ids"] == ["id-1"]

    def test_corrupt_state_falls_back_to_empty(
        self, tmp_path: Path,
    ) -> None:
        cand = tmp_path / "c.jsonl"
        cand.write_text("", encoding="utf-8")
        sp = cand.with_suffix(cand.suffix + ".review-state.json")
        sp.write_text("{not valid json", encoding="utf-8")
        state = _load_state(cand)
        assert state == {
            "decisions": {}, "accepted_ids": [],
        }


# ── End-to-end review loop ────────────────────────────────────────


class TestReviewCandidates:
    def test_accept_all_writes_all_entries(
        self, candidates_file: Path, target_file: Path,
    ) -> None:
        """Three accepts -> three entries in the target file."""
        writer = io.StringIO()
        totals = review_candidates(
            candidates_file, target_file,
            reader=_ScriptedReader(["a", "a", "a"]),
            writer=writer,
        )
        assert totals["accept"] == 3
        assert totals["reject"] == 0
        rows = _read_jsonl(target_file)
        assert len(rows) == 3

    def test_reject_does_not_append(
        self, candidates_file: Path, target_file: Path,
    ) -> None:
        writer = io.StringIO()
        totals = review_candidates(
            candidates_file, target_file,
            reader=_ScriptedReader(["r", "r", "r"]),
            writer=writer,
        )
        assert totals["reject"] == 3
        assert totals["accept"] == 0
        assert not target_file.exists() or (
            len(_read_jsonl(target_file)) == 0
        )

    def test_mixed_decisions(
        self, candidates_file: Path, target_file: Path,
    ) -> None:
        writer = io.StringIO()
        totals = review_candidates(
            candidates_file, target_file,
            reader=_ScriptedReader(["a", "r", "s"]),
            writer=writer,
        )
        assert totals == {
            "accept": 1, "reject": 1, "skip": 1, "quit": 0,
        }
        assert len(_read_jsonl(target_file)) == 1

    def test_quit_stops_early(
        self, candidates_file: Path, target_file: Path,
    ) -> None:
        writer = io.StringIO()
        totals = review_candidates(
            candidates_file, target_file,
            reader=_ScriptedReader(["a", "q"]),
            writer=writer,
        )
        assert totals["accept"] == 1
        assert totals["quit"] == 1
        # Only the first entry should have been processed.
        assert len(_read_jsonl(target_file)) == 1

    def test_unknown_input_reprompts(
        self, candidates_file: Path, target_file: Path,
    ) -> None:
        writer = io.StringIO()
        totals = review_candidates(
            candidates_file, target_file,
            # "x" is invalid, then "a" for all 3.
            reader=_ScriptedReader(["x", "a", "a", "a"]),
            writer=writer,
        )
        assert totals["accept"] == 3

    def test_edit_with_valid_replacement_accepts(
        self, candidates_file: Path, target_file: Path,
    ) -> None:
        """Edit returns valid entry -> accepted."""
        # Build a different-but-valid entry.
        replacement = _valid_entry(99)
        writer = io.StringIO()
        editor = _FakeEditorRunner([
            replacement, None, None,
        ])
        totals = review_candidates(
            candidates_file, target_file,
            reader=_ScriptedReader(["e", "r", "r"]),
            writer=writer,
            editor_runner=editor,
        )
        assert totals["accept"] == 1
        assert editor.call_count == 1
        rows = _read_jsonl(target_file)
        assert rows[0]["id"] == "mws150a-dtc-099"

    def test_edit_returning_none_reprompts(
        self, candidates_file: Path, target_file: Path,
    ) -> None:
        """Editor aborted -> reviewer re-prompts, user rejects."""
        writer = io.StringIO()
        editor = _FakeEditorRunner([None])
        totals = review_candidates(
            candidates_file, target_file,
            reader=_ScriptedReader([
                "e", "r",  # first entry: edit aborts, reject.
                "r", "r",  # reject the rest.
            ]),
            writer=writer,
            editor_runner=editor,
        )
        assert totals["reject"] == 3
        assert totals["accept"] == 0

    def test_edit_with_invalid_schema_reprompts(
        self, candidates_file: Path, target_file: Path,
    ) -> None:
        """Edited entry fails schema -> reviewer re-prompts."""
        bad = _valid_entry()
        del bad["category"]  # schema violation
        writer = io.StringIO()
        editor = _FakeEditorRunner([bad])
        totals = review_candidates(
            candidates_file, target_file,
            reader=_ScriptedReader([
                "e", "r",  # first: invalid edit, then reject.
                "r", "r",
            ]),
            writer=writer,
            editor_runner=editor,
        )
        assert totals["accept"] == 0
        assert totals["reject"] == 3

    def test_state_is_saved_per_decision(
        self, candidates_file: Path, target_file: Path,
    ) -> None:
        """After quitting, the state file records prior decisions."""
        writer = io.StringIO()
        review_candidates(
            candidates_file, target_file,
            reader=_ScriptedReader(["a", "q"]),
            writer=writer,
        )
        state = _load_state(candidates_file)
        assert state["decisions"]["mws150a-dtc-001"] == "accept"
        assert "mws150a-dtc-001" in state["accepted_ids"]

    def test_rerun_skips_previously_decided(
        self, candidates_file: Path, target_file: Path,
    ) -> None:
        """Second invocation only revisits skipped entries."""
        writer1 = io.StringIO()
        review_candidates(
            candidates_file, target_file,
            reader=_ScriptedReader(["a", "r", "s"]),
            writer=writer1,
        )
        # Re-run: only the skipped entry (index 3) needs input.
        writer2 = io.StringIO()
        totals = review_candidates(
            candidates_file, target_file,
            reader=_ScriptedReader(["a"]),
            writer=writer2,
        )
        assert totals["accept"] == 1
        assert len(_read_jsonl(target_file)) == 2

    def test_cannot_accept_malformed_entry(
        self, tmp_path: Path,
    ) -> None:
        """A candidate missing required fields cannot be accepted."""
        bad = _valid_entry()
        del bad["category"]
        cand = tmp_path / "c.jsonl"
        cand.write_text(
            json.dumps(bad) + "\n", encoding="utf-8",
        )
        target = tmp_path / "g.jsonl"
        writer = io.StringIO()
        totals = review_candidates(
            cand, target,
            # "a" fails validation, then "r" to move on.
            reader=_ScriptedReader(["a", "r"]),
            writer=writer,
        )
        assert totals["accept"] == 0
        assert totals["reject"] == 1


# ── Input loader ──────────────────────────────────────────────────


class TestLoadCandidates:
    def test_loads_valid_jsonl(
        self, candidates_file: Path,
    ) -> None:
        entries = _load_candidates(candidates_file)
        assert len(entries) == 3

    def test_skips_malformed_lines(
        self, tmp_path: Path,
    ) -> None:
        path = tmp_path / "c.jsonl"
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(_valid_entry()) + "\n")
            handle.write("not valid json\n")
            handle.write(json.dumps(_valid_entry(2)) + "\n")
        entries = _load_candidates(path)
        assert len(entries) == 2

    def test_empty_file_returns_empty(
        self, tmp_path: Path,
    ) -> None:
        path = tmp_path / "c.jsonl"
        path.write_text("", encoding="utf-8")
        assert _load_candidates(path) == []
