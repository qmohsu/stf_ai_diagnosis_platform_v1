# Manual-agent golden set

This directory stores the human-reviewed golden dataset used by the
manual-agent evaluation suite. Each subdirectory is a **frozen
version** of the set — once committed, entries in a version
directory are immutable.

## Directory layout

```
golden/
  v1/
    mws150a.jsonl   # one entry per line, GoldenEntry schema
  candidates/       # (gitignored) staging area for unreviewed candidates
```

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

## Known limitations (2026-04-23)

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

## Running the suite

```
pytest --run-eval diagnostic_api/tests/harness/evals/
```

Without `--run-eval`, eval-marked tests are skipped so normal
`pytest` runs stay fast and free. The run writes a timestamped
report to
`diagnostic_api/tests/harness/evals/reports/eval_{timestamp}.json`
(gitignored).
