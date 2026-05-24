# HARNESS-14 Phase 6 Baseline — Manual Agent vs RAG on Locked Goldens

**Status:** Planned (no run executed yet)
**Owner:** Li-Ta Hsu
**Date:** 2026-05-24
**Goldens:** `tests/harness/evals/golden/v2/locked/mws150a.jsonl` — 30 expert-approved entries (5★ accept from Towngas workshop expert; promoted via `scripts/promote_golden.py` in HARNESS-20 phase 2, PR #104)
**Judge:** `z-ai/glm-5.1` via OpenRouter (HK-accessible per #23)
**Agent under test:** local `qwen3.5:27b-q8_0` (production model)
**Comparison system:** pgvector RAG retrieval (`top_k=5`, `vehicle_model="MWS150-A"`)

---

## What this doc is for

This is a **pre-run baseline plan**, not a results writeup. It captures everything needed to execute the first agent-vs-RAG comparative eval on the post-HARNESS-20 corpus, plus the gotchas discovered during plumbing validation. The actual results doc lands as `harness_14_phase6_baseline_results.md` after the run.

The work split:

| Phase | Action | Status |
|---|---|---|
| 1 (PR #102) | Two-tier corpus + promote script | ✅ MERGED |
| 2 (PR #104) | Retro-lock 30 expert-approved candidates | ✅ MERGED |
| 3 (this PR) | RAG eval lane + this baseline plan + procedural gotchas | 🔧 IN PROGRESS |
| First baseline run | Execute the procedure below + capture results | ⏸ DEFERRED (user paused) |
| Threshold pin + results doc | Lower `_PASS_THRESHOLD` per real data; ship `phase6_baseline_results.md` | ⏸ DEFERRED |

---

## Procedure (ready to run)

### Pre-run checks

```bash
# 1. Confirm server is at the post-phase-2 commit
ssh polyu-gpu "cd ~/stf_ai_diagnosis_platform_v1 && git log --oneline -1"
# Expect: a commit whose tree contains golden/v2/locked/mws150a.jsonl with 30 lines

# 2. Confirm 30 lines in the locked file
ssh polyu-gpu "podman exec stf-diagnostic-api wc -l \
  /app/tests/harness/evals/golden/v2/locked/mws150a.jsonl"
# Expect: 30

# 3. Confirm Ollama serves qwen3.5:27b-q8_0
ssh polyu-gpu "curl -s http://127.0.0.1:11434/api/tags | grep qwen3.5:27b"
# Expect: a model entry

# 4. Confirm OpenRouter key + GLM-5.1 reachable
ssh polyu-gpu "grep PREMIUM_LLM_API_KEY ~/stf_ai_diagnosis_platform_v1/infra/.env | wc -l"
# Expect: 1
```

### Run command

```bash
# Mount tests/ + scripts/ INTO a one-shot container built from the
# diagnostic-api image (the image itself ships only golden/v2/, not
# the test files — see Dockerfile line 61 comment).
#
# CRITICAL: mount tests/ RW (not :ro) — the eval_report fixture
# writes reports/eval_{timestamp}.json at teardown.  Read-only
# mount errors with "Read-only file system" right before exit
# and you lose the report.
ssh polyu-gpu "podman run --rm \
  -v ~/stf_ai_diagnosis_platform_v1/diagnostic_api/tests:/app/tests \
  -v ~/stf_ai_diagnosis_platform_v1/diagnostic_api/scripts:/app/scripts:ro \
  -e PYTHONPATH=/app \
  --env-file ~/stf_ai_diagnosis_platform_v1/infra/.env \
  --network host \
  localhost/stf-diagnostic-api:0.1.0 \
  pytest --run-eval \
    /app/tests/harness/evals/test_manual_agent_eval.py \
    /app/tests/harness/evals/test_rag_eval.py \
    --tb=short 2>&1 | tee /tmp/eval_phase6_baseline.log"
```

### Capture artifacts

```bash
# Pull the report JSON back from the server for analysis
ssh polyu-gpu "ls -t ~/stf_ai_diagnosis_platform_v1/diagnostic_api/tests/harness/evals/reports/*.json | head -1"
# → /home/talon/.../reports/eval_{ts}.json

scp polyu-gpu:~/stf_ai_diagnosis_platform_v1/diagnostic_api/tests/harness/evals/reports/eval_{ts}.json \
  ./docs/eval-reports/phase6_baseline_eval.json
```

---

## Expected cost + time

| Lane | Per-entry latency | Per-entry cost | 30-entry total |
|---|---|---|---|
| Manual agent | 60-180s (1-8 tool iterations × Ollama local) | ~$0.005 (judge only) | 30-90 min, ~$0.15 |
| RAG | <2s (pgvector + judge) | ~$0.005 (judge only) | <2 min, ~$0.15 |
| **Combined** | — | — | **~30-90 min, ~$0.30** |

Cost dominated by the judge (GLM-5.1 at ~$1.5/1M tokens, ~3K context + ~500 output per call). Agent inference is free (local Ollama, GPU-bound). Latency dominated by the manual agent's multi-iteration loop.

---

## Pre-run decisions (already locked in this PR)

| Decision | Choice | Why |
|---|---|---|
| Eval scope | Both `manual_agent` AND `rag` lanes | The publishable artifact (#74) is the comparison. Running only one gets half the story. |
| Hard-fail on threshold | Yes — keep at `_PASS_THRESHOLD = 0.7` for the first run | The eval_report fixture writes the JSON BEFORE the asserts run, so all 30 grades are captured even when individual asserts fail. Exit code is non-zero; we look at the JSON. Pin a realistic threshold in a follow-up after seeing real data. |
| Pass threshold revision | Deferred to results PR | Need real numbers to justify the pin. |
| RAG top-k | 5 (production default) | Matches what `/v2/obd/.../diagnose` requests. Larger k (`10`, `20`) for recall@k curves is a separate ticket. |
| RAG vehicle filter | `MWS150-A` | Only manual currently ingested. Cross-manual scope expands when a second manual lands. |
| Shared `eval_report` | Yes — one combined report covers both lanes | Both test files write into the same session-scoped fixture; a single pytest invocation produces one JSON with grades for both systems against the same 30 entries. Enables direct comparison per entry. |

---

## Gotchas discovered during plumbing validation (2026-05-24)

These bit me during the smoke test; documenting so the real run doesn't re-stumble:

1. **Test files are NOT in the container image.** `diagnostic_api/Dockerfile` line 61 comment confirms this is deliberate (test code shouldn't ship to production). The mount-tests-in command above is the workaround. Don't try to `podman exec stf-diagnostic-api pytest ...` — it'll error with "no tests collected".

2. **Reports dir needs RW mount.** Mounted `:ro` errors at teardown with `OSError: [Errno 30] Read-only file system: '.../reports/eval_*.json'`. The grades from EVERY entry are lost (the fixture writes once at the end). Mount as RW.

3. **Mock plumbing doesn't validate real grading.** `--mock-agent --mock-judge` confirms the pipeline runs but produces nonsense scores (mock agent has no real citations → fact_recall=0, section_recall=0). Useful for "does pytest collect + run + report?" but not for "is the agent good?".

4. **Container restart re-runs `golden_sync`.** If you ever edit `golden/v2/*.jsonl` while the API is up, `podman restart stf-diagnostic-api` to re-mirror into the DB. Locked tier is read by the eval harness directly from the filesystem (no restart needed for eval changes).

5. **Pre-existing DB schema bug (HARNESS-20 phase 1).** `golden_sync` walks both tiers but the primary-key-on-id-only schema means the candidate row overwrites the locked row in the DB. Net: `golden_entries.tier` always reads `'candidate'` for the 30 entries. **Does NOT affect this eval** because the eval reads JSONL from the filesystem, not from the DB. Flagged as a separate follow-up.

6. **Stale `_PASS_THRESHOLD = 0.7`.** Hardcoded for stub-perfect plumbing tests. Real Qwen3.5:27b numbers will likely fall in 0.4-0.6. Keeping it at 0.7 for the baseline run is INTENTIONAL — see the table above for the rationale (JSON gets captured anyway).

---

## What to do after the run

1. **Inspect the JSON report** — focus on per-entry `Grade.overall` distribution, per-`question_type` (`lookup`, `procedural`, `cross-section`, `image-required`, `adversarial`) breakdown, and the deterministic-metric subscores (`section_recall`, `fact_recall`, `claim_precision`).

2. **Categorise failures into agent error / judge error / golden bug** — the 3-bucket attribution model from HARNESS-14 phase 5 (`docs/harness_14_phase5_baseline.md`). If a category has >2 "judge error" or "golden bug" entries, fix those before re-baselining — otherwise the threshold pin will encode the bug.

3. **Pick the threshold** — typical rule-of-thumb: `mean(overall) - 1·stdev(overall)` rounded down to one decimal. Gives a floor that catches regressions without being noisy. Pin in both `test_manual_agent_eval.py:_PASS_THRESHOLD` and `test_rag_eval.py:_PASS_THRESHOLD`. They CAN be different per lane if the comparison shows a structural gap.

4. **Write `docs/harness_14_phase6_baseline_results.md`** — header (date, models, commit SHA), per-lane summary table, per-category breakdown, comparison findings ("agent beats RAG by +N on procedural; RAG matches agent on lookup"), known limitations, recommendations.

5. **Open the results PR** with the threshold change + the results doc. Reference the captured JSON in `docs/eval-reports/` (or whichever path the team picks for eval artifacts).

---

## Related

- HARNESS-14 / #73: original eval suite design
- HARNESS-20 / #90: two-tier corpus + lock-in
- #74: agent-vs-RAG comparative benchmark (the downstream consumer this baseline feeds)
- HARNESS-21 / #97: parallel OBD eval lane (separate fixture, same judge)
- `docs/harness_14_phase5_baseline.md`: the v1-corpus baseline this supersedes
