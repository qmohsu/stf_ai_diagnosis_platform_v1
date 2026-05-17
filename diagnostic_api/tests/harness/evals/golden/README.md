# Eval golden sets

This directory stores the human-reviewed golden datasets used by the
two evaluation lanes — the **manual-agent** lane (HARNESS-14) and
the **OBD-agent** lane (HARNESS-21).  Each subdirectory is a
**frozen version** of the set: once committed, entries in a version
directory are immutable.

## Directory layout

```
golden/
  v1/
    mws150a.jsonl              # manual lane, MWS150-A service manual
    yamaha_road_test.jsonl     # OBD lane, Yamaha road-test fixture
  v2/                          # (HARNESS-15 expansions)
  candidates/                  # (gitignored) staging for unreviewed candidates
```

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

1. **Do not edit entries in `v1/` in place.** If an entry is found
   to be incorrect after freeze, bump the whole set to a new version
   (`v2/`) and fix the entry there. This prevents silent eval-set
   drift and keeps historical eval reports comparable.
2. **Additions** to an existing version directory are allowed only
   during the active build-out window (phase 3/4 of HARNESS-14). Once
   a version is declared frozen in the dev plan, it is append-only
   closed.
3. **Deletions** always require a version bump.
4. Every entry must round-trip through
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
