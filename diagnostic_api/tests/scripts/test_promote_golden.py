"""Unit tests for ``scripts.promote_golden`` (HARNESS-20).

These tests exercise the pure file-and-validation paths of
``promote_entry``.  The DB is stubbed via a tiny fake session
so the suite stays hermetic — no Postgres connection required.

Coverage:

- happy path: write to locked, append PROMOTIONS row, hash
  computed
- refuse re-promote of an id already in the locked file
- refuse when latest graded review is below the 4★ accept gate
- refuse when no graded review exists
- ``--force`` bypasses the review gate and records the override
- ``--dry-run`` writes nothing
- candidate id missing from JSONL → refuse with clear message
- PROMOTIONS row formatting escapes pipes in free-text
- stable manual-identity gate (HARNESS-23 T11, #151): prose
  ``manual_id`` refused, ``--force`` does NOT bypass, entries
  without citations pass vacuously

Author: Li-Ta Hsu
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional

import pytest

from scripts.promote_golden import (
    PromotionResult,
    _canonical_dumps,
    _find_candidate_line,
    _format_promotions_row,
    _locked_ids,
    _sha256_hex,
    _stable_manual_id_problems,
    promote_entry,
)


# ── Fixtures ─────────────────────────────────────────────────


# Stable manual identity used across fixtures.  HARNESS-23 T11
# (#151): goldens cite the manuals.id UUID, never cover-code
# prose like "MWS150A" — the promote gate refuses prose ids.
_MANUAL_UUID = "3fa85f64-5717-4562-b3fc-2c963f66afa6"


def _make_candidate_line(
    entry_id: str,
    extra: str = "",
    manual_id: str = _MANUAL_UUID,
    citations: bool = True,
) -> str:
    """Build one valid candidate JSONL line for a given id."""
    payload = {
        "id": entry_id,
        "category": "dtc",
        "question_type": "lookup",
        "difficulty": "easy",
        "question": "What does P0117 mean?",
        "golden_summary": "P0117 is the ECT-low circuit code.",
        "golden_citations": [
            {
                "manual_id": manual_id,
                "slug": "dtc-p0117",
                "quote": "ECT circuit low input",
            },
        ] if citations else [],
        "must_contain": ["P0117"],
        "pitfall_directives": [],
        "notes": extra,
    }
    return json.dumps(payload, ensure_ascii=False)


@pytest.fixture
def candidate_file(tmp_path: Path) -> Path:
    """A candidate JSONL containing three entries."""
    f = tmp_path / "mws150a.jsonl"
    lines = [
        _make_candidate_line("entry-001"),
        _make_candidate_line("entry-002"),
        _make_candidate_line("entry-003"),
    ]
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f


@pytest.fixture
def locked_file(tmp_path: Path) -> Path:
    """An empty locked JSONL — the script will append into this."""
    f = tmp_path / "locked" / "mws150a.jsonl"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("", encoding="utf-8")
    return f


@pytest.fixture
def promotions_log(tmp_path: Path) -> Path:
    """An empty PROMOTIONS.md file — script appends rows."""
    f = tmp_path / "locked" / "PROMOTIONS.md"
    # Caller created the dir via locked_file fixture; recreate
    # defensively in case promotions_log is requested first.
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("(header omitted in tests)\n", encoding="utf-8")
    return f


def _fake_db_returning(review):
    """Build a fake DB session whose review-query path returns
    ``review`` (or ``None``)."""

    class _FakeQuery:
        def __init__(self, value):
            self._value = value

        def filter(self, *_args, **_kwargs):
            return self

        def order_by(self, *_args, **_kwargs):
            return self

        def first(self):
            return self._value

    class _FakeDB:
        def query(self, _model):
            return _FakeQuery(review)

    return _FakeDB()


def _review(
    status: str = "accept",
    stars: Optional[int] = 5,
    review_id: str = "rev-uuid-aaa",
):
    """Build a fake GoldenReview-like object."""
    return SimpleNamespace(
        id=review_id, status=status, star_rating=stars,
    )


# ── Pure helpers ─────────────────────────────────────────────


def test_canonical_dumps_is_key_order_stable() -> None:
    """Hashing input must not depend on dict key order."""
    a = {"z": 1, "a": 2}
    b = {"a": 2, "z": 1}
    assert _canonical_dumps(a) == _canonical_dumps(b)


def test_sha256_hex_is_deterministic() -> None:
    """Same input → same hash, every time."""
    assert _sha256_hex("hello") == _sha256_hex("hello")
    assert _sha256_hex("hello") != _sha256_hex("world")


def test_find_candidate_line_returns_raw_text(
    candidate_file: Path,
) -> None:
    """The raw line bytes must come back so the locked file
    receives a verbatim copy of the candidate."""
    raw, parsed = _find_candidate_line(
        candidate_file, "entry-002",
    )
    assert parsed["id"] == "entry-002"
    # Round-trip the same text we'd append:
    assert json.loads(raw)["id"] == "entry-002"


def test_find_candidate_line_missing_raises(
    candidate_file: Path,
) -> None:
    """An unknown id surfaces as KeyError."""
    with pytest.raises(KeyError, match="entry-999"):
        _find_candidate_line(candidate_file, "entry-999")


def test_locked_ids_handles_empty_file(
    locked_file: Path,
) -> None:
    """An empty locked file has no ids — must not crash."""
    assert _locked_ids(locked_file) == []


def test_locked_ids_returns_committed_ids(
    locked_file: Path,
) -> None:
    """Once an entry is in the locked file it shows up."""
    locked_file.write_text(
        _make_candidate_line("entry-xx") + "\n",
        encoding="utf-8",
    )
    assert _locked_ids(locked_file) == ["entry-xx"]


def test_format_promotions_row_escapes_pipes() -> None:
    """Pipes in free-text break the Markdown table; escape them."""
    row = _format_promotions_row(
        promoted_at="2026-05-24T12:00:00+00:00",
        entry_id="entry-001",
        content_hash="deadbeef" * 8,
        reviewer="tal|on",
        expert_review_id="rev-1",
        reason="needed | this | now",
    )
    # Free-text columns should have escaped pipes; the hash
    # column wraps in backticks so internal `|` would also need
    # escaping — but hashes are hex, so this just sanity-checks
    # the free-text path.
    assert "tal\\|on" in row
    assert "needed \\| this \\| now" in row
    assert row.endswith("|\n")


# ── promote_entry: happy path ────────────────────────────────


def test_promote_entry_happy_path_writes_locked_and_log(
    candidate_file: Path,
    locked_file: Path,
    promotions_log: Path,
) -> None:
    """Successful promotion appends to both files and returns
    promoted=True with a populated hash."""
    db = _fake_db_returning(_review())
    fixed_now = datetime(2026, 5, 24, 12, 0, 0, tzinfo=timezone.utc)

    result = promote_entry(
        db=db,
        entry_id="entry-001",
        reviewer="talon",
        reason="expert >=4* on 2026-05-23",
        candidate_file=candidate_file,
        locked_file=locked_file,
        promotions_log=promotions_log,
        now=fixed_now,
    )

    assert result.promoted is True
    assert result.entry_id == "entry-001"
    assert len(result.content_hash) == 64  # sha256 hex
    assert result.expert_review_id == "rev-uuid-aaa"

    locked_text = locked_file.read_text(encoding="utf-8")
    assert '"id":"entry-001"' in locked_text.replace(' ', '')
    assert locked_text.endswith("\n")

    log_text = promotions_log.read_text(encoding="utf-8")
    assert "entry-001" in log_text
    assert result.content_hash in log_text
    assert "talon" in log_text
    assert "expert >=4* on 2026-05-23" in log_text
    assert "rev-uuid-aaa" in log_text


def test_promote_entry_appends_verbatim_bytes(
    candidate_file: Path,
    locked_file: Path,
    promotions_log: Path,
) -> None:
    """The line written to locked must match the candidate's
    original bytes, not a re-serialised form."""
    db = _fake_db_returning(_review())
    raw_expected, _ = _find_candidate_line(
        candidate_file, "entry-002",
    )

    promote_entry(
        db=db,
        entry_id="entry-002",
        reviewer="talon",
        reason="ok",
        candidate_file=candidate_file,
        locked_file=locked_file,
        promotions_log=promotions_log,
    )

    locked_lines = [
        line for line in
        locked_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert locked_lines == [raw_expected]


# ── promote_entry: refusal paths ─────────────────────────────


def test_refuse_when_entry_id_missing(
    candidate_file: Path,
    locked_file: Path,
    promotions_log: Path,
) -> None:
    """A bogus id never makes it past the candidate-lookup step."""
    result = promote_entry(
        db=_fake_db_returning(_review()),
        entry_id="entry-999",
        reviewer="talon",
        reason="ok",
        candidate_file=candidate_file,
        locked_file=locked_file,
        promotions_log=promotions_log,
    )
    assert result.promoted is False
    assert "entry-999" in result.message
    assert locked_file.read_text(encoding="utf-8") == ""


def test_refuse_when_already_locked(
    candidate_file: Path,
    locked_file: Path,
    promotions_log: Path,
) -> None:
    """Re-promoting an id refused; must clone to a new id."""
    locked_file.write_text(
        _make_candidate_line("entry-001") + "\n",
        encoding="utf-8",
    )
    result = promote_entry(
        db=_fake_db_returning(_review()),
        entry_id="entry-001",
        reviewer="talon",
        reason="re-promote attempt",
        candidate_file=candidate_file,
        locked_file=locked_file,
        promotions_log=promotions_log,
    )
    assert result.promoted is False
    assert "already locked" in result.message
    # Locked file must NOT have grown a duplicate line.
    locked_lines = [
        line for line in
        locked_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(locked_lines) == 1


def test_refuse_when_review_below_4_stars(
    candidate_file: Path,
    locked_file: Path,
    promotions_log: Path,
) -> None:
    """Stars must be >= 4 for a non-forced promotion."""
    db = _fake_db_returning(_review(status="accept", stars=3))
    result = promote_entry(
        db=db,
        entry_id="entry-001",
        reviewer="talon",
        reason="should fail",
        candidate_file=candidate_file,
        locked_file=locked_file,
        promotions_log=promotions_log,
    )
    assert result.promoted is False
    assert "stars=3" in result.message
    assert "--force" in result.message
    assert locked_file.read_text(encoding="utf-8") == ""


def test_refuse_when_review_not_accept(
    candidate_file: Path,
    locked_file: Path,
    promotions_log: Path,
) -> None:
    """Status must be 'accept' even with 5 stars."""
    db = _fake_db_returning(
        _review(status="needs_revision", stars=5),
    )
    result = promote_entry(
        db=db,
        entry_id="entry-001",
        reviewer="talon",
        reason="should fail",
        candidate_file=candidate_file,
        locked_file=locked_file,
        promotions_log=promotions_log,
    )
    assert result.promoted is False
    assert "needs_revision" in result.message


def test_refuse_when_no_graded_review(
    candidate_file: Path,
    locked_file: Path,
    promotions_log: Path,
) -> None:
    """No reviews → can't promote without --force."""
    db = _fake_db_returning(None)
    result = promote_entry(
        db=db,
        entry_id="entry-001",
        reviewer="talon",
        reason="should fail",
        candidate_file=candidate_file,
        locked_file=locked_file,
        promotions_log=promotions_log,
    )
    assert result.promoted is False
    assert "no graded review" in result.message


