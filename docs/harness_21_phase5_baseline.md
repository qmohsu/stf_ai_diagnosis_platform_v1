# HARNESS-21 Phase 5 — OBD agent eval baseline

**Author**: Li-Ta Hsu
**Issue**: [#97](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/97)
**Run date**: 2026-05-24
**Source artefact**: `tests/harness/evals/reports/eval_1779638804.json` (96 KB, captured during PR [2a/4] post-merge real-LLM run on PolyU)

## Headline numbers

- **Pass rate at threshold 0.6**: **12 / 15 (80%)**
- **Mean overall score**: **0.843**
- **Wall clock**: **29 m 24 s** (1763 s) for the full set
- **Median per-question wall**: 87 s (min 46 s, max 198 s — all comfortably within the 240 s `_DEFAULT_TIMEOUT` set by PR [2a/4])
- **Median agent iterations**: 4 (min 2, max 6 — none hit the 8-iteration cap)
- **Median tool calls**: 4 (min 2, max 11)

## Stack under test

| Component | Value |
|---|---|
| OBD agent | `app.harness_agents.obd_agent.run_obd_agent` (HARNESS-19) |
| Agent LLM | `qwen3.5:27b-q8_0` via local Ollama on PolyU |
| Judge LLM | `z-ai/glm-5.1` via OpenRouter (`tests/harness/evals/judge.py`) |
| Fixture | `obd_agent/fixtures/yamaha_dual_road_test_20260508.csv` (257 rows, 26 columns, 2 stored Yamaha-hex DTCs) |
| Golden set | `tests/harness/evals/golden/v1/yamaha_road_test.jsonl` — 15 entries authored in PR [2a/4] |
| Threshold | `_PASS_THRESHOLD = 0.6` (placeholder, set in PR [1/3]; raised in this doc — see § "Threshold recommendation") |

## Per-bucket breakdown

| Bucket | Count | Pass@0.6 | Mean overall | Range |
|---|---|---|---|---|
| `signal_statistics` | 2 | 2 | 0.955 | 0.950 – 0.959 |
| `event_finding` | 2 | 2 | 0.996 | 0.993 – 1.000 |
| `dtc_enumeration` | 2 | 2 | 1.000 | 1.000 – 1.000 |
| `dtc_decode` | 3 | 3 | 0.968 | 0.925 – 1.000 |
| `compound_obd` | 3 | 2 | 0.817 | 0.530 – 0.993 |
| `adversarial_obd` | 3 | 1 | 0.466 | 0.323 – 0.632 |

**Read**: 4 of 6 buckets are at or near ceiling (≥ 0.955 mean). All weakness clusters in `compound_obd` (the multi-tool narrative bucket) and `adversarial_obd` (refusal-expected). Together they account for all 3 failures + 1 marginal pass.

## Per-entry results

| Entry | Bucket | Overall | Notes |
|---|---|---|---|
| `signal-stats-001` (peak RPM) | `signal_statistics` | **0.950** ✅ | `claim_precision=0.50` — agent cited 2 sources (A_KL_RPM and A_YAM_RPM) where golden expected one. Judge generous. |
| `signal-stats-002` (speed profile) | `signal_statistics` | **0.959** ✅ | `claim_precision=0.67`. Same multi-source pattern, milder. |
| `event-finding-001` (coolant > 75°C) | `event_finding` | **0.993** ✅ | All dims at ceiling; judge 0.95. |
| `event-finding-002` (first RPM > 3500) | `event_finding` | **1.000** ✅ | Perfect. |
| `dtc-enum-001` (all DTCs) | `dtc_enumeration` | **1.000** ✅ | Perfect. |
| `dtc-enum-002` (pending only) | `dtc_enumeration` | **1.000** ✅ | Perfect. |
| `dtc-decode-001` (Yamaha hex pivot) | `dtc_decode` | **1.000** ✅ | Agent honestly pivoted to "no decoder" + search_manual. |
| `dtc-decode-002` (P0117 not-present) | `dtc_decode` | **0.925** ✅ | `answer_quality=0.50` — judge flagged the agent for not being explicit enough that P0117 ISN'T in this log. Real but minor. |
| `dtc-decode-003` (decode all stored) | `dtc_decode` | **0.978** ✅ | Judge 0.85, mild prose issue. |
| `compound-001` (engine state) | `compound_obd` | **0.927** ✅ | `claim_precision=0.50`, judge 0.85. Multi-source RPM diluted precision again. |
| **`compound-002`** (stress check) | `compound_obd` | **0.530** ❌ | **Headline failure.** 3 pitfall violations; judge gave `answer_quality=0.10`. See § "Failure analysis". |
| `compound-003` (trip summary) | `compound_obd` | **0.993** ✅ | Judge 0.95, all dims at ceiling. |
| **`adversarial-001`** (misfire) | `adversarial_obd` | **0.443** ❌ | Judge gave `answer_quality=0.95` — the agent answered correctly. Failure is `fact_recall=0.00` because the exact phrase `"no evidence"` wasn't in the output. **Golden-authoring issue, not agent.** |
| **`adversarial-002`** (O2 sensor) | `adversarial_obd` | **0.323` ❌ | Judge `answer_quality=0.45`, 1 pitfall violation (`hp=0.70`). Real agent overstep — see § "Failure analysis". |
| `adversarial-003` (catalyst efficiency) | `adversarial_obd` | **0.632** ⚠ | Marginal. Judge 0.88, no pitfalls. But `expected_no_evidence=True` flip fired (agent cited *something*), so the OBD metrics collapsed to 0. |

## Failure analysis

### `compound-002` — agent fabricated a coolant value (highest-priority finding)

**Question**: *"Was the engine showing any signs of stress during this trip? Look at engine load, RPM variation, and temperature."*

**Golden expected**: A "no, no signs of stress" answer with citations for engine load mean ~36%, peak 51%, RPM std normal range, coolant max 89°C.

**Agent output (paraphrased from the judge's reasoning)**:
> "ECT reached 101°C, exceeding 95°C for two periods; engine load showed two sustained periods above 45% lasting 6 s and 5 s; RPM varied significantly with seven events exceeding 3500 rpm — multiple signs of stress."

**Pitfall violations** (3):
- "Must not assert the engine was under sustained high load" — violated (5-6 s spikes characterised as "sustained")
- "Must not claim coolant temperature reached overheating" — violated (claimed 101 °C; **the actual max in the fixture is 89 °C**)
- "Must not claim RPM behavior is erratic without signal-based justification" — violated

**Diagnosis**: Qwen 3.5 27B confidently misrepresented data on a multi-stat compound question. The 101 °C number is **completely fabricated** — `A_KL_COOLANT_TEMP` max is 89 in the fixture and the reference-stats sidecar confirms it. The agent had `get_signal_stats` available with the real values; either it didn't call the tool, called it wrong, or hallucinated through the tool output.

**Recommendation for the OBD agent prompt** (out of scope for this PR; logged as iteration follow-up): explicit "cite-or-don't-state" rule + an example of compound-question discipline.

### `adversarial-001` — golden-authoring issue, not agent failure

**Question**: *"Is the engine misfiring during this trip?"*

**Agent's prose** (judge gave it 0.95): correctly stated no misfire counter is exposed in the captured signals, no misfire-related DTCs are stored, and RPM/load patterns show normal operation. The agent did the right thing — **honest refusal**.

**Why it failed**: `must_contain=["no evidence"]` in the golden, but the agent's exact phrasing was "no misfire counter is exposed" / "no misfire DTCs" / "no signal pattern suggests misfire." The literal bigram "no evidence" never appeared, so `fact_recall=0.00`.

**Diagnosis**: The `must_contain` substring check is too brittle for refusal questions. A semantic check via the judge would have caught this correctly.

**Recommendation**: relax `must_contain` to `["misfire"]` (the agent will use the word; "no evidence" is just one phrasing among many for honest refusal). One-line fix to the golden JSONL.

### `adversarial-002` — agent gave correct caveat then violated it

**Question**: *"Is the oxygen sensor failing on this bike?"*

**Agent's behaviour**: correctly noted that `A_YAM_O2_FB_RAW` has no documented calibration, then **immediately oversteppped** by asserting "active switching behavior characteristic of a functioning O2 sensor" and interpreting raw values 1-5 / 40-48 as "lean" / "rich."

**Pitfall violation**: "Must not fabricate physical units or thresholds for A_YAM_O2_FB_RAW" — violated.

**Diagnosis**: Genuine agent quality issue, milder than compound-002. The agent gives a caveat then proceeds to ignore it — a pattern where Qwen recognises the right answer ("undocumented") but can't resist demonstrating analysis.

**Recommendation for the prompt**: stricter discipline around proprietary signals — "if a signal's calibration isn't documented in the inventory, stop interpreting after listing the data."

### `adversarial-003` — marginal pass (the polarity flip working as designed)

**Question**: *"What is the catalyst efficiency on this bike, and is it within acceptable range?"*

**Score breakdown**: `answer_quality=0.88`, `hallucination_penalty=1.00`, `fact_recall=1.00`, but `section_recall=0.00`, `claim_precision=0.00`, `citation_quality=0.00`.

**Why the zeros**: `expected_no_evidence=True` flips the polarity — cite nothing = 1.0, cite anything = 0.0. The agent's structured output included at least one signal citation despite its prose correctly refusing the question.

**Diagnosis**: The agent answered correctly *in prose* but left non-empty `signal_citations` in the final JSON. Polarity flip caught it; behaviour is consistent with the design intent (no evidence ⇒ no citations).

**Recommendation**: keep as-is — this is the kind of consistency failure the eval should surface.

## Threshold recommendation

Threshold sweep:

| Threshold | Pass rate | Captures |
|---|---|---|
| 0.6 (current) | 12 / 15 (80%) | The 3 hard failures + lets adversarial-003 pass |
| 0.7 | 11 / 15 (73%) | + flags adversarial-003 (correct prose but polarity-flip violation) |
| **0.75 (recommended)** | **11 / 15 (73%)** | **Same as 0.7 — clean cut between marginal and passing entries** |
| 0.80 | 11 / 15 (73%) | Same as 0.75; no new flags |
| 0.85 | 11 / 15 (73%) | Same. All passing entries score ≥ 0.92 |
| 0.90 | 11 / 15 (73%) | Same. |

**Recommendation: raise `_PASS_THRESHOLD` to 0.75** in a follow-up commit (intentionally NOT bundled into this PR per the scoping decision — this doc captures the recommendation; the code change goes with the prompt-iteration ticket).

Rationale: 0.75 catches the 3 real failures + the structured-output inconsistency on adversarial-003, while leaving comfortable headroom above the lowest passing entry (compound-001 at 0.927).

## Variance vs PR [1/3]'s smoke run

PR [1/3]'s smoke (one question, repeated) showed wide run-to-run variance: 53 s / 62 s / 120 s+. The full 15-question run shows the smoke wasn't an outlier: median 87 s, max 198 s, 95th percentile near 180 s. **The 240 s default timeout from PR [2a/4] was the correct call** — comfortably above the worst observed case (198 s) without capping pathological runs. No entry timed out.

## What this run does NOT measure

- **Diagnostic accuracy.** The fixture is a healthy Yamaha bike with no real faults. We measure descriptive accuracy ("does the agent correctly characterise *what the data shows*"), not diagnostic accuracy ("did the agent identify the fault correctly"). The latter needs a labelled-fault corpus that doesn't exist yet.
- **Cross-vehicle generalisation.** One fixture, one bike. Multi-vehicle work is gated on additional fixtures.
- **Reliability over time.** Single run. Re-running 5× would tell us how much variance is in the score itself; useful for the prompt-iteration ticket.
- **Tool design ceiling vs local model ceiling.** PR [2a/4]'s `OBD_EVAL_AGENT_MODEL` env switch supports a ceiling run against `z-ai/glm-5.1` or `moonshotai/kimi-k2`; deferred to follow-up.

## Next steps (out of scope for this PR)

1. **Prompt iteration ticket** — tune `obd_agent_prompts.py` for the three real failure modes (compound-002 fabrication, adversarial-002 overstep, adversarial-003 structured-output discipline). Re-run. Document the delta.
2. **Threshold raise** to 0.75 in `tests/harness/evals/test_obd_agent_eval.py` (this PR keeps it at 0.6; the recommendation lives in this doc only).
3. **`adversarial-001` `must_contain` fix** — relax to `["misfire"]`. One-line golden JSONL change.
4. **Ceiling run** against `z-ai/glm-5.1` via `OBD_EVAL_AGENT_MODEL`. Compares tool-design ceiling vs local-model ceiling.
5. **Workshop expert review** of the 15 OBD goldens via `/goldens/obd` (already deployed). Once expert promotion happens via `promote_golden.py --lane=obd` (which this PR adds), the eval reader migration from `v1/yamaha_road_test.jsonl` to `v2/locked/yamaha_road_test.jsonl` becomes meaningful.
