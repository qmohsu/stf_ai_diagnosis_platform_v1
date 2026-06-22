# HARNESS-14 Phase 6 — First-round eval follow-ups (next-round plan)

**Date:** 2026-06-21
**Source:** the first real agent-vs-RAG baseline — `docs/harness_14_phase6_baseline.md` + the captured report `docs/eval-reports/phase6_baseline_eval.json` (60 grades).
**Origin issue:** HARNESS-23 (#107).

This is the actionable backlog derived **only from confirmed first-round findings**. Each item is tracked as a GitHub issue (numbers below), labelled `eval-followup` + a `phase:*` label.

---

## First-round result (the thing we are fixing against)

| Lane | mean | median | stdev | pass@0.7 |
|---|---:|---:|---:|---:|
| manual_agent | 0.590 | 0.580 | 0.163 | 6/30 |
| rag | 0.337 | 0.305 | 0.133 | 1/30 |

Agent beats single-shot RAG by +0.25 and wins every question-type. **But the 0.590 is a floor, not a true capability read** — a large share of failures are metric/golden artifacts and a structurally tight agent budget, not agent quality. The next round removes those confounds, then re-baselines.

### Audited / corrected since the first writeup
Two suspected problems were checked against the report data and **dropped**:
- **Judge noise (GLM-5.1 misfires)** — NOT reproduced. Every sub-1.0 `hallucination_penalty` call was evidence-consistent. *However*, the audit found a real, different rubric bug → **T6**.
- **GPU-load timeout variance** — REFUTED. Per-iteration cost is a stable ~10–24 s/iter across complete / timeout / max-iter runs (no outliers); timeouts are structural. Sharpened **T1** instead (both the wall **and** the iter cap bind, on different entries).

---

## The backlog

Legend — Effort: S/M/L. "Evidence" is the first-round datum that justifies the item.

### Phase 1 — cheap, high-impact; unblocks a real measurement

| ID | Issue | Item | Evidence | Where | Effort |
|---|---|---|---|---|---|
| T1 | [#143](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/143) | Raise iteration cap 8→12 **and** wall timeout 120→240 s | 19/30 budget fails: 13 hit the 120 s wall (5–7 iters), 6 hit the 8-iter cap | `manual_agent.py` config | S |
| T2 | [#144](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/144) | Graceful early "Not found:" decline | all 6 adversarial timed out searching for absent info; answer_quality 0 | `manual_agent_prompts.py` + stop condition | S–M |
| T4 | [#145](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/145) | Slug-tolerant citation/section matching | correct answers scored citation 0.30 / section_recall 0.00 despite fact_recall 1.0 (e.g. lookup-002) | `metrics.py` | M |
| T5 | [#146](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/146) | Credit a correct decline | adversarial correct refusals score answer_quality 0 | `judge.py` / `judge_prompts.py` | M |
| T6 | [#147](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/147) | Split "must-not-omit" out of `hallucination_penalty` | omission directives counted as hallucinations; double-penalizes RAG (0.71) | `judge_prompts.py` + penalty calc | M |

### Phase 2 — golden cleanup (one re-promotion pass) + lower-priority agent/metric

| ID | Issue | Item | Evidence | Where | Effort |
|---|---|---|---|---|---|
| T8 | [#148](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/148) | Fix adversarial `expected_recall_slugs` (empty → vacuous 1.0) | adversarial section_recall 1.0 both lanes | goldens re-promote / `metrics.py` | S–M |
| T9 | [#149](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/149) | De-brittle CJK-exact `must_contain` | cross-005 fact_recall 0 despite finding section | `metrics.py` / goldens | M |
| T10 | [#150](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/150) | Realign `expected_tool_trace` counts | trajectory_efficiency 0.363 (expects ~2, real 4–6) | goldens re-promote | S |
| T11 | [#151](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/151) | Pin goldens to a stable `manual_id`, not prose | root cause of the MWS-150-A↔TRICITY155 drift | golden schema / `promote_golden.py` | S |
| T3 | [#152](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/152) | Reduce agent tool-call churn | 5–8 calls vs ~2 | `manual_agent_prompts.py` | S |
| T7 | [#153](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/153) | Decide on structural-floor terms | zero-content RAG still scores ~0.40 | `metrics.py` weights (decision) | S |

### Phase 3 — clean re-baseline (gated)

| ID | Issue | Item | Gate | Effort |
|---|---|---|---|---|
| T16 | [#154](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/154) | Codify the eval run (script the correct `podman` invocation) | — | S–M |
| T18 | [#155](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/155) | **Re-baseline + re-pin thresholds** | after Phase 1 + T8/T9/T10 | M |

### Separate track / backlog (not eval-blocking)

| ID | Issue | Item | Evidence | Effort |
|---|---|---|---|---|
| T12 | [#156](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/156) | **Prod bug:** filtered-HNSW recall starves to 0 rows with ≥2 manuals | proven: 0 rows even at ef_search=1000 | M–L |
| T13 | [#157](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/157) | **Prod bug:** OBD diagnose retrieves with no `vehicle_model` filter | Yamaha query returns Corolla content | S (after T12) |
| T14 | [#158](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/158) | Weak RAG retrieval on translated-Chinese corpus | section_recall ≈ 0 on 24/30 | L |
| T15 | [#159](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/159) | RAG lane has no synthesis step | answer_quality 0.05 | M |
| T17 | [#160](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/160) | Document the eval-only embedding-client workaround | 15/30 zeroed on first run | S |
| T19 | [#161](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/161) | Decide eval CI / cadence | ~73 min, opt-in | S |

---

## Recommended sequence

1. **Phase 1** — T1, T4, T5, T6 (+ T2 with T5). Biggest movement, cheapest; fixes the dominant agent failure and the metric under-counts.
2. **Phase 2** — one golden re-promotion pass (T8, T9, T10, T11), then T3, T7.
3. **Phase 3** — T16 → **T18** (clean re-run + re-pin).
4. **Parallel track** — T12 → T13 (production bugs); backlog T14, T15, T17, T19.

## Important caveat
After Phase 1–2, the re-baselined number (T18) **will not be comparable** to the first-round 0.590 / 0.337 — that is expected and correct: the first number is a confounded floor; the second is the real capability read. Keep `phase6_baseline_eval.json` as the labelled "v1, confounded" reference.

## Already fixed (do not re-open)
- Identifier drift MWS-150-A ↔ TRICITY155 — **APP-61** (#141/#142, merged): the manual now carries a `factory_code` alias; the agent matches a question by either name (verified on the server).
- Eval embedding-client zeroing 15/30 — fixed in the eval adapter (`rag_runner._embed_query`); see T17 for the doc-only note.