# ── promote_entry: --force and --dry-run ─────────────────────


def test_force_bypasses_review_gate(
    candidate_file: Path,
    locked_file: Path,
    promotions_log: Path,
) -> None:
    """--force skips the review check entirely.  The audit log
    row records that no expert_review_id was attached."""
    result = promote_entry(
        db=None,  # --force allows no DB session
        entry_id="entry-001",
        reviewer="talon",
        reason="force-promoted to unblock baseline",
        candidate_file=candidate_file,
        locked_file=locked_file,
        promotions_log=promotions_log,
        force=True,
    )
    assert result.promoted is True
    assert result.expert_review_id is None

    log_text = promotions_log.read_text(encoding="utf-8")
    assert "force-promoted to unblock baseline" in log_text
    # The "(none)" sentinel marks rows without an expert review.
    assert "(none)" in log_text


def test_dry_run_writes_nothing(
    candidate_file: Path,
    locked_file: Path,
    promotions_log: Path,
) -> None:
    """--dry-run validates but leaves both target files
    untouched.  Result carries the computed hash so the caller
    can show what would have been written."""
    db = _fake_db_returning(_review())
    before_locked = locked_file.read_text(encoding="utf-8")
    before_log = promotions_log.read_text(encoding="utf-8")

    result = promote_entry(
        db=db,
        entry_id="entry-001",
        reviewer="talon",
        reason="dry run",
        candidate_file=candidate_file,
        locked_file=locked_file,
        promotions_log=promotions_log,
        dry_run=True,
    )

    assert result.promoted is False
    assert result.dry_run is True
    assert result.content_hash != ""
    assert locked_file.read_text(encoding="utf-8") == before_locked
    assert promotions_log.read_text(encoding="utf-8") == before_log


