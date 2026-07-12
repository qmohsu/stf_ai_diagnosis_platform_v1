#!/usr/bin/env python3
"""Promote a candidate golden entry into the locked tier.

HARNESS-20 (GitHub Issue #90).

The V2 golden corpus is split into two tiers:

- **Candidate** — ``tests/harness/evals/golden/v2/*.jsonl``.
  Mutable.  The dashboard syncs this set into Postgres on app
  startup; experts grade entries via the dashboard; entries can
  be edited in place during the iteration window.

- **Locked** — ``tests/harness/evals/golden/v2/locked/*.jsonl``.
  Append-only.  The eval harness
  (``tests/harness/evals/test_manual_agent_eval.py``) reads
  ONLY this tier.  Promoting an entry is what makes it count
  against any agent-vs-RAG benchmark we publish.

This script is the one-way bridge between the two tiers.  It
refuses to promote unless the most-recent expert review on the
entry is ``status='accept'`` with ``star_rating >= 4``, so a
typo fix that landed in the candidate set without the expert
re-grading it cannot accidentally end up locked.  Pass
``--force`` to override; the override is recorded in the
``reason`` column of ``locked/PROMOTIONS.md`` so the audit
trail makes the situation visible.

**Stable manual-identity gate (HARNESS-23 T11, GitHub Issue
#151).**  Every ``golden_citations[].manual_id`` must be the
ingested manual's UUID (the ``manuals.id`` primary key), never
cover-code prose or a filename stem.  The first-round baseline
broke because goldens named the vehicle by the cover code
``MWS-150-A`` while the corpus stored
``vehicle_model=TRICITY155`` — free-text identities drift;
UUIDs don't.  This gate is a data-shape check, NOT a
review-quality check, so ``--force`` does not bypass it: the
candidate tier is mutable, so the correct response to a refusal
is to fix the candidate's ``manual_id`` and re-promote.

Usage::

    # Normal promotion — driven off an expert ≥4★ accept review
    python -m scripts.promote_golden \\
        --entry-id <manual_uuid>-dtc-001 \\
        --reviewer talon \\
        --reason "expert >=4* on 2026-05-23"

    # Show what would happen without writing anything
    python -m scripts.promote_golden \\
        --entry-id <manual_uuid>-dtc-001 \\
        --reviewer talon \\
        --reason "expert >=4* on 2026-05-23" \\
        --dry-run

    # Override the review-quality gate (logs the override in
    # PROMOTIONS.md so future readers can see the rationale)
    python -m scripts.promote_golden \\
        --entry-id <manual_uuid>-dtc-001 \\
        --reviewer talon \\
        --reason "force-promoted to unblock #74 baseline" \\
        --force

Author: Li-Ta Hsu
Date: May 2026
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session


# ── Defaults ─────────────────────────────────────────────────


# Repo-root-relative defaults.  Resolved against this file's
# package position so the script works from any cwd.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Manual-lane defaults (HARNESS-20).
_DEFAULT_CANDIDATE_FILE = (
    _REPO_ROOT
    / "diagnostic_api"
    / "tests"
    / "harness"
    / "evals"
    / "golden"
    / "v2"
    / "mws150a.jsonl"
)
_DEFAULT_LOCKED_FILE = (
    _REPO_ROOT
    / "diagnostic_api"
    / "tests"
    / "harness"
    / "evals"
    / "golden"
    / "v2"
    / "locked"
    / "mws150a.jsonl"
)
_DEFAULT_PROMOTIONS_LOG = (
    _DEFAULT_LOCKED_FILE.parent / "PROMOTIONS.md"
)

# OBD-lane defaults (HARNESS-21 [3/4]).  Selected when
# ``--lane=obd`` is passed without explicit --candidate-file /
# --locked-file overrides.  PROMOTIONS.md is shared with the
# manual lane; promotion audit rows for both lanes append to
# the same file with a ``lane`` column.
_DEFAULT_OBD_CANDIDATE_FILE = (
    _REPO_ROOT
    / "diagnostic_api"
    / "tests"
    / "harness"
    / "evals"
    / "golden"
    / "v2"
    / "yamaha_road_test.jsonl"
)
_DEFAULT_OBD_LOCKED_FILE = (
    _REPO_ROOT
    / "diagnostic_api"
    / "tests"
    / "harness"
    / "evals"
    / "golden"
    / "v2"
    / "locked"
    / "yamaha_road_test.jsonl"
)


def _defaults_for_lane(
    lane: str,
) -> "tuple[Path, Path, Path]":
    """Return (candidate, locked, promotions_log) defaults for
    the given lane.  PROMOTIONS.md is shared between lanes.

    Args:
        lane: Either ``"manual"`` or ``"obd"``.

    Returns:
        Three-tuple of repo-absolute paths.

    Raises:
        ValueError: If ``lane`` is not a recognised value.
    """
    if lane == "manual":
        return (
            _DEFAULT_CANDIDATE_FILE,
            _DEFAULT_LOCKED_FILE,
            _DEFAULT_PROMOTIONS_LOG,
        )
    if lane == "obd":
        return (
            _DEFAULT_OBD_CANDIDATE_FILE,
            _DEFAULT_OBD_LOCKED_FILE,
            _DEFAULT_PROMOTIONS_LOG,
        )
    raise ValueError(
        f"Unknown lane {lane!r}; expected 'manual' or 'obd'.",
    )


# Minimum star rating and accepted status required for a
# non-forced promotion.  Both gates can be overridden via
# ``--force``; the override is recorded in PROMOTIONS.md.
_MIN_STAR_RATING = 4
_REQUIRED_STATUS = "accept"


# ── Return shape ─────────────────────────────────────────────


@dataclass
class PromotionResult:
    """Outcome of one promotion attempt.

    Returned by ``promote_entry`` so unit tests can inspect what
    happened without parsing stdout.  ``promoted=False`` paired
    with a ``message`` describes why the promotion was refused.
    """

    promoted: bool
    entry_id: str
    content_hash: str
    expert_review_id: Optional[str]
    message: str
    dry_run: bool


# ── Pure helpers (no DB / no FS side effects) ────────────────


def _canonical_dumps(obj: Dict[str, Any]) -> str:
    """Stable JSON serialisation used for hashing.

    ``sort_keys=True`` so re-ordered fields produce the same
    hash; ``ensure_ascii=False`` so Chinese characters appear
    in their native form (matches the JSONL file's encoding).
    """
    return json.dumps(
        obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )


def _sha256_hex(text: str) -> str:
    """SHA-256 hex digest of ``text`` encoded as UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _find_candidate_line(
    candidate_file: Path, entry_id: str,
) -> Tuple[str, Dict[str, Any]]:
    """Locate the candidate JSONL line for ``entry_id``.

    Returns the **raw** line (with trailing newline stripped)
    plus the parsed dict.  Raises ``KeyError`` if not present.
    The raw line is what gets appended verbatim to the locked
    file — preserving the exact bytes the candidate had makes
    diffs against future promotions clean.
    """
    with candidate_file.open(encoding="utf-8") as f:
        for line in f:
            text = line.rstrip("\n")
            if not text.strip():
                continue
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                continue
            if parsed.get("id") == entry_id:
                return text, parsed
    raise KeyError(
        f"entry_id {entry_id!r} not found in {candidate_file}",
    )


def _locked_ids(locked_file: Path) -> List[str]:
    """Return entry ids currently present in the locked file.

    Used to refuse re-promotion: once an entry is locked, the
    correct flow for a substantive change is to clone it under a
    new id (e.g. ``-revB``).  Editing the locked line in place
    would defeat the whole point of the tier.
    """
    if not locked_file.exists():
        return []
    out: List[str] = []
    with locked_file.open(encoding="utf-8") as f:
        for line in f:
            text = line.strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                continue
            entry_id = obj.get("id")
            if entry_id:
                out.append(entry_id)
    return out


def _stable_manual_id_problems(
    parsed: Dict[str, Any],
) -> List[str]:
    """Validate that citation manual ids are stable UUIDs.

    HARNESS-23 T11 (GitHub Issue #151).  Goldens must reference
    their vehicle via the ``manual_id``-linked identity — the
    ingested manual's ``manuals.id`` UUID — not via cover-code
    prose (``"MWS-150-A"``) or filename stems
    (``"MWS150A_Service_Manual"``).  Free-text identities drift
    between goldens, the DB, and manual filenames; the manual
    UUID is the one identifier every layer shares.

    Entries without ``golden_citations`` (e.g. OBD-lane entries)
    pass vacuously — the gate only constrains citations that
    exist.

    Args:
        parsed: The candidate entry as parsed from its JSONL
            line.

    Returns:
        A list of human-readable problem strings; empty when the
        entry passes the gate.
    """
    problems: List[str] = []
    citations = parsed.get("golden_citations") or []
    for idx, citation in enumerate(citations):
        if not isinstance(citation, dict):
            problems.append(
                f"golden_citations[{idx}] is not an object",
            )
            continue
        manual_id = citation.get("manual_id")
        if not isinstance(manual_id, str):
            problems.append(
                f"golden_citations[{idx}].manual_id is missing "
                "or not a string",
            )
            continue
        try:
            uuid.UUID(manual_id)
        except ValueError:
            problems.append(
                f"golden_citations[{idx}].manual_id "
                f"{manual_id!r} is not a manual UUID "
                "(cover-code prose / filename stems drift; "
                "use the manuals.id UUID)",
            )
    return problems


def _format_promotions_row(
    promoted_at: str,
    entry_id: str,
    content_hash: str,
    reviewer: str,
    expert_review_id: Optional[str],
    reason: str,
) -> str:
    """Format one row of the Markdown PROMOTIONS log.

    Pipe characters in free-text fields are escaped so they
    don't break the table layout.
    """
    def _escape(s: str) -> str:
        return s.replace("|", "\\|").replace("\n", " ").strip()

    return (
        f"| {promoted_at} "
        f"| {_escape(entry_id)} "
        f"| `{content_hash}` "
        f"| {_escape(reviewer)} "
        f"| {expert_review_id or '(none)'} "
        f"| {_escape(reason)} |\n"
    )


# ── DB-touching review-gate helper ───────────────────────────


def _latest_graded_review(
    db: Session, entry_id: str,
):
    """Return the most-recent review on ``entry_id`` that has a
    non-null ``star_rating``, or ``None`` if no graded review
    exists.

    Imported lazily so the script can be unit-tested without an
    app/database wiring step (tests pass a fake session that
    monkey-patches this function).
    """
    from app.models_db import GoldenReview  # noqa: WPS433

    return (
        db.query(GoldenReview)
        .filter(
            GoldenReview.golden_entry_id == entry_id,
            GoldenReview.star_rating.isnot(None),
        )
        .order_by(GoldenReview.created_at.desc())
        .first()
    )


# ── Core operation ───────────────────────────────────────────


def promote_entry(
    db: Optional[Session],
    entry_id: str,
    reviewer: str,
    reason: str,
    *,
    candidate_file: Path = _DEFAULT_CANDIDATE_FILE,
    locked_file: Path = _DEFAULT_LOCKED_FILE,
    promotions_log: Path = _DEFAULT_PROMOTIONS_LOG,
    force: bool = False,
    expert_review_id_override: Optional[str] = None,
    dry_run: bool = False,
    now: Optional[datetime] = None,
) -> PromotionResult:
    """Promote one candidate entry into the locked tier.

    Validation order (each step short-circuits with
    ``promoted=False`` if it fails):

    1. Candidate file must contain ``entry_id``.
    2. Locked file must NOT already contain ``entry_id`` — once
       locked, edits require cloning to a new id.
    3. Every ``golden_citations[].manual_id`` must be a stable
       manual UUID (HARNESS-23 T11, #151) — never cover-code
       prose or a filename stem.  NOT bypassed by ``force``:
       the candidate tier is mutable, so fix the candidate and
       re-promote.
    4. Most-recent graded review on the entry must be
       ``status='accept'`` with ``star_rating >= 4`` — unless
       ``force=True``.

    Side effects (only when ``dry_run=False`` and all checks
    pass):

    - Appends the raw candidate JSONL line, verbatim, to
      ``locked_file``.
    - Appends one row to ``promotions_log``.

    Args:
        db: SQLAlchemy session.  May be ``None`` when ``force``
            is true AND the caller has already established that
            review-gate validation can be skipped (used by the
            unit tests).  Required otherwise.
        entry_id: Stable id matching ``GoldenEntry.id`` and
            ``GoldenCandidate.id`` in the JSONL.
        reviewer: Short label for the human running the
            promotion (e.g. ``"talon"``).
        reason: Free-text justification.  Required even on
            normal accepts so reviewers can pin down which
            review the promotion is responding to.
        candidate_file: Source JSONL with the candidate entry.
        locked_file: Target JSONL to append to.
        promotions_log: Markdown audit file to append to.
        force: Skip the review-quality gate.  Records the
            override in the audit row.
        expert_review_id_override: When provided, stamps this id
            into the audit row's ``expert_review_id`` column
            regardless of the gate path.  Useful for batch
            re-promotion runs (HARNESS-20 phase 2) where the
            promoter has already validated the qualifying review
            externally and just needs to record its id without
            establishing a live DB session inside the script.
        dry_run: Validate everything but don't write.
        now: Optional fixed timestamp for deterministic tests.

    Returns:
        ``PromotionResult`` describing the outcome.

    Raises:
        FileNotFoundError: If ``candidate_file`` is missing.
    """
    if not candidate_file.exists():
        raise FileNotFoundError(
            f"candidate file missing: {candidate_file}",
        )

    # 1. Find the candidate line.
    try:
        raw_line, parsed = _find_candidate_line(
            candidate_file, entry_id,
        )
    except KeyError as exc:
        return PromotionResult(
            promoted=False,
            entry_id=entry_id,
            content_hash="",
            expert_review_id=None,
            message=str(exc),
            dry_run=dry_run,
        )

    # 2. Refuse re-promotion (idempotency would be wrong here —
    #    edits demand a new id; locked is append-only).
    if entry_id in _locked_ids(locked_file):
        return PromotionResult(
            promoted=False,
            entry_id=entry_id,
            content_hash="",
            expert_review_id=None,
            message=(
                f"entry_id {entry_id!r} is already locked; "
                "clone to a new id to publish a new revision."
            ),
            dry_run=dry_run,
        )

    # 3. Stable manual-identity gate (HARNESS-23 T11, #151).
    #    A data-shape check, not a review-quality check — so
    #    --force does NOT bypass it.  The candidate tier is
    #    mutable; fix the candidate's manual_id and re-promote.
    id_problems = _stable_manual_id_problems(parsed)
    if id_problems:
        return PromotionResult(
            promoted=False,
            entry_id=entry_id,
            content_hash="",
            expert_review_id=None,
            message=(
                "stable manual-identity gate failed "
                "(HARNESS-23 T11, #151): "
                + "; ".join(id_problems)
                + ".  Fix the candidate entry to cite the "
                "manuals.id UUID (not bypassed by --force)."
            ),
            dry_run=dry_run,
        )

    # 4. Review-quality gate (unless forced).
    review = None
    expert_review_id: Optional[str] = None
    if not force:
        if db is None:
            return PromotionResult(
                promoted=False,
                entry_id=entry_id,
                content_hash="",
                expert_review_id=None,
                message=(
                    "review-gate check requires a DB session; "
                    "pass --force to override."
                ),
                dry_run=dry_run,
            )
        review = _latest_graded_review(db, entry_id)
        if review is None:
            return PromotionResult(
                promoted=False,
                entry_id=entry_id,
                content_hash="",
                expert_review_id=None,
                message=(
                    "no graded review found; "
                    "pass --force to override."
                ),
                dry_run=dry_run,
            )
        if (
            review.status != _REQUIRED_STATUS
            or (review.star_rating or 0) < _MIN_STAR_RATING
        ):
            return PromotionResult(
                promoted=False,
                entry_id=entry_id,
                content_hash="",
                expert_review_id=str(review.id),
                message=(
                    f"latest graded review is "
                    f"status={review.status!r} "
                    f"stars={review.star_rating!r}; "
                    f"need status='accept' and "
                    f"stars>={_MIN_STAR_RATING}.  "
                    "Pass --force to override."
                ),
                dry_run=dry_run,
            )
        expert_review_id = str(review.id)

    # Caller-supplied override wins: lets batch re-promotion
    # runs (HARNESS-20 phase 2) stamp the qualifying review id
    # even on the --force path where no live DB lookup occurred.
    if expert_review_id_override is not None:
        expert_review_id = expert_review_id_override

    # Validation passed.  Compute hash + format audit row.
    content_hash = _sha256_hex(_canonical_dumps(parsed))
    promoted_at = (now or datetime.now(timezone.utc)).isoformat(
        timespec="seconds",
    )
    audit_row = _format_promotions_row(
        promoted_at=promoted_at,
        entry_id=entry_id,
        content_hash=content_hash,
        reviewer=reviewer,
        expert_review_id=expert_review_id,
        reason=reason,
    )

    if dry_run:
        return PromotionResult(
            promoted=False,
            entry_id=entry_id,
            content_hash=content_hash,
            expert_review_id=expert_review_id,
            message=(
                "dry-run: validation passed.  Would append:\n"
                f"  {locked_file}: {raw_line[:120]}...\n"
                f"  {promotions_log}: {audit_row.rstrip()}"
            ),
            dry_run=True,
        )

    # 5. Append verbatim to the locked JSONL (keeping the
    #    original line bytes so future diffs against the
    #    candidate show only intentional changes).
    locked_file.parent.mkdir(parents=True, exist_ok=True)
    with locked_file.open("a", encoding="utf-8", newline="\n") as f:
        f.write(raw_line)
        if not raw_line.endswith("\n"):
            f.write("\n")

    # 6. Append the audit row.  Mark down style: just append
    #    rows beneath the existing table.
    promotions_log.parent.mkdir(parents=True, exist_ok=True)
    with promotions_log.open(
        "a", encoding="utf-8", newline="\n",
    ) as f:
        f.write(audit_row)

    return PromotionResult(
        promoted=True,
        entry_id=entry_id,
        content_hash=content_hash,
        expert_review_id=expert_review_id,
        message=(
            f"promoted {entry_id} -> {locked_file.name} "
            f"(sha256={content_hash[:12]}...)"
        ),
        dry_run=False,
    )


# ── CLI plumbing ─────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser.  Factored out for testability."""
    p = argparse.ArgumentParser(
        prog="promote_golden",
        description=(
            "Promote a candidate golden entry into the locked "
            "tier (HARNESS-20)."
        ),
    )
    p.add_argument(
        "--entry-id",
        required=True,
        help="Stable id of the entry to promote.",
    )
    p.add_argument(
        "--reviewer",
        required=True,
        help=(
            "Human running the promotion (e.g. 'talon').  Not "
            "the workshop expert — that's tracked separately "
            "via expert_review_id."
        ),
    )
    p.add_argument(
        "--reason",
        required=True,
        help=(
            "Free-text justification.  Recorded in "
            "PROMOTIONS.md.  Be specific: cite the review id, "
            "the date, the GitHub issue, etc."
        ),
    )
    p.add_argument(
        "--lane",
        default="manual",
        choices=("manual", "obd"),
        help=(
            "Eval lane: 'manual' (HARNESS-14 mws150a, default for "
            "back-compat) or 'obd' (HARNESS-21 yamaha_road_test).  "
            "Selects default candidate/locked file paths when not "
            "overridden explicitly."
        ),
    )
    p.add_argument(
        "--candidate-file",
        type=Path,
        default=None,
        help=(
            "Override candidate JSONL path.  When None, derived "
            "from --lane."
        ),
    )
    p.add_argument(
        "--locked-file",
        type=Path,
        default=None,
        help=(
            "Override locked JSONL path.  When None, derived "
            "from --lane."
        ),
    )
    p.add_argument(
        "--promotions-log",
        type=Path,
        default=None,
        help=(
            "Override audit log path.  When None, derived from "
            "--lane (shared between lanes — both write rows to "
            "the same PROMOTIONS.md)."
        ),
    )
    p.add_argument(
        "--force",
        action="store_true",
        help=(
            "Skip the review-quality gate (latest review must "
            "be status='accept' with stars>=4).  The override "
            "is logged."
        ),
    )
    p.add_argument(
        "--expert-review-id",
        default=None,
        help=(
            "Override the audit row's expert_review_id column.  "
            "Useful in batch re-promotion runs where the gate "
            "was validated externally (e.g. HARNESS-20 phase 2) "
            "and the script runs with --force to skip the live "
            "DB lookup but should still attribute the source "
            "review."
        ),
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate without writing.",
    )
    return p


def main(argv: Optional[List[str]] = None) -> int:
    """Entry point.  Returns process exit code."""
    args = _build_parser().parse_args(argv)

    # Resolve lane-aware defaults for any path argument left None.
    # Explicit per-flag overrides win over the lane default.
    lane_candidate, lane_locked, lane_log = _defaults_for_lane(
        args.lane,
    )
    candidate_file = args.candidate_file or lane_candidate
    locked_file = args.locked_file or lane_locked
    promotions_log = args.promotions_log or lane_log

    # Open a DB session lazily so --force can skip the import
    # when running in a CI shell without DB credentials.
    db: Optional[Session] = None
    if not args.force:
        from app.db.session import SessionLocal  # noqa: WPS433

        db = SessionLocal()

    try:
        result = promote_entry(
            db=db,
            entry_id=args.entry_id,
            reviewer=args.reviewer,
            reason=args.reason,
            candidate_file=candidate_file,
            locked_file=locked_file,
            promotions_log=promotions_log,
            force=args.force,
            expert_review_id_override=args.expert_review_id,
            dry_run=args.dry_run,
        )
    finally:
        if db is not None:
            db.close()

    print(result.message)
    return 0 if (result.promoted or result.dry_run) else 1


if __name__ == "__main__":
    sys.exit(main())
