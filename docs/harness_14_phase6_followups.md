# HARNESS-14 Phase 6 — First-round eval follow-ups (next-round plan)

**Date:** 2026-06-21
**Source:** the first real agent-vs-RAG baseline — `docs/harness_14_phase6_baseline.md` + the captured report `docs/eval-reports/phase6_baseline_eval.json` (60 grades).
**Origin issue:** HARNESS-23 (#107).

This is the actionable backlog derived **only from confirmed first-round findings**. Each item is tracked as a GitHub issue (numbers below), labelled `eval-followup` + a `phase:*` label.

---

## Start here — orientation for a fresh session working any T-ticket

> If you opened this from a ticket link, you have enough context to do the task once you've read this section + the ticket. Read **`CLAUDE.md` first** (non-negotiable repo workflow: branch before the first commit; a PR for every change; the PolyU deploy runbook; the pre-commit doc gate that blocks substantive commits unless `docs/dev_plan.md` + `docs/design_doc.md` are staged or the message contains `[docs-ok]`; doc routing V1 `APP-XX` vs V2 `HARNESS-XX`).

### Where the eval lives (all paths under `diagnostic_api/`)
- **Goldens (source of truth, locked):** `tests/harness/evals/golden/v2/locked/mws150a.jsonl` — 30 entries, 6 each × `lookup` / `procedural` / `cross-section` / `image-required` / `adversarial`. The locked tier is append-only; changes go through `scripts/promote_golden.py` (re-promotion), not hand-edits.
- **Runners:** `tests/harness/evals/runner.py` (`run_manual_agent_unified`) and `rag_runner.py` (`run_rag`, with the exact-scan path `_exact_vector_retrieve` + `_embed_query`).
- **Judge:** `tests/harness/evals/judge.py` (model `z-ai/glm-5.1` via OpenRouter; reads `settings.premium_llm_api_key` / `premium_llm_base_url`), prompts in `judge_prompts.py`.
- **Deterministic metrics:** `tests/harness/evals/metrics.py` (manual dims + `DEFAULT_OVERALL_WEIGHTS`) and `metrics_obd.py`.
- **Schemas:** `tests/harness/evals/schemas.py` (`GoldenEntry`, `SystemRunResult`, `Grade` — the 9 weighted dims).
- **Test entrypoints:** `test_manual_agent_eval.py`, `test_rag_eval.py` (parametrised over the 30 goldens; `_PASS_THRESHOLD` pinned 0.4 / 0.2).
- **Agent under test:** `app/harness_agents/manual_agent.py` (+ `manual_agent_prompts.py`); navigation tools in `app/harness_tools/manual_tools.py`.
- **First-round artifacts:** result writeup `docs/harness_14_phase6_baseline.md`; raw 60-grade report `docs/eval-reports/phase6_baseline_eval.json`; aggregator `docs/eval-reports/aggregate_phase6.py`.

### Re-derive the first-round numbers (offline, instant)
```
python docs/eval-reports/aggregate_phase6.py docs/eval-reports/phase6_baseline_eval.json
```
This prints the per-lane, per-question_type, per-dimension, and per-entry tables every ticket's "evidence" refers to.

### Fast feedback (offline, no server) — use this for most Phase-1/2 tickets
Metric / judge / golden / prompt / agent-config changes are validated by **offline unit tests** — you do **not** need the full eval per change:
```
cd diagnostic_api
python -m pytest tests/harness/evals/test_metrics.py tests/harness/evals/test_judge.py -q   # metric/judge logic
python -m pytest tests/harness/test_manual_tools.py -q                                       # manual tools/prompts
```
Add unit tests alongside your change. **Run the full eval only at the Phase-3 re-baseline gate (#155)** — not after each individual fix.

### Running the FULL eval (server, ~73 min; ~2× after T1) — the correct invocation
Runs on the PolyU GPU server (`ssh polyu-gpu`) against local Ollama + pgvector + the GLM judge. The originally-documented command was wrong three ways; use this:
1. Deploy your branch to the server per the `CLAUDE.md` "Post-Implementation Verification" loop (checkout branch → rebuild `diagnostic-api` → `alembic upgrade head` if there's a migration).
2. Generate the env from the **running container** (NOT `infra/.env` — the app reads `DB_*`/`LLM_*`, which compose maps from differently-named keys):
   ```
   ssh polyu-gpu "podman exec stf-diagnostic-api env | grep -E '^(DB_|LLM_|EMBEDDING_|VISION_|PREMIUM_LLM_|MANUAL_|JWT_|STRICT_MODE|LOG_|OBD_LOG_|AUDIO_|ALLOW_EXTERNAL)' > /tmp/eval.env"
   ```
3. Run **both lanes in one invocation** (shared report); mount `tests/` **RW** + the **real** manuals volume (APP-61 backfilled `factory_code`, so the real volume now works — the old `/tmp` frontmatter patch is no longer needed):
   ```
   ssh polyu-gpu "podman run --rm \
     -v ~/stf_ai_diagnosis_platform_v1/diagnostic_api/tests:/app/tests \
     -v ~/stf_ai_diagnosis_platform_v1/diagnostic_api/scripts:/app/scripts:ro \
     -v infra_diagnostic_api_manuals:/app/data/manuals:ro \
     --env-file /tmp/eval.env -e PYTHONPATH=/app -e LOG_FILE=/tmp/diag.log \
     --network host localhost/stf-diagnostic-api:0.1.0 \
     pytest --run-eval -p no:cacheprovider \
       /app/tests/harness/evals/test_manual_agent_eval.py \
       /app/tests/harness/evals/test_rag_eval.py --tb=short 2>&1 | tee /tmp/eval_run.log"
   ```
   A **non-zero exit is expected** (entries below threshold); the report is written at teardown regardless. It is long-running — launch detached and poll for the report file.
4. Pull + aggregate:
   ```
   scp polyu-gpu:~/stf_ai_diagnosis_platform_v1/diagnostic_api/tests/harness/evals/reports/eval_<ts>.json docs/eval-reports/<name>.json
   python docs/eval-reports/aggregate_phase6.py docs/eval-reports/<name>.json
   ```

### Gotchas that will bite a cold session
- Test files are **not** in the image → must mount `tests/`. `reports/` must be **RW** or the JSON is lost at teardown.
- `--env-file infra/.env` does **not** work (wrong key names) — use the running-container env (step 2).
- **tiktoken**: importing `app.harness`/`app.main` downloads `cl100k_base` at import → fails **offline**. On the server (online) it is fine. For offline unit tests, stick to modules that don't pull the harness (the eval metric/judge/manual_tools tests are offline-safe).
- After a server `down`+`up`, the local LLM is evicted — warm it (`CLAUDE.md` deploy step 10) before timing anything.
- **Restore the server to `main` when done** (`CLAUDE.md` step 5).

#### Embedding-client / event-loop trap (T17, #160 — resolved, eval-side only)

The first combined run silently retrieved **0 chunks on 15/30 RAG entries in an alternating pattern**. Root cause: the production `embedding_service` (`app/rag/embedding.py`) reuses one module-level `httpx.AsyncClient` for connection pooling. Under pytest-asyncio each test runs in its **own event loop**, so the pooled client stays bound to whichever loop first created it; once that loop closes, calls from later tests' loops die inside `get_embedding()`'s broad exception handler and retrieval silently returns `[]`. The alternating pattern is the tell-tale — whether a test's loop matched the client's birth loop flip-flopped across the suite.

**Fix location: the eval adapter only.** `tests/harness/evals/rag_runner.py::_embed_query` embeds with a **fresh per-call `httpx.AsyncClient`** created in the current loop, sidestepping cross-loop reuse. Do-not list:

- Do **not** swap the eval adapter back to the production singleton "for consistency" — that reintroduces the 15/30 silent zeroing.
- Do **not** "fix forward" `app/rag/embedding.py` — production runs a single long-lived event loop, where the pooled singleton is correct and faster. There is nothing to fix in production.
- If a future eval report shows alternating zero-retrievals again, suspect a shared async client (some new code path reusing a loop-bound client) before suspecting the corpus.

Full details live in the docstring on `_embed_query` and the guard comment above the singleton in `app/rag/embedding.py`.

### One golden rule for this backlog
Phase-1/2 tickets change how scores are computed or what the agent can do. **Do not re-run the full eval (or re-pin thresholds) per ticket.** Land the offline-verified change, then the **single** clean re-baseline at #155 produces the comparable number. The first-round `phase6_baseline_eval.json` stays the labelled "v1, confounded" reference.

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
| T15 | [#159](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/159) | RAG lane has no synthesis step — **resolved: decision, no build (see "T15 decision" below)** | answer_quality 0.05 | M |
| T17 | [#160](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/160) | Document the eval-only embedding-client workaround | 15/30 zeroed on first run | S |
| T19 | [#161](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/161) | Decide eval CI / cadence | ~73 min, opt-in | S |

### T15 decision — RAG synthesis (#159, resolved 2026-07-12: no synthesis step; lane stays the retrieval floor)

**Decision.** The eval RAG lane (`tests/harness/evals/rag_runner.py`) stays
**deliberately synthesis-free**, and no production RAG synthesis path is
built. RAG-as-a-product is **not** a Phase 1 surface. T15 is closed as a
documented decision with zero code change.

**Rationale (three independent reasons, any one sufficient):**

1. **Comparison semantics.** The lane exists as the *retrieval floor* for
   the agent-vs-RAG comparison — "what does top-5 retrieval alone buy you?"
   Its low `answer_quality` (0.045 first round, **0.057** at the v2
   re-baseline #155) is the honest price of no synthesis, not a bug.
   Adding an LLM over the chunks would turn the base of the comparison
   into a second one-shot-LLM lane and silently redefine every
   agent-vs-RAG delta, including the pinned thresholds (0.4 / 0.1) and the
   v2 headline (agent 0.670 vs RAG 0.239). The ticket's own scope guard
   says the same: the no-synthesis lane must stay the baseline.
2. **No product surface behind it.** Nothing user-facing serves raw
   retrieved chunks. The product surfaces — the one-shot diagnose
   endpoints and the V2 agent (`/diagnose/agent`) — already synthesise
   over retrieval. There is no user experiencing the 0.057; there is
   nothing to fix in production.
3. **Cost without a consumer.** A synthesis pass adds one LLM call per
   eval entry (30 more calls per full run on an already ~73-min,
   GPU-bound eval) plus a prompt to maintain and judge-calibrate — spent
   on a lane no user sees.

**Future path (recorded, NOT built): a third `rag_synth` lane.** If a
synthesised-RAG comparison arm is ever wanted (e.g. as a paper baseline:
"retrieval + one-shot LLM, no agency"), it is added as a **third, additive
lane** — `rag_synth` — never by modifying the floor lane. Sketch: reuse the
existing premium/OpenRouter client to run one synthesis call over the same
top-5 chunks `run_rag` already retrieves, emit a normal `SystemRunResult`,
and grade it with the identical judge + metrics so all three lanes stay
directly comparable. That is a new ticket when (if) the paper needs it.

---

## Recommended sequence

1. **Phase 1** — T1, T4, T5, T6 (+ T2 with T5). Biggest movement, cheapest; fixes the dominant agent failure and the metric under-counts.
2. **Phase 2** — one golden re-promotion pass (T8, T9, T10, T11), then T3, T7.
3. **Phase 3** — T16 → **T18** (clean re-run + re-pin).
4. **Parallel track** — T12 → T13 (production bugs); backlog T14, T15, T17, T19.

## Important caveat
After Phase 1–2, the re-baselined number (T18) **will not be comparable** to the first-round 0.590 / 0.337 — that is expected and correct: the first number is a confounded floor; the second is the real capability read. Keep `phase6_baseline_eval.json` as the labelled "v1, confounded" reference.

## Already fixed (do not re-open)
- **T7** — structural-floor terms decision — **#153**: **rebalanced** (not just documented). `exploration_cost` demoted to reported-only (weight 0.05 → **0.00** — an agent-only efficiency dim that handed RAG a free 0.05 via `(1 - cost)` and, at agent mean cost 0.753, actually taxed the agent; same treatment as `trajectory_efficiency`), `value_accuracy` halved 0.10 → **0.05** (stays weighted so fabricated OBD numbers cost; the manual lane's neutral 1.0 was free credit in both lanes — first-round mean 1.000/1.000). The freed 0.10 went back to `section_recall` 0.20 → **0.25** and `fact_recall` 0.15 → **0.20** (reversing the 2026-05-17 trims that funded `value_accuracy`). Zero-content floor drops ~0.40 → ~0.30; the residual (vacuous `claim_precision` 0.10 + saturated `hallucination_penalty` 0.15 + neutral `value_accuracy` 0.05) is documented in the `metrics.py` weight-table comment and left for the #155 re-baseline. Both lanes' absolute scores shift down (projected on first-round data: agent 0.590 → ~0.577, RAG 0.337 → ~0.251) — expected; #155 re-pins thresholds. Pinned by `TestStructuralFloorRebalance` in `test_metrics.py`.
- **T1** — raise the agent budget — **#143** (merged): `manual_agent.py` `_DEFAULT_MAX_ITERATIONS` 8 → 12 and `_DEFAULT_TIMEOUT` 120 → 240 s (`_DEFAULT_MAX_TOKENS` reviewed, left at 12288 — no run hit the per-call cap). Fixes the 19/30 budget-exhaustion failures (13 wall-timeouts at 5-7 iters + 6 iter-cap hits). Config-only; defaults pinned by `TestManualAgentConfigDefaults`. **Full-eval wall-time roughly doubles** as a result — manual-lane runs that were cut off now run to completion (already reflected in the "~2× after T1" note in the "Running the FULL eval" section above).
- Identifier drift MWS-150-A ↔ TRICITY155 — **APP-61** (#141/#142, merged): the manual now carries a `factory_code` alias; the agent matches a question by either name (verified on the server).
- Eval embedding-client zeroing 15/30 — fixed in the eval adapter (`rag_runner._embed_query`). **T17 (#160) resolved**: the workaround is now documented in the "Embedding-client / event-loop trap" gotcha above plus code comments on `_embed_query` and the `app/rag/embedding.py` singleton (doc-only; production untouched by design).