def test_missing_db_without_force_refuses(
    candidate_file: Path,
    locked_file: Path,
    promotions_log: Path,
) -> None:
    """Calling without a DB session AND without --force is a
    refusal (otherwise the review gate would silently no-op)."""
    result = promote_entry(
        db=None,
        entry_id="entry-001",
        reviewer="talon",
        reason="missing DB",
        candidate_file=candidate_file,
        locked_file=locked_file,
        promotions_log=promotions_log,
        force=False,
    )
    assert result.promoted is False
    assert "DB session" in result.message


# ── HARNESS-20 phase 2: --expert-review-id override ──────────


def test_expert_review_id_override_stamps_audit_row(
    candidate_file: Path,
    locked_file: Path,
    promotions_log: Path,
) -> None:
    """`--expert-review-id` lets a batch re-promotion run stamp
    the qualifying review id even on the --force path where no
    live DB lookup occurred (HARNESS-20 phase 2)."""
    review_id = "rev-uuid-phase2-001"

    result = promote_entry(
        db=None,
        entry_id="entry-001",
        reviewer="talon",
        reason="HARNESS-20 phase 2 retro-lock",
        candidate_file=candidate_file,
        locked_file=locked_file,
        promotions_log=promotions_log,
        force=True,
        expert_review_id_override=review_id,
    )
    assert result.promoted is True
    assert result.expert_review_id == review_id

    log_text = promotions_log.read_text(encoding="utf-8")
    assert review_id in log_text
    # The "(none)" sentinel must NOT show up for this row — the
    # override is exactly what prevents it.
    assert "| (none) |" not in log_text


