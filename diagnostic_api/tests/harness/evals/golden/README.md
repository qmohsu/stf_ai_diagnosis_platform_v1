# Eval golden sets

This directory stores the human-reviewed golden datasets used by the
two evaluation lanes — the **manual-agent** lane (HARNESS-14) and
the **OBD-agent** lane (HARNESS-21).  The v2 set is a **two-tier
corpus** per HARNESS-20: a mutable candidate set for in-progress
refinement, and an append-only locked set that the eval harness
actually grades against.

## Directory layout

```
golden/
  v1/
    mws150a.jsonl                # manual lane (deprecated; pre-HARNESS-20)
    yamaha_road_test.jsonl       # OBD lane, Yamaha road-test fixture (PR [2a/4] authored here)
    yamaha_road_test_reference.json  # Sidecar: per-signal stats, event windows, DTCs
  v2/
    mws150a.jsonl                # MANUAL CANDIDATE — dashboard /goldens/manual
    yamaha_road_test.jsonl       # OBD CANDIDATE — dashboard /goldens/obd (PR [2b/4])
    locked/
      mws150a.jsonl              # MANUAL LOCKED — manual-eval reads this
      yamaha_road_test.jsonl     # OBD LOCKED — OBD-eval reads this (empty until first UI promotion)
      PROMOTIONS.md              # Audit trail; one row per promotion (both lanes)
    candidates/                  # (gitignored) raw author drafts pre-review
```

## Two-tier policy (HARNESS-20)

Every entry is born into the **candidate** tier
(`v2/mws150a.jsonl`).  Candidates are mutable: typo fixes, citation
swaps, prose rewrites all land here in response to dashboard review
feedback.  The dashboard syncs the candidate set into Postgres on
app startup and surfaces it to expert reviewers.

Once an expert posts a review with `status='accept'` and
`star_rating >= 4`, the entry becomes eligible for promotion to the
**locked** tier (`v2/locked/mws150a.jsonl`).  The eval harness
(`tests/harness/evals/test_manual_agent_eval.py`) reads ONLY the
locked tier.  Promoting an entry is what makes it count against any
agent-vs-RAG benchmark we publish.

Promotion is one-way and audit-trailed.  Run
`python -m scripts.promote_golden --entry-id <id> --reviewer <name>
--reason <why>` to append the candidate's JSONL line verbatim into
the locked file and record one row in `PROMOTIONS.md` (timestamp,
SHA-256 content hash, expert review id, reason).  The script refuses
the review-gate unless you pass `--force`; forced promotions are
flagged in the audit row so future readers can see why.

To revise a locked entry, **clone it under a new id** (e.g.
`<old-id>-revB`).  Edits to an already-locked line are caught by
the content hash on the next consistency check and would silently
re-score every historical eval report — exactly the drift the
two-tier split exists to prevent.

## Authoring convention: stable manual identity (HARNESS-23 T11, #151)

