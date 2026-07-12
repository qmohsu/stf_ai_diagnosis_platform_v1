# HARNESS-14 Phase 6 Baseline v2 — Manual Agent vs RAG on Locked Goldens

**Status:** ✅ v2 re-baseline complete (#155) — the trustworthy capability number
**Owner:** Li-Ta Hsu
**Run date:** 2026-07-12 (v1: 2026-06-20)
**Issue:** HARNESS-23 (#107); re-baseline ticket #155

> ## ⚠️ v1 and v2 are NOT comparable
>
> Between the two runs, the **rubric itself changed**: #153 removed the
> structural-floor weight terms (`exploration_cost` demoted to
> reported-only, `value_accuracy` halved, freed weight moved to the
> recall dims), #148 made adversarial `section_recall` N/A-with-
> renormalisation instead of vacuously 1.0, #149 made `fact_recall`
> bilingual-tolerant, #146/#147 changed how the judge scores declines
> and pitfall violations, and #150 realigned `expected_tool_trace`.
> A v1 grade and a v2 grade of the *same* transcript would differ.
> Do NOT diff the two headline tables; the v1 report
> ([`phase6_baseline_eval.json`](eval-reports/phase6_baseline_eval.json))
> is retained only as the labelled "v1, confounded" reference.
> The v1 version of this document is preserved in git history
> (commit `084deb3` and earlier).

## Reproducibility header

| Field | Value |
|---|---|
| Goldens | `tests/harness/evals/golden/v2/locked/mws150a.jsonl` — **30** expert-locked entries (6 each × `lookup`, `procedural`, `cross-section`, `image-required`, `adversarial`), `expected_tool_trace` realigned by #150 |
| Manual agent model | `qwen3.5:27b-q8_0` (local Ollama, 2× RTX 6000 Ada) |
| Manual agent config | 3 tools; max **12** iterations; **240 s** wall timeout (T1/#143); forced-synthesis backstop at 3 reads (T2/#165); tool-budget prompt (T3/#152) |
| RAG engine | pgvector cosine retrieval, **exact (sequential) scan**, `top_k=5`, `vehicle_model=TRICITY155` |
| RAG embedding | `nomic-embed-text` (768-dim, Ollama) |
| Judge | `z-ai/glm-5.1` via OpenRouter, temperature 0, JSON-only; ANSWERABILITY-aware (#146), assertion/omission-split directives (#147) |
| Rubric weights | 2026-07-12 vector (#153): recall-weighted, no structural-floor terms; adversarial `section_recall` N/A + renormalise (#148) |
| Branch / commit | `main` @ `084deb3` (all phase-1/2 fixes merged) |
| Manuals | **real** `infra_diagnostic_api_manuals` volume (APP-61 `factory_code` backfill — no frontmatter patch needed) |
| Combined report | [`docs/eval-reports/phase6_rebaseline_v2_eval.json`](eval-reports/phase6_rebaseline_v2_eval.json) (60 grades, one run) |
| Aggregator | [`docs/eval-reports/aggregate_phase6.py`](eval-reports/aggregate_phase6.py) |
| Wall time | 64 min 42 s for both lanes (v1: ~73 min with 19 timeouts) |

---

## Headline (v2)

| Lane | n | mean | median | stdev | min | max | pass@0.7 |
|---|---:|---:|---:|---:|---:|---:|---:|
| **manual_agent** | 30 | **0.670** | 0.693 | 0.176 | 0.338 | 0.917 | 15/30 (50%) |
| **rag** | 30 | **0.239** | 0.215 | 0.122 | 0.170 | 0.851 | 1/30 (3%) |

**The manual agent beats single-shot RAG by +0.43 mean overall (0.670 vs
0.239)** on the locked corpus, under a rubric with the free structural
credit removed. The agent wins every question-type category by
+0.35–0.51.

**The #155 sanity check passes:** the agent mean rose 0.590 → 0.670
*despite* #153/#148 lowering both lanes' floors (which alone would have
pushed it down — #153's projection was ~0.577 on the v1 transcripts).
The rise is real capability recovery: budget exhaustion is gone.

**Headline operational fact: 0/30 budget-exhaustion failures** (v1:
19/30 — 13 `timeout` + 6 `max_iterations`). Every run finished
`stopped_reason=complete`, 5–8 iterations, mean wall latency **66 s**
(max 110 s) against the 240 s budget. Mean tool calls 5.77 (v1: 6.17);
`trajectory_efficiency` 0.850 vs v1's 0.363 (also lifted by #150's
realistic expected counts).

---

## Per-question-type breakdown (`Grade.overall` mean)

| question_type | manual_agent | rag | delta (agent − rag) |
|---|---:|---:|---:|
| lookup | 0.805 | 0.298 | **+0.507** |
| image-required | 0.660 | 0.203 | **+0.457** |
| adversarial | 0.711 | 0.270 | **+0.441** |
| cross-section | 0.608 | 0.201 | **+0.408** |
| procedural | 0.567 | 0.220 | **+0.346** |

- **Lookup is now strong (0.805)** — T4's slug-tolerant matching stopped
  correct answers scoring 0 on citation dims, and the agent reliably
  finds + synthesises single-fact specs.
- **Adversarial transformed (agent 0.711).** All 6 runs now *complete
  with an explicit decline* (v1: all 6 timed out at `answer_quality=0`).
  The T2 forced-synthesis backstop produces the decline; the T5
  ANSWERABILITY rubric credits it (`answer_quality` 0.75–0.95 across
  the six). This is the clearest end-to-end validation of the
  phase-1 stack.
- **Procedural is the weakest agent category (0.567)** — multi-step
  answers still omit steps (see failure attribution).

## Per-dimension means

| dimension | manual_agent | rag | note |
|---|---:|---:|---|
| section_recall | 0.632 | 0.042 | adversarial entries are N/A (#148) and excluded from the mean; RAG's near-0 is genuine retrieval failure, no longer masked by the vacuous 1.0 |
| claim_precision | 0.294 | 0.011 | agent cites more sections than the goldens list as expected; candidate for a future look, not a blocker |
| exploration_cost (raw) | 0.483 | 0.000 | **reported-only since #153** (weight 0) |
| fact_recall | 0.678 | 0.072 | v1: 0.336/0.048 — roughly doubled by completions + #149's bilingual matching |
| fact_density | 0.673 | 0.072 | — |
| hallucination_penalty | 0.940 | 0.820 | assertion-only counting (#147); RAG rose from 0.71 (omissions no longer mislabelled as hallucination) |
| citation_quality | 0.733 | 0.323 | v1: 0.427 — slug-tolerant matching (#145) |
| value_accuracy | 1.000 | 1.000 | neutral for the manual corpus; weight halved to 0.05 (#153) |
| answer_quality | 0.545 | 0.057 | v1: 0.277/0.045 — completions + decline crediting; still the biggest agent-vs-RAG separator |
| trajectory_efficiency (reported only) | 0.850 | 1.000 | v1: 0.363 — realistic expected counts (#150) + fewer wasted calls (T3) |

**Where RAG's 0.239 comes from now:** with the free credit gone
(#153/#148 removed ~0.15 of structural floor), RAG's score is mostly
`hallucination_penalty` (0.82 × 0.15) + `value_accuracy` (1.0 × 0.05) +
partial `citation_quality`. Its answer dimensions remain near zero
(`fact_recall` 0.07, `answer_quality` 0.06). The conclusion is
unchanged but now honestly priced: **single-shot top-5 concatenation
is not a usable answer system on this rubric** — see #159 (RAG
synthesis step) if a stronger comparison lane is ever wanted.

---

## Failure attribution (v2)

The v1 3-bucket attribution is resolved as follows:

- **Bucket 1 (budget exhaustion, was 19/30):** eliminated. T1 budget +
  T2 forced synthesis + #144 thinking-suppression → 0 timeouts,
  mean latency 66 s.
- **Bucket 2 (metric/judge under-counts):** fixed by #145 (slugs),
  #146 (declines), #147 (omission split), #149 (bilingual facts),
  #148 (adversarial floor).
- **Bucket 3 (golden/corpus artifacts):** fixed by #150 (tool traces),
  #151 (stable manual_id gate), APP-61 (factory_code backfill — the
  eval now mounts the real manuals volume unpatched).

**What remains is genuine capability work (new Bucket 4).** The 4
below-floor agent entries (overall < 0.4, all `complete`, no timeouts):

| entry | overall | failure mode |
|---|---:|---|
| dtc-001 | 0.338 | wrong diagnosis: read the wrong subsection and identified P0117 as an IAT-sensor issue; missed the coolant-temp sensor facts |
| procedural-002 | 0.340 | premature decline: claimed the flowchart/wiring detail wasn't available instead of reading the right section |
| procedural-005 | 0.365 | incomplete synthesis: omitted the ABS-cycle step and torque spec from a long procedure |
| image-005 | 0.382 | incomplete synthesis: missed the boot-precondition step and the image-position fact |

Pattern: **navigation lands near the right content but synthesis drops
required steps/facts on long multi-step sections**, and one case reads
the wrong subsection confidently. These are model/prompt-quality
issues (candidate follow-ups: section-completeness nudge in the
synthesis prompt; larger model comparison), not eval artifacts.

---

## Pinned `_PASS_THRESHOLD` (v2)

Rule: `mean − 1·stdev`, floored to one decimal, per lane:

| Lane | mean − 1·stdev | **pinned** | v1 pin |
|---|---:|:---:|:---:|
| manual_agent | 0.670 − 0.176 = 0.494 | **0.4** | 0.4 (unchanged) |
| rag | 0.239 − 0.122 = 0.117 | **0.1** | 0.2 (lowered) |

Applied in `test_manual_agent_eval.py` / `test_rag_eval.py`. The RAG
floor drop reflects the removed structural free credit (#153/#148),
not a capability regression. These are per-entry regression floors —
under a mean−1σ rule a tail of entries sits below it by construction
(currently the 4 agent entries above; 0 RAG entries at 0.1).

---

## Eval-side reconciliations still in force (production untouched)

1. **RAG `vehicle_model=TRICITY155` + exact (sequential) scan** — the
   filtered-HNSW starvation (#156) and the production no-filter
   retrieval (#157) remain open production tickets; the eval lane
   keeps its faithful exact-scan workaround.
2. **Manuals volume:** the real `infra_diagnostic_api_manuals` volume
   is now used directly — APP-61's `factory_code` backfill made the v1
   frontmatter patch unnecessary.
3. **Embedding client per-call** in the RAG lane (pytest-asyncio event
   -loop safety) — unchanged from v1 (#160 documents it).

Unlike v1, the v2 report comes from **one clean combined run** (no
lane stitching).

## Out of scope / next

- **#154** — script this run end-to-end (the by-hand invocation works
  but is gotcha-dense).
- **#156/#157** — production retrieval bugs surfaced by this eval.
- **#158/#159** — RAG-lane research (retrieval quality, synthesis step).
- **New capability follow-up** — procedural/synthesis completeness
  (the 4 below-floor entries above).

## Related

- HARNESS-20 (#90) — locked corpus; HARNESS-14 (#73) — eval suite;
  #74 — original agent-vs-RAG motivation
- #107 — v1 baseline + follow-up backlog; #155 — this re-baseline
- `docs/harness_14_phase6_followups.md` — the T1–T19 backlog this run
  gates