def test_expert_review_id_override_wins_over_db_lookup(
    candidate_file: Path,
    locked_file: Path,
    promotions_log: Path,
) -> None:
    """Even on the non-force path (gate runs successfully), an
    explicit override replaces the DB-derived review id in the
    audit row.  Lets re-promotions document a *different*
    canonical review than whatever happens to be latest in the
    DB at the moment."""
    db = _fake_db_returning(_review(review_id="rev-from-db"))
    override = "rev-explicit-override"

    result = promote_entry(
        db=db,
        entry_id="entry-001",
        reviewer="talon",
        reason="document the canonical review explicitly",
        candidate_file=candidate_file,
        locked_file=locked_file,
        promotions_log=promotions_log,
        expert_review_id_override=override,
    )
    assert result.promoted is True
    assert result.expert_review_id == override
    log_text = promotions_log.read_text(encoding="utf-8")
    assert override in log_text
    assert "rev-from-db" not in log_text


# ── HARNESS-23 T11 (#151): stable manual-identity gate ───────


def test_stable_manual_id_problems_accepts_uuid() -> None:
    """A citation carrying the manuals.id UUID passes cleanly."""
    parsed = json.loads(_make_candidate_line("entry-001"))
    assert _stable_manual_id_problems(parsed) == []


def test_stable_manual_id_problems_flags_prose() -> None:
    """Cover-code prose (the MWS-150-A drift) is flagged with a
    message naming the offending value and index."""
    parsed = json.loads(
        _make_candidate_line("entry-001", manual_id="MWS-150-A"),
    )
    problems = _stable_manual_id_problems(parsed)
    assert len(problems) == 1
    assert "MWS-150-A" in problems[0]
    assert "golden_citations[0]" in problems[0]