The first-round agent-vs-RAG baseline broke because goldens named the
vehicle by its **cover code** (`MWS-150-A`) while the corpus stored
`vehicle_model=TRICITY155` — the agent refused 27/30 entries because
no manual appeared to match the vehicle in the question.  APP-61
(#141) aliased the symptom by adding `manuals.factory_code`; the
durable fix is to never author a golden against prose identity in the
first place.

Rules when authoring or editing any golden entry:

1. **`golden_citations[].manual_id` MUST be the manual's UUID** —
   the `manuals.id` primary key (e.g.
   `0a2ba199-665f-41aa-a106-1163cad68d16`), which is also the
   `<manual_uuid>-` prefix of `GoldenEntry.id`.  Never a cover code
   (`MWS-150-A`), filename stem (`MWS150A_Service_Manual`), or
   marketing name (`TRICITY 155`).  Get the UUID from the ingested
   manual row (`SELECT id, manufacturer, vehicle_model, factory_code
   FROM manuals`) or the dashboard — not from the PDF cover.
2. **Prose vehicle names in `question` / `obd_context` must resolve
   against the corpus.**  Phrase them with an identifier the agent
   can actually match: `manufacturer` + `vehicle_model`, or the
   `factory_code` alias (APP-61).  Before promoting, check the
   ingested manual row and confirm every prose identifier in the
   entry appears on it.
3. **Enforcement**: `scripts/promote_golden.py` refuses to promote
   an entry whose citation `manual_id` is not a UUID.  This is a
   data-shape gate, not a review-quality gate — `--force` does NOT
   bypass it.  Fix the (mutable) candidate entry and re-promote.

## Lane routing

Each `GoldenEntry.question_type` drives the rubric:

| Lane | `question_type` values |
|---|---|
| manual | `lookup`, `procedural`, `cross-section`, `image-required`, `adversarial` |
| OBD | `signal_statistics`, `event_finding`, `dtc_enumeration`, `dtc_decode`, `compound_obd`, `adversarial_obd` |

The dispatcher in `metrics.py` (`_is_obd_lane`) consults the
question_type literal — adding a new lane means widening the
literal in `schemas.py` and updating the dispatcher.

## Immutability rules

1. **Candidate tier** (`v2/mws150a.jsonl`): mutable.  In-place
   edits are expected and routine during the review iteration
   window.  Always followed by an "admin note" review post in the
   dashboard so the expert is asked to re-grade the updated entry.
2. **Locked tier** (`v2/locked/mws150a.jsonl`): append-only.  The
   only legitimate way to write to it is `scripts/promote_golden.py`.
   `PROMOTIONS.md` records every write.  Revising a locked entry
   requires cloning to a new id; the script refuses to re-promote
   an id that is already locked.
3. **Deletions** from the locked tier always require a version bump
   (`v2/` → `v3/locked/`).
4. **v1** (`v1/mws150a.jsonl`) is deprecated.  Kept only for
   historical eval report comparability; new evals read v2/locked.
5. Every entry must round-trip through
   `tests.harness.evals.schemas.GoldenEntry.model_validate()` — the
   eval loader will refuse to load a malformed file.

## Generating candidates (future, Phase 3)

`scripts/generate_golden_candidates.py` picks sections from a real
ingested manual, asks Claude to produce a
`(question, summary, citations, must_contain)` tuple, and verifies
each citation exists in the source text. Output lands in
`golden/candidates/` and is human-reviewed via
`scripts/review_golden_candidates.py` before being promoted to
`golden/v1/`.

Neither script exists yet; see HARNESS-14 phase 3 in
`docs/v2_dev_plan.md`.

## Known limitations (2026-04-23, updated 2026-05-17)

- **One-manual corpus.** Only the `MWS150-A` (TRICITY 155 zh-CN)
  service manual is currently ingested. Acquiring a second manual
  is blocked by external physical availability. Consequences:
  - `list_manuals` is tested only at the unit level; at the agent
    level it is trivially correct (one manual to pick from).
  - Cross-manual disambiguation scenarios (e.g. "wrong
    `vehicle_model` filter") are **not** part of `v1/`.
  - When a second manual becomes available, bump goldens to `v2/`
    and add cross-manual scenarios.
- **Below target on DTC.** v1 has 1 dtc entry (target 8). Only one
  non-trivial DTC-procedure section survived generation +
  human review; the DTC Index appendix dominated the other
  candidates with low-value occurrence-count lookups. Raising
  DTC coverage requires either generator-heuristic improvements
  (skip appendix) or hand-written additions to v1. Tracked as a
  follow-up in HARNESS-14.
- **Small corpus for v1.** 10 entries total vs the 30-entry taxonomy
  target. Prioritised quality over quantity for the first freeze —
  additions permitted until v1 is declared frozen in the dev plan.

### OBD lane (HARNESS-21)

- **One-fixture corpus.** Only the Yamaha road-test fixture is
  graded by `v1/yamaha_road_test.jsonl`.  Honda and other vehicle
  fixtures are out of scope until a labelled multi-vehicle corpus
  exists.
- **Healthy-bike only.** The Yamaha fixture has no real faults;
  goldens grade descriptive accuracy ("what the data shows"), not
  diagnostic accuracy ("what's wrong").  Diagnostic-accuracy evals
  are a separate ticket once ground-truth fault recordings are
  available.
- **PR [1/3] dummies in place.** The three current entries are
  placeholder dummies that match the canned mock-agent response.
  Real hand-authored entries (10–15, covering all six OBD
  question types) land in PR [2/3].
- **Real-LLM session bootstrap deferred.** The Yamaha
  `OBDAnalysisSession` row needed for real-LLM runs is
  intentionally not created in PR [1/3] — the `yamaha_session_id`
  fixture skips with a clear message when `--mock-agent` is not
  set.  PR [2/3] addresses the path-resolution gap.

## Running the suite

Manual lane (HARNESS-14):
```
pytest --run-eval diagnostic_api/tests/harness/evals/test_manual_agent_eval.py
```

OBD lane (HARNESS-21) — PR [1/3] plumbing verification (no
external dependencies):
```
pytest -m eval --run-eval --mock-agent --mock-judge \
    diagnostic_api/tests/harness/evals/test_obd_agent_eval.py
```

OBD lane — phase-3 ceiling run (swap to GLM 5.1):
```
OBD_EVAL_AGENT_MODEL=z-ai/glm-5.1 \
    pytest -m eval --run-eval \
    diagnostic_api/tests/harness/evals/test_obd_agent_eval.py
```

Without `--run-eval`, eval-marked tests are skipped so normal
`pytest` runs stay fast and free. The run writes a timestamped
report to
`diagnostic_api/tests/harness/evals/reports/eval_{timestamp}.json`
(gitignored).
