# HARNESS-14 Phase 6 Baseline — Manual Agent vs RAG on Locked Goldens

**Status:** ✅ Run complete — first real agent-vs-RAG number
**Owner:** Li-Ta Hsu
**Run date:** 2026-06-20
**Issue:** HARNESS-23 (#107)

## Reproducibility header

| Field | Value |
|---|---|
| Goldens | `tests/harness/evals/golden/v2/locked/mws150a.jsonl` — **30** expert-locked entries (6 each × `lookup`, `procedural`, `cross-section`, `image-required`, `adversarial`) |
| Manual agent model | `qwen3.5:27b-q8_0` (local Ollama, 2× RTX 6000 Ada) |
| Manual agent config | 3 tools (`list_manuals`, `get_manual_toc`, `read_manual_section`); max 8 iterations; 120 s wall timeout |
| RAG engine | pgvector cosine retrieval, **exact (sequential) scan**, `top_k=5`, `vehicle_model=TRICITY155` |
| RAG embedding | `nomic-embed-text` (768-dim, Ollama) |
| Judge | `z-ai/glm-5.1` via OpenRouter, temperature 0, JSON-only |
| Branch | `harness-23-baseline-eval` |
| Commit (manual lane) | `639e70d` |
| Commit (RAG lane) | `90bdbb6` (after the embedding-client fix — see §"Eval-side reconciliations") |
| Combined report | [`docs/eval-reports/phase6_baseline_eval.json`](eval-reports/phase6_baseline_eval.json) (60 grades; see its `_provenance`) |
| Aggregator | [`docs/eval-reports/aggregate_phase6.py`](eval-reports/aggregate_phase6.py) |

> This doc supersedes the pre-run plan that previously lived here (preserved in git history at `c89e3a5` / `47edf84`). It supersedes `docs/harness_14_phase5_baseline.md` (the deprecated v1-corpus baseline: 3/10, mean 0.534).

---

## Headline

| Lane | n | mean | median | stdev | min | max | pass@0.7 |
|---|---:|---:|---:|---:|---:|---:|---:|
| **manual_agent** | 30 | **0.590** | 0.580 | 0.163 | 0.350 | 0.958 | 6/30 (20%) |
| **rag** | 30 | **0.337** | 0.305 | 0.133 | 0.180 | 0.851 | 1/30 (3%) |

**The manual agent beats single-shot RAG by +0.25 mean overall (0.590 vs 0.337)** on the post-HARNESS-20 locked corpus. The agent wins every question-type category. This closes the loop on #74 (the original motivation for a publishable agent-vs-RAG comparison).

The `0.7` pass threshold is the inherited stub value; both lanes fall well below it because it was never calibrated to real `qwen3.5:27b` output. Re-pinned below.

---

## Per-question-type breakdown (`Grade.overall` mean)

| question_type | manual_agent | rag | delta (agent − rag) |
|---|---:|---:|---:|
| lookup | 0.728 | 0.382 | **+0.347** |
| image-required | 0.562 | 0.271 | **+0.292** |
| procedural | 0.563 | 0.292 | **+0.272** |
| cross-section | 0.504 | 0.255 | **+0.249** |
| adversarial | 0.593 | 0.485 | +0.108 |

- **Agent dominates fact-lookup** (+0.35): when it finds and reads the right section it synthesises the spec correctly (e.g. `lookup-006` 0.912: front brake pad 5.8 mm / 1.0 mm / DOT 4; `image-002` 0.958: cable-routing diagram, 11 components).
- **Agent leads image and procedural by ~+0.28** — but mostly because RAG collapses there, not because the agent is strong (see failure attribution).
- **Adversarial is nearly a tie** (+0.11): both lanes are supposed to *decline* (the question contains a false premise, e.g. "chain adjustment spec" for a CVT scooter). Neither lane fabricates, so they converge on the rubric's structural floor.

## Per-dimension means

| dimension | manual_agent | rag | note |
|---|---:|---:|---|
| section_recall | 0.644 | 0.233 | agent navigates to expected sections; RAG's 0.23 is mostly the adversarial vacuous-1.0 (empty `expected_recall_slugs`) |
| claim_precision | 0.740 | 0.011 | RAG cites the slugs it retrieved, which almost never match expected |
| exploration_cost (raw) | 0.753 | 0.000 | enters `overall` as `(1−cost)`; RAG is 0 by construction → free 0.05 |
| fact_recall | 0.336 | 0.048 | fraction of `must_contain` present in the output |
| fact_density | 0.323 | 0.048 | — |
| hallucination_penalty | 0.970 | 0.710 | agent rarely fabricates (honest agent); RAG's raw chunks trip pitfall directives |
| citation_quality | 0.427 | 0.323 | — |
| value_accuracy | 1.000 | 1.000 | neutral floor for the manual corpus (no numeric semantics); adds 0.10 to **both** lanes |
| answer_quality | 0.277 | 0.045 | the single biggest separator — RAG has **no synthesis step**, so it scores near 0 |
| trajectory_efficiency (reported only) | 0.363 | 1.000 | not in `overall`; agent uses 5–8 calls vs goldens' 2-call expectation |

**Where RAG's 0.337 actually comes from:** roughly 0.15 of it is the structural floor every RAG row gets for free — `value_accuracy` 1.0 (×0.10) + `exploration_cost` 0 → `(1−0)` (×0.05). The rest is mostly `hallucination_penalty`. RAG's *answer* dimensions (`fact_recall` 0.05, `answer_quality` 0.05) are near zero. **Single-shot top-5 concatenation is not a usable answer on this rubric** — it returns chunks, not answers.

---

## Comparison findings

1. **Agent > RAG everywhere; the gap is the synthesis step.** The agent reads sections and writes an answer (`answer_quality` 0.28); RAG concatenates 5 chunks and stops (`answer_quality` 0.05). On a rubric that rewards answering the question, the no-LLM RAG lane structurally cannot compete — it is a *retrieval floor*, not a *system*.
2. **RAG can win when retrieval lands.** `lookup-005` (tyre-pressure spec) scored **0.851** for RAG — the `車體規格` (body-spec) chunk was the top hit and carried every fact. This is RAG's ceiling on this corpus: when the answer is a single table that embeds close to the query, top-5 is enough. It happened once in 30.
3. **RAG retrieval is weak on the translated-Chinese corpus.** `section_recall` is 0.00 on 24/30 RAG entries. Generic chunks (`註` "note", DTC-index tables, image captions) dominate the top-5 for most queries — they embed close to everything. Cross-language (English question → Chinese-translated manual) makes it worse.
4. **The agent's wins are gated by its iteration budget, not its capability.** 11/30 agent runs `complete`d; those carry the high scores. The other **19/30 hit `timeout` (13) or `max_iterations` (6)** and returned *"The agent did not produce a final answer within the budget."* — `fact_recall`=0, `answer_quality`=0 even when `section_recall`=1.0 (it found the section but ran out of budget before answering). Lifting the budget is the highest-leverage agent fix.

---

## Failure attribution (3-bucket model, per `harness_14_phase5_baseline.md`)

### Bucket 1 — Agent error / inefficiency (dominant, fixable)
- **Budget exhaustion: 19/30 manual runs timed out or hit max-iterations.** All 6 cross-section, 5/6 image, 4/6 procedural, all 6 adversarial. The 8-iteration / 120 s budget is too tight for multi-part questions (e.g. `cross-005`: "bleed sequence AND pad-wear limit") and for image-OCR navigation. Fix: raise `max_iterations` (8→12) and the wall timeout; consider a "you are running low on budget, answer now" nudge.
- **Adversarial timeouts.** The agent correctly refuses to fabricate (`hallucination_penalty` ~1.0) but keeps searching for the non-existent spec until it times out, instead of emitting a clean "Not found:" decline → `answer_quality` 0. Half agent (doesn't give up), half judge (can't credit a non-answer).

### Bucket 2 — Judge / metric artifact (under-counts the agent)
- **Citation-slug opacity.** Chinese headings produce non-semantic slugs that vary by navigation path. The agent gives the *correct* facts but cites a parent/adjacent slug, so `citation_quality`=0.30 and `section_recall`=0.00 despite `fact_recall`=1.0 and `answer_quality`=0.90 (e.g. `lookup-002`, `procedural-006`). The deterministic slug-exact-match metrics systematically penalise correct answers. Carried over from phase 5; still unfixed. Fix: slug-tolerant matching (accept a citation whose section text contains a golden quote).
- **Correct declines score 0 on `answer_quality`.** The judge has no rubric path to reward "correctly said the question is based on a false premise" when the agent produced no structured answer.

### Bucket 3 — Golden / corpus issue (must reconcile before next baseline)
- **Identifier drift (root cause of the whole eval being non-trivial).** The goldens reference **`MWS-150-A`**; the corpus was relabelled to manufacturer `Yamaha` / model **`TRICITY155`** by APP-59/APP-60 after the goldens were locked. This broke both lanes (see §"Eval-side reconciliations"). The permanent fix is to reconcile the manual's stored identity with the locked goldens (or add `MWS-150-A` as an alias), not to keep patching the eval.
- **Adversarial `expected_recall_slugs` is empty** → `section_recall` is vacuously 1.0 for both lanes, inflating adversarial overall by ~+0.20 of floor. Both lanes get it equally, so the *comparison* is unaffected, but the absolute adversarial numbers read high.
- **`must_contain` requires CJK exact substrings** (e.g. `右前`, `左前`) that a correct English-or-mixed answer may paraphrase, under-counting `fact_recall`.

---

## Pinned `_PASS_THRESHOLD` recommendation

Rule-of-thumb from the ticket: `mean − 1·stdev`, floored to one decimal. Per lane (the comparison shows a structural gap, so they differ):

| Lane | mean − 1·stdev | **pinned** |
|---|---:|:---:|
| manual_agent | 0.590 − 0.163 = 0.427 | **0.4** |
| rag | 0.337 − 0.133 = 0.204 | **0.2** |

Applied to `test_manual_agent_eval.py` (0.4) and `test_rag_eval.py` (0.2). These are regression floors, not quality targets — they catch a lane falling off a cliff without flapping on per-entry judge noise. The RAG 0.2 floor honestly reflects that a no-synthesis lane sits just above the rubric's structural floor; revisit if RAG ever grows a synthesis step.

---

## Eval-side reconciliations (production untouched)

The issue's "verified-ready 2026-05-24" setup had drifted by run time (2026-06-20) because a second manual (`Corolla E11`, Toyota, 3051 chunks) was ingested into the shared corpus. Three eval-only fixes were required to produce a meaningful number; **none touch production code or data**:

1. **RAG `vehicle_model` filter `MWS150-A` → `TRICITY155`** — the goldens' manual's current DB label. The old value matched 0 rows. (`639e70d`)
2. **RAG exact (sequential) scan instead of HNSW** — with two manuals sharing the HNSW index, the post-filter starves a single-manual filter to **0 rows** (HNSW selects approximate-NN first, then filters; the larger English Corolla manual crowds out the Chinese Yamaha one — 0 rows even at the pgvector 0.7.4 max `ef_search=1000`). Exact scan makes the filter faithful; scores are identical cosine similarities. (`639e70d`) — *Production `retrieve_context` is unchanged; note that production OBD diagnose calls it with **no** `vehicle_model` filter (`obd_analysis.py:1420`), so it instead retrieves cross-manual — a separate latent bug worth its own ticket.*
3. **Manual-agent manuals mount reconciled to the goldens' identifier** — the honest agent (APP-59/HARNESS-25) correctly refuses when the manual's vehicle doesn't match the question, and `list_manuals` showed the manual as `TRICITY155`, not the goldens' `MWS-150-A` → it refused 27/30. The eval mounts a single-manual copy whose frontmatter label is reconciled to `MWS-150-A`, restoring the issue's *intended* single-manual baseline. (eval mount only)
4. **Embedding client per-call (not singleton)** — the first combined run silently retrieved 0 chunks on 15/30 RAG entries (alternating pattern): the production `embedding_service` reuses one `httpx.AsyncClient`, which breaks under pytest-asyncio's per-test event loops. The RAG lane now embeds with a fresh per-call client and was re-run; the manual lane does not embed and was unaffected. (`90bdbb6`)

Because of #4, the combined report is assembled from two runs (manual lane from the first run, RAG lane from the post-fix re-run — same goldens, same judge). See the report's `_provenance`.

---

## Out of scope (follow-ups)

- **Reconcile corpus identity with the locked goldens** (`MWS-150-A` ↔ `TRICITY155`) so the eval needs no manuals-mount patch. *(highest priority follow-up)*
- **Raise the agent iteration/timeout budget** and re-baseline — expected to lift the agent mean materially given 19/30 budget-exhaustion failures.
- **Slug-tolerant citation/section matching** in the judge rubric (kills the Bucket-2 false negatives).
- **Fix production `retrieve_context` filtered-HNSW recall** (or pass a `vehicle_model` filter in production OBD diagnose) — separate ticket.
- Cross-manual scope, RAG `top_k` / synthesis-step experiments, CI integration — all deferred.

## Related

- HARNESS-20 (#90) — produced the locked corpus measured here
- HARNESS-14 (#73) — built the eval suite
- #74 — original agent-vs-RAG motivation (this baseline answers it)
- `docs/harness_14_phase5_baseline.md` — superseded v1-corpus baseline