def test_stable_manual_id_problems_flags_filename_stem() -> None:
    """Filename stems (the old schema-docstring convention) are
    just as unstable as cover codes — also flagged."""
    parsed = json.loads(
        _make_candidate_line(
            "entry-001", manual_id="MWS150A_Service_Manual",
        ),
    )
    assert len(_stable_manual_id_problems(parsed)) == 1


def test_stable_manual_id_problems_flags_missing_field() -> None:
    """A citation without a manual_id string is flagged rather
    than crashing the gate."""
    parsed = json.loads(_make_candidate_line("entry-001"))
    del parsed["golden_citations"][0]["manual_id"]
    problems = _stable_manual_id_problems(parsed)
    assert len(problems) == 1
    assert "missing" in problems[0]


def test_stable_manual_id_problems_empty_citations_pass() -> None:
    """OBD-lane entries have no manual citations — the gate must
    pass them vacuously."""
    parsed = json.loads(
        _make_candidate_line("entry-001", citations=False),
    )
    assert _stable_manual_id_problems(parsed) == []
    # Also tolerate the field being absent entirely.
    del parsed["golden_citations"]
    assert _stable_manual_id_problems(parsed) == []


def test_refuse_when_manual_id_is_prose(
    tmp_path: Path,
    locked_file: Path,
    promotions_log: Path,
) -> None:
    """promote_entry refuses a prose manual_id and writes
    nothing."""
    candidate = tmp_path / "prose.jsonl"
    candidate.write_text(
        _make_candidate_line("entry-001", manual_id="MWS150A")
        + "\n",
        encoding="utf-8",
    )
    result = promote_entry(
        db=_fake_db_returning(_review()),
        entry_id="entry-001",
        reviewer="talon",
        reason="should fail the T11 gate",
        candidate_file=candidate,
        locked_file=locked_file,
        promotions_log=promotions_log,
    )
    assert result.promoted is False
    assert "MWS150A" in result.message
    assert "manuals.id UUID" in result.message
    assert locked_file.read_text(encoding="utf-8") == ""


def test_force_does_not_bypass_manual_id_gate(
    tmp_path: Path,
    locked_file: Path,
    promotions_log: Path,
) -> None:
    """--force bypasses the review gate only; the manual-identity
    gate is a data-shape check and still refuses."""
    candidate = tmp_path / "prose.jsonl"
    candidate.write_text(
        _make_candidate_line("entry-001", manual_id="MWS-150-A")
        + "\n",
        encoding="utf-8",
    )
    result = promote_entry(
        db=None,
        entry_id="entry-001",
        reviewer="talon",
        reason="force cannot bypass T11",
        candidate_file=candidate,
        locked_file=locked_file,
        promotions_log=promotions_log,
        force=True,
    )
    assert result.promoted is False
    assert "not bypassed by --force" in result.message
    assert locked_file.read_text(encoding="utf-8") == ""


def test_entry_without_citations_promotes(
    tmp_path: Path,
    locked_file: Path,
    promotions_log: Path,
) -> None:
    """An OBD-lane-style entry (no golden_citations) passes the
    identity gate and promotes normally."""
    candidate = tmp_path / "obd.jsonl"
    candidate.write_text(
        _make_candidate_line("entry-obd-001", citations=False)
        + "\n",
        encoding="utf-8",
    )
    result = promote_entry(
        db=_fake_db_returning(_review()),
        entry_id="entry-obd-001",
        reviewer="talon",
        reason="obd entry, no citations",
        candidate_file=candidate,
        locked_file=locked_file,
        promotions_log=promotions_log,
    )
    assert result.promoted is True
    assert "entry-obd-001" in locked_file.read_text(
        encoding="utf-8",
    )


# ── HARNESS-21 [3/4]: --lane=obd defaults ────────────────────


