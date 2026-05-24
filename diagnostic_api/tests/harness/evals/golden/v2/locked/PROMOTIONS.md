# Locked-tier promotion log (HARNESS-20)

This file is the append-only audit trail for the locked tier of
the V2 golden corpus.  Every line in `locked/*.jsonl` corresponds
to exactly one row here.

## When a row is added

`scripts/promote_golden.py` appends one row whenever an entry is
promoted from the mutable candidate set
(`tests/harness/evals/golden/v2/*.jsonl`) into the canonical
locked set (`tests/harness/evals/golden/v2/locked/*.jsonl`).  The
eval harness (`tests/harness/evals/test_manual_agent_eval.py`)
reads only the locked tier, so promoting an entry is what makes
it count against the agent-vs-RAG benchmark.

Promotion requires that the most-recent expert review on the
entry is `status='accept'` with `star_rating >= 4`, unless the
promoter passes `--force` (which is then noted in the `reason`
column).

## Why this file is plain Markdown

A DB table would be invisible during code review and would let
anyone with `psql` access rewrite history.  A flat audit file in
the repo means every promotion shows up as a git diff and is
permanently attributable via `git blame`.

## Row format

| Column | Meaning |
|---|---|
| `promoted_at` | UTC ISO-8601 timestamp produced by the script. |
| `entry_id` | The golden entry's stable id (e.g. `<manual_uuid>-dtc-001`). |
| `content_hash` | SHA-256 of the canonical-form (`sort_keys=True`) JSON of the promoted line, hex-encoded.  Lets a future tool detect whether the locked line ever drifts away from what was originally promoted. |
| `reviewer` | Free-text label for the human who ran the promotion (e.g. `talon`, `talon@polyu`).  Promoter, not the workshop expert — the expert's stars live in `golden_reviews`. |
| `expert_review_id` | UUID of the `golden_reviews` row whose acceptance triggered this promotion.  Resolves the audit chain back to the expert grade.  May be omitted when `--force` was used; the `reason` column then carries the justification. |
| `reason` | Short free-text explanation.  Mandatory.  Examples: `"expert ≥4★ on 2026-05-23"`, `"force-promoted to unblock #74; expert review pending"`. |

## Rows

<!-- Appended by promote_golden.py.  Do not edit by hand; the
     content_hash protects locked entries from silent edits, and
     editing this file would only mask drift. -->

| promoted_at | entry_id | content_hash | reviewer | expert_review_id | reason |
|---|---|---|---|---|---|
