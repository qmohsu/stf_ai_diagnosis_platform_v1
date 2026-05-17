# HARNESS-21 — OBD sub-agent evaluation framework (design)

**Author**: Li-Ta Hsu
**Date**: 2026-05-17
**Issue**: [#97](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/97)
**Mirrors**: HARNESS-14 / [#73](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/73) (manual-agent eval framework)
**Status**: approved, pending implementation plan

## Problem

The OBD sub-agent (`app/harness_agents/obd_agent.py`) is in production as
of HARNESS-19, wired into the main agent via `delegate_to_obd_agent`. Its
136 unit tests cover mechanics — tool dispatch, type validation, formatter
rendering — but verify nothing about whether the agent's *answers* are
correct against a real OBD log. The manual sub-agent has had a complete
evaluation framework since HARNESS-14: restricted ReAct runner,
LLM-as-judge with rubric scoring, hand-authored frozen goldens, pytest
plumbing under `@pytest.mark.eval` + `--run-eval`. The OBD side has none
of that.

## Goal

Build the parallel evaluation suite for the OBD sub-agent against the
existing Yamaha road-test fixture (`obd_agent/fixtures/yamaha_dual_road_test_20260508.csv`,
[#80](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/80)).
Reuse the manual-side scaffolding where structurally identical; diverge
only where OBD data shape requires it. Grade *descriptive accuracy*
(the agent correctly characterizes what the data shows), not *diagnostic
accuracy* (the agent correctly identifies a fault) — the Yamaha fixture
is a healthy bike with no ground-truth faults to detect.

## Design decisions (from the brainstorming pass)

| Decision | Choice |
|---|---|
| Schema strategy | Extend `SystemRunResult` with optional `obd_signal_citations` + `obd_dtc_citations`; extend `GoldenEntry` with `expected_signal_citations` + `expected_dtcs` + `expected_no_evidence` |
| Refusal grading (adversarial / dtc_decode) | Explicit `expected_no_evidence: bool` flag flips citation-empty grading polarity; `pitfall_directives` catch affirmative hallucinations via LLM judge |
| Numerical value tolerance | 5% relative by default, per-citation override via `value_tolerance_rel` |
| Integration depth | Sibling modules: `obd_runner.py` + `metrics_obd.py` + `test_obd_agent_eval.py` alongside the manual versions; share `schemas.py` + `judge.py` + `conftest.py` |
| PR cadence | 3 PRs: scaffolding+dummy → real goldens → baseline+iterate; LLM-driven generator scripts deferred |

## § 1 — Architecture & file layout

```
diagnostic_api/
  tests/
    harness/
      evals/
        schemas.py                 # EXTENDED (additive only)
        judge.py                   # UNCHANGED (used as-is)
        metrics.py                 # MINOR (dispatcher + new dim slot)
        metrics_obd.py             # NEW — OBD deterministic metrics
        runner.py                  # UNCHANGED
        obd_runner.py              # NEW — run_obd_agent + adapter
        conftest.py                # UNCHANGED
        test_manual_agent_eval.py  # UNCHANGED
        test_obd_agent_eval.py     # NEW — parametrized over yamaha goldens
        golden/
          v1/
            yamaha_road_test.jsonl # NEW — 10–15 hand-authored entries
          README.md                # UPDATED — note the OBD lane
        reports/                   # gitignored, shared with manual lane
  scripts/
    compute_yamaha_reference.py    # NEW — author aid (not LLM-driven)
  docs/
    harness_21_phase5_baseline.md  # NEW — phase 3 deliverable
```

Reuse points:

- `judge.py` — pitfall_directives and answer_quality are content-agnostic;
  OBD `output_text` is the agent's summary + serialized citations, graded
  the same way as manual output.
- `conftest.py` — `--run-eval` flag and `eval_report` fixture work
  unchanged.
- `compute_overall()` weights — same shape; OBD-exclusive dimensions
  short-circuit to 1.0 (neutral) for manual entries.

## § 2 — Schema extensions (additive only)

`tests/harness/evals/schemas.py`:

```python
SystemLabel = Literal["manual_agent", "rag", "obd_agent"]   # widened

GoldenQuestionType = Literal[
    # existing manual-side
    "lookup", "procedural", "cross-section", "image-required", "adversarial",
    # new OBD-side
    "signal_statistics", "event_finding", "dtc_enumeration",
    "dtc_decode", "compound_obd", "adversarial_obd",
]

class ExpectedSignalCitation(BaseModel):
    """Golden reference for one signal citation.

    Match by `signal` (case-insensitive). When `stat`, `time_range`, or
    `value` is specified, those must also match. Tolerance applies only
    when comparing `value`.
    """
    signal: str
    stat: Optional[str] = None
    value: Optional[float] = None
    value_tolerance_rel: float = 0.05
    time_range: Optional[Tuple[str, str]] = None

class ExpectedDTC(BaseModel):
    code: str
    status: Optional[Literal["stored", "pending"]] = None

class SystemRunResult(BaseModel):
    # existing fields unchanged...
    obd_signal_citations: List[SignalCitation] = Field(default_factory=list)
    obd_dtc_citations:    List[DTCCitation]    = Field(default_factory=list)

class GoldenEntry(BaseModel):
    # existing fields unchanged...
    expected_signal_citations: List[ExpectedSignalCitation] = Field(default_factory=list)
    expected_dtcs:             List[ExpectedDTC]             = Field(default_factory=list)
    expected_no_evidence: bool = False
```

Adapter (`obd_runner.py`): `OBDAgentResult → SystemRunResult` with
`system_label="obd_agent"`, `claim_slugs=[]`, `read_slugs=[]`,
`output_text = summary + serialized citations`, `obd_*_citations` passed
through.

## § 3 — OBD-specific deterministic metrics

`metrics_obd.py`:

```python
@dataclass(frozen=True)
class OBDDeterministicMetrics:
    signal_recall:    float
    signal_precision: float
    value_accuracy:   float
    dtc_accuracy:     float
```

- `signal_recall = |matched_expected_signals| / max(|expected_signal_citations|, 1)` — match by signal name (case-insensitive); when golden specifies `stat` or `time_range`, those must also match (time-range *overlap*, not equality).
- `signal_precision = |cited_signals ∩ expected_signals| / max(|cited_signals|, 1)` — penalizes "cite everything to be safe."
- `value_accuracy` — over citations where both sides specify `value`: `|actual - expected| ≤ expected * value_tolerance_rel` ⇒ hit. Score = `hits / max(comparisons, 1)`; **defaults to 1.0** when no comparable pairs (most question types). Zero-expected guard: `abs(actual) ≤ 0.01` when `expected == 0`.
- `dtc_accuracy` — Jaccard over case-insensitive DTC codes; status mismatch counts as miss when status is specified.

Reused from `metrics.py` unchanged: `fact_recall`, `fact_density`,
`trajectory_efficiency`.

**`expected_no_evidence` polarity flip**: when `True`,
`signal_recall` and `dtc_accuracy` become `1.0` iff the agent cited
nothing (`|cited| == 0`); `value_accuracy` defaults to `1.0`;
`pitfall_directives` catch "didn't cite but still asserted fault."

**Folding into `overall`**:

```
overall = 0.20 * section_recall_or_signal_recall
        + 0.10 * claim_precision_or_signal_precision
        + 0.05 * (1 - exploration_cost)
        + 0.15 * fact_recall
        + 0.05 * fact_density
        + 0.15 * hallucination_penalty
        + 0.05 * citation_quality_or_dtc_accuracy
        + 0.10 * value_accuracy
        + 0.15 * answer_quality
```

Each row's dispatcher in `metrics.compute_deterministic_metrics` picks
the OBD dim when `entry.expected_signal_citations` is non-empty (OBD
lane), else the manual dim. `value_accuracy` is OBD-exclusive — neutral
1.0 for manual entries.

## § 4 — Judge wiring (unchanged) + OBD output_text format

`judge.py` is reused as-is. Same model (`z-ai/glm-5.1`), same retry
policy, same parser. The judge sees OBD content via:

- Widened `question_type` literal in the user prompt.
- Pitfall directives authored for OBD failure modes (no misfire
  assertion, no fabricated Yamaha hex decode, no invented signal values).
- `output_text` from the OBD adapter:

```
<agent.summary>

--- Signal citations (3) ---
RPM (p95) = 2941.0 rpm  @ [2026-05-08T11:20:39, 2026-05-08T11:24:55]
COOLANT_TEMP (max) = 84.0 °C  @ [...]
SPEED (mean) = 6.2 km/h

--- DTC citations (2) ---
87F11043000000000000CB (stored, K-Line)
44F2305A000000000000AB (stored, K-Line)

--- Limitations ---
- Yamaha hex DTC codes are not decodable without manufacturer-specific decoder.
```

No `JUDGE_SYSTEM_PROMPT` change in v1; revisit only if phase-3 baseline
shows the judge systematically under-grading OBD entries.

## § 5 — Golden set (10–15 hand-authored entries)

Distribution across 6 OBD question types:

| Question type | Count | What "correct" looks like |
|---|---|---|
| `signal_statistics` | 3 | Cites signal with mean/p95/max within tolerance |
| `event_finding`     | 2 | Cites signal with time_range overlapping the true window |
| `dtc_enumeration`   | 2 | Lists both Yamaha-hex codes exactly; correct ECU; status |
| `dtc_decode`        | 2 | Honest pivot — `limitations` notes "no decoder"; no fabricated translation |
| `compound_obd`      | 2 | ≥3 signal citations + coherent narrative + no fabrication |
| `adversarial_obd`   | 1–3 | `expected_no_evidence=True`; pitfall on the would-be fault |

12 entries floor, 15 ceiling; second-variant slots go to `dtc_decode`
and `adversarial_obd` if a single entry doesn't exercise both failure
modes (fabricate vs skip; affirm vs avoid).

**Authoring workflow**: a one-time script
(`diagnostic_api/scripts/compute_yamaha_reference.py`) loads the
Yamaha CSV via the existing `obd_loader`, prints exact stats per signal
(mean, p95, min, max, std) and event windows for common thresholds.
The author copies those numbers into the goldens; pitfall_directives
and `must_contain` are written by hand.

**Immutability**: `golden/v1/yamaha_road_test.jsonl` is git-tracked
and never edited in place; corrections bump to `v2/yamaha_road_test.jsonl`.

## § 6 — Pytest plumbing & report format

```python
@pytest.mark.eval
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entry", load_golden("v1/yamaha_road_test.jsonl"),
    ids=lambda e: e.id,
)
async def test_obd_agent(entry, eval_report):
    run = await run_obd_agent_unified(entry.question, _yamaha_session_id())
    grade = await grade_run(entry, run)
    eval_report.record(entry, run, grade)
    assert grade.overall >= 0.6, grade.reasoning
```

Threshold starts at **0.6** (manual lane is 0.7); raised in PR [3/3]
once the baseline is known.

**Yamaha session bootstrap**: a `pytest` fixture upserts an
`OBDAnalysisSession` with `UUID5(fixture_path)` at suite start, loads
the CSV via `obd_loader`, persists across runs.

**Ceiling runs**: `OBD_EVAL_AGENT_MODEL=z-ai/glm-5.1 pytest -m eval --run-eval ...`
— env var read by `obd_runner._build_default_deps()`, swaps Ollama for
an OpenRouter `AsyncOpenAI` client.

**Report format**: `reports/eval_{ts}.json`, same envelope as the manual
lane, two extra blocks per OBD entry (`obd_signal_citations`,
`obd_dtc_citations`). `report_summary.py` (PR [3/3]) emits markdown
baseline scorecard.

**Default `pytest` unchanged**: `@pytest.mark.eval` is skipped without
`--run-eval`; CI runs the existing unit tests at the same speed.

## § 7 — Phasing (3 PRs)

### PR [1/3] — Scaffolding + 3 dummy goldens, no LLM calls

Adds: extended `schemas.py`; `metrics_obd.py`; dispatcher in `metrics.py`;
`obd_runner.py`; `test_obd_agent_eval.py`; 3 dummy entries in
`golden/v1/yamaha_road_test.jsonl`; `golden/README.md` update; unit
tests (`test_metrics_obd.py`, `test_obd_runner.py`, additions to
`test_judge.py`); `compute_yamaha_reference.py`; V2 doc updates.

Acceptance: unit tests green; `pytest -m eval --run-eval` runs 3 dummy
entries (mocked LLM) and produces a `reports/eval_{ts}.json`; manual
lane untouched.

### PR [2/3] — 10–12 hand-authored Yamaha goldens, real LLM

Adds: full v1 golden set per § 5 distribution; V2 doc milestone update.
No code changes beyond goldens.

Acceptance: `pytest -m eval --run-eval` runs the full set against
local Qwen + GLM judge, produces complete report. Test threshold not
yet pinned.

### PR [3/3] — Baseline scorecard + prompt iteration

Adds: `report_summary.py`; `docs/harness_21_phase5_baseline.md`; any
`obd_agent_prompts.py` tweaks motivated by baseline failures; tuned
threshold; closure entries in `v2_dev_plan.md` and `v2_design_doc.md`.

Acceptance: baseline doc landed; eval passes at new threshold; v2 docs
bumped.

## Out of scope (tracked separately)

- Multi-vehicle expansion (Honda etc.) — bumps to `v2/yamaha_road_test.jsonl` + adds new vehicle goldens when fixture exists
- LLM-driven `generate_golden_candidates.py` OBD variant — issue flags as overkill for one fixture
- Labeled-fault diagnostic-accuracy eval — needs ground-truth fault recordings
- Cross-language OBD eval — OBD output is English-only
- CI integration — eval stays opt-in (cost + latency); add nightly job in a separate ticket if needed