def test_defaults_for_lane_manual_returns_mws_paths() -> None:
    """``_defaults_for_lane('manual')`` returns the original
    HARNESS-20 mws150a defaults."""
    from scripts.promote_golden import (
        _DEFAULT_CANDIDATE_FILE,
        _DEFAULT_LOCKED_FILE,
        _DEFAULT_PROMOTIONS_LOG,
        _defaults_for_lane,
    )
    cand, locked, log = _defaults_for_lane("manual")
    assert cand == _DEFAULT_CANDIDATE_FILE
    assert locked == _DEFAULT_LOCKED_FILE
    assert log == _DEFAULT_PROMOTIONS_LOG


def test_defaults_for_lane_obd_returns_yamaha_paths() -> None:
    """``_defaults_for_lane('obd')`` returns
    yamaha_road_test.jsonl paths and the shared PROMOTIONS.md."""
    from scripts.promote_golden import (
        _DEFAULT_OBD_CANDIDATE_FILE,
        _DEFAULT_OBD_LOCKED_FILE,
        _DEFAULT_PROMOTIONS_LOG,
        _defaults_for_lane,
    )
    cand, locked, log = _defaults_for_lane("obd")
    assert cand == _DEFAULT_OBD_CANDIDATE_FILE
    assert locked == _DEFAULT_OBD_LOCKED_FILE
    # PROMOTIONS.md shared between lanes.
    assert log == _DEFAULT_PROMOTIONS_LOG
    # OBD files target the yamaha corpus.
    assert "yamaha_road_test.jsonl" in cand.name
    assert "yamaha_road_test.jsonl" in locked.name


def test_defaults_for_lane_unknown_raises() -> None:
    """Unknown lane values raise ValueError."""
    from scripts.promote_golden import _defaults_for_lane

    with pytest.raises(ValueError) as exc:
        _defaults_for_lane("manual_eval")  # close to valid
    assert "manual_eval" in str(exc.value)


def test_cli_lane_flag_defaults_to_manual() -> None:
    """CLI without --lane gets manual-lane defaults."""
    from scripts.promote_golden import _build_parser

    args = _build_parser().parse_args(
        [
            "--entry-id", "x",
            "--reviewer", "y",
            "--reason", "z",
        ],
    )
    assert args.lane == "manual"
    # The path defaults stay None — resolution happens in main()
    # via _defaults_for_lane.  Tests of that resolution live in
    # the main()-level tests below; here we just pin that the
    # parser doesn't carry stale per-flag defaults that would
    # win over the lane-derived ones.
    assert args.candidate_file is None
    assert args.locked_file is None
    assert args.promotions_log is None


def test_cli_lane_flag_accepts_obd() -> None:
    """``--lane=obd`` parses cleanly."""
    from scripts.promote_golden import _build_parser

    args = _build_parser().parse_args(
        [
            "--entry-id", "x",
            "--reviewer", "y",
            "--reason", "z",
            "--lane", "obd",
        ],
    )
    assert args.lane == "obd"


def test_cli_lane_flag_rejects_unknown() -> None:
    """``--lane=garbage`` rejected by argparse choices."""
    from scripts.promote_golden import _build_parser

    with pytest.raises(SystemExit):
        _build_parser().parse_args(
            [
                "--entry-id", "x",
                "--reviewer", "y",
                "--reason", "z",
                "--lane", "garbage",
            ],
        )


def test_explicit_path_overrides_win_over_lane(tmp_path) -> None:
    """When ``--lane=obd`` is passed but explicit --candidate-file
    is also given, the explicit override wins.  Mirrors the
    documented main() resolution order."""
    from scripts.promote_golden import _build_parser

    custom = tmp_path / "custom.jsonl"
    args = _build_parser().parse_args(
        [
            "--entry-id", "x",
            "--reviewer", "y",
            "--reason", "z",
            "--lane", "obd",
            "--candidate-file", str(custom),
        ],
    )
    assert args.lane == "obd"
    # parser keeps the explicit path; main() will use it as-is
    # without applying the OBD default.
    assert args.candidate_file == custom
