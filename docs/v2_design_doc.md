# STF AI Diagnosis Platform — V2 Harness Architecture

**From Context Engineering to Harness Engineering: Agent-Driven Vehicle Diagnosis**

## Document control

| Field | Value |
|-------|-------|
| **Doc title** | V2 Harness Architecture for AI-Assisted Vehicle Diagnosis |
| **Project** | STF AI Diagnosis Platform — Phase 1 Pilot |
| **Status** | Draft v0.9 |
| **Owner** | Li-Ta Hsu |
| **Contributors** | ML engineers; backend engineers; frontend engineers |
| **Last updated** | 2026-06-25 (v1.8.4) |
| **Primary pilot stack** | FastAPI + AsyncOpenAI (OpenRouter) + Ollama + pgvector (PostgreSQL) + Next.js |
| **New in this revision** | #144 (manual-agent eval latency): the manual-agent **eval** now drives local Ollama via the **native `/api/chat` endpoint with `"think": false`** (new `OllamaNativeLLMClient` in `app/harness/deps.py`), not the OpenAI-compat `/v1` adapter. A server probe proved `/v1` (and the `/no_think` directive the eval driver used) cannot suppress qwen3's reasoning — so the agent ran at ~36 s/call and timed out adversarial goldens (`P9999`: `stopped_reason=timeout`, `answer_quality=0`) before it could navigate **and** synthesise within the 240 s wall. Native `think:false` drops it to ~14 s/call; server A/B: `adversarial-006` 0-timeout → **`complete`/0.73 in 123 s** (correct "P9999 not in table; manual documents P0106/P0117/…"), `adversarial-003` unchanged 0.79 but ~30 % faster. The client translates OpenAI↔Ollama-native message/tool-call shapes; multimodal tool content flattened to text (`qwen3.5:27b` is text-only). **Eval-only — production delegation runs on the shared OpenRouter client and was never affected.** Complements #165 (graceful-decline backstop); feeds the #155 re-baseline. Tests: `tests/harness/test_ollama_native_client.py`. See §12.5.<br><br>**Previous revision (v1.8.3):** HARNESS-23 T2 (GitHub issue #144): **graceful finalize on unanswerable manual questions** for the manual sub-agent. The #107 baseline showed all 6 adversarial runs ending `stopped_reason=timeout` at `answer_quality=0`. A branch-deploy server smoke refined the diagnosis: T1's 240 s budget (#143) already lets the simpler adversarial entries complete (e.g. `adversarial-003` finishes ~137 s, ~0.80 on both old and new), but `adversarial-006` (fake DTC `P9999`) **still timed out on both** — the live `qwen3.5:27b` model spins by reading **distinct** sections (6 of them) hunting the absent code, so a byte-identical repeat-detector never fires. Two agent-only layers (no metric/judge/golden change): (1) the prompt (`manual_agent_prompts.py`) gains a "When to decline early" section telling the agent to return the documented `{"summary": "Not found: …", "citations": []}` shape *immediately* once `list_manuals` shows no `vehicle=`/`factory_code=` match or the answer is absent; (2) a **forced-synthesis backstop** in `run_manual_agent` (`manual_agent.py`) counts cumulative `read_manual_section` calls — once it reaches `_MAX_SECTION_READS_BEFORE_FINAL` (3) or a byte-identical call recurs, the loop re-prompts the model **with the tools withheld (`tools=[]`)** plus `_FORCE_FINAL_INSTRUCTION` so it must answer-or-decline from gathered evidence, terminating at `stopped_reason="complete"` instead of the wall. A forced *synthesis* turn (not a canned refusal) lets the model give the corrective answer the goldens expect; forced-turn error/empty degrades to a canned `Not found:`. Trade-off for #155: capping at 3 reads may trim a genuine many-section answer — measured at the re-baseline. Pairs with T5 (#146); feeds #155. See §12.5. Tests: `TestRunManualAgentForcedSynthesis` + `TestForceNotFoundFinalize` in `tests/harness_agents/test_manual_agent.py`.<br><br>**Previous revision (v1.8.2):** HARNESS-26 (paired with V1 APP-60): **agent vehicle grounding**. HARNESS-25 stopped the agent confabulating a Yamaha scooter, but with only the bare VIN in its context it then reverse-reasoned the model from the only same-make manual (called the Hiace a "Corolla"). Now that APP-60 requires make/model at upload and stamps them into `parsed_summary`, `harness/harness_prompts.build_user_message` renders `Vehicle: {Manufacturer} {Model} (VIN {vehicle_id})` (new `_format_vehicle` helper) instead of the bare `vehicle_id`; it falls back to `vehicle_id` (or `unknown`) for historical sessions. This lets the HARNESS-25 match-or-refuse rule resolve the correct manual positively, or honestly say none matches. Prompt-only; no schema or SSE change. Tests in `tests/harness/test_harness_prompts.py` (7, offline).<br><br>**Previous revision (v1.8.1):** HARNESS-25 (GitHub issue #136, paired with V1 APP-59): **honest manual agent**. In the first real agent run on a Toyota Hiace (DTC P00AF, #135) the agent treated the only manual in the vault — the Yamaha MWS150-A scooter manual — as authoritative and concluded the vehicle was a Yamaha scooter with a spurious code. Two prompt/tool-output changes (no schema, no SSE change): (1) `list_manuals` renders each manual's canonical `vehicle="<Manufacturer> <Model>"` identity (from the `.md` frontmatter that APP-59 now stamps), filters leniently on manufacturer/model/canonical, and appends a footer telling the agent to treat a manual as authoritative only if its make/model matches the vehicle under diagnosis (else say "no service manual is available for this vehicle"); (2) a **Vehicle grounding (critical)** rule added to the main system prompt (`harness/harness_prompts.py`) and the manual sub-agent's process (`harness_agents/manual_agent_prompts.py`) — a standard SAE DTC + the session VIN outweigh manual content that contradicts the vehicle type. See the §`list_manuals` tool description below. Tests in `tests/harness/test_manual_tools.py` (canonical name, refusal footer, manufacturer filter).<br><br>**Previous revision (v1.8.0):** HARNESS-24 (GitHub issue #127): fixed the `400 provider mismatch` that made expert feedback on Agent AI diagnoses impossible. Root cause: the Agent AI tab's feedback form posted to `POST /v2/obd/{id}/feedback/ai_diagnosis` (which hard-requires `provider='local'`) with the agent generation's `diagnosis_history_id`. Fix (chosen option a — dedicated table, consistent with the 5 existing per-view feedback tables): new `OBDAgentDiagnosisFeedback` model + Alembic migration `e4f5a6b7c8d9` creating `obd_agent_diagnosis_feedback`; new `POST /v2/obd/{id}/feedback/agent_diagnosis` endpoint that validates `diagnosis_history_id` against `provider='agent'`; frontend `AgentDiagnosisView` feedback form rewired to it. Two related gaps fixed in the same PR: (1) the session History tab gained an **Agent Model** lane (`provider='agent'` generations were stored but invisible) — required widening the `/history` provider filter and the `DiagnosisHistoryItem.provider` / `FeedbackHistoryItem.tab_name` response Literals to include `agent` / `agent_diagnosis` (the latter also fixes a latent 500 when an agent row was serialised); (2) the **force-agent toggle** is now surfaced beside the Regenerate button so it stays visible/controllable after the result panel replaces the initial form. i18n keys added in 3 locales. Backend unit + integration tests added. No change to the agent loop or SSE protocol.<br><br>**Previous revision (v1.7.1):** HARNESS-23 (refactor, paired with V1 APP-58 / issue #128): the timer-based `_with_keepalive` SSE wrapper added for the agent path in HARNESS-22 is relocated to a shared helper beside `_sse_event` in `obd_analysis.py` so the V1 local + premium diagnose endpoints can reuse it; `harness/router.py` now imports it (its duplicate definition and now-unused `asyncio`/`AsyncIterator` imports removed) and additionally wraps the **Tier‑0 one-shot path** (local Ollama, same cold-load risk) that was previously unwrapped. No behavioural change to the agent stream; no migration. The user-visible #128 fix — first post-deploy diagnosis dying on a silent Ollama cold-load — is documented in the V1 design doc (APP-58).<br><br>**Previous revision (v1.7.0):** HARNESS-22: live reasoning streaming for the Agent AI diagnosis. The agent loop previously made a **blocking, non-streaming** `chat()` call per ReAct iteration and emitted nothing until it returned, so with `qwen3.5:27b` thinking-mode the UI sat on a multi-minute frozen spinner. New `LLMClient.chat_stream()` (on `OpenAILLMClient`) streams `reasoning` (qwen3 thinking channel) + `content` deltas and accumulates tool-call fragments by index into the same terminal `LLMResponse`; `loop._stream_llm_turn` surfaces them as live `reasoning`/`token` `HarnessEvent`s and **falls back to blocking `chat()`** if streaming fails. New `_with_keepalive` SSE wrapper injects `: ping` comments during any >15s gap, closing the Cloudflare-tunnel idle-timeout risk that the APP-41 keep-alive had only ever covered for the one-shot path. Reasoning is **ephemeral** (streamed, not persisted) — `harness_event_log` and History replay are unchanged, no migration. Frontend: new collapsible `ReasoningPanel`, per-iteration reasoning that clears at each tool-call boundary, final answer types out live; `&check;`→`✓` glyph fix. Design doc: `docs/plans/2026-06-09-agent-reasoning-streaming-design.md`.<br><br>**Previous revision (v1.6.0):** Removed `search_manual` from the main-agent tool registry. Registry shrinks 12 → 11 tools; manual primitives 4 → 3. The function still exists in `harness_tools/rag_tools.py` but is no longer registered anywhere in the agent pipeline — no RAG in the end-to-end diagnosis flow. `lookup_dtc` next-step guidance updated to `get_manual_toc` → `read_manual_section` navigation instead of the former `search_manual` pivot. 15 files updated (production: `tool_registry.py`, `harness_prompts.py`, `delegation_tools.py`, `obd_dtcs.py`, `context.py`; 10 test files updated). No Alembic migration needed.<br><br>**Previous revision (v1.5.3):** HARNESS-20 schema fix (post-phase-2 follow-up to GitHub Issue #90): `GoldenEntry.tier` (string column added in phase 1) is replaced by `is_locked` (boolean) via Alembic migration `a1b2c3d4e5f6`, and `golden_sync` is rewritten as two passes — candidate-content upsert in pass 1, locked-flag overlay UPDATE in pass 2.  Root cause of the phase-1 bug: both tiers share entry ids by design (the locked file is a verbatim copy of the candidate line — that's how `promote_golden.py` works), but `GoldenEntry.id` is the sole primary key, so the single recursive walk did two upserts on the same id and the second overwrote the first.  Post-phase-2 deploy verification showed all 30 rows ended up with `tier='candidate'` regardless of actual lock state.  The fix reframes the column: each row holds the candidate's mutable content (so the dashboard always reflects the latest edit), and `is_locked` is just the badge meaning "this id is also in the locked file".  Locked-tier *content* stays on the filesystem and is read by the eval harness directly — the DB no longer tries to mirror it.  Two-pass sync surfaces a `locked_orphans` counter for the data-integrity case where a locked id has no matching candidate (non-fatal; logged per-id).  12 unit tests cover the new helpers (`_iter_candidate_jsonl_files`, `_iter_locked_jsonl_files`, `_apply_locked_overlay`).  See v1.5.1 below for the prior phase-2 entry that this corrects.<br><br>**Original v1.5.1 entry (HARNESS-20 phase 2):** 30 expert-approved candidates retro-locked into `golden/v2/locked/mws150a.jsonl`.  Server-side enumeration confirmed all 30 entries had a 5★ `accept` review from the Towngas workshop expert (reviewer UUID `b34ac0f0-...`).  Batch promoted via a run-once driver using `--force` + a new `--expert-review-id` kwarg on `promote_golden.py` that stamps the qualifying review id into the audit row even when the live DB lookup is skipped (typical when running locally against a server-side review history).  `locked/PROMOTIONS.md` now has 30 attributable rows; the eval harness `test_manual_agent_eval.py` collects 30 parametrised cases (was 1 skipped placeholder under phase 1's empty-tier safety net).  2 new unit tests cover the override kwarg.  Outstanding before the first real eval run: lower `_PASS_THRESHOLD` from the stub-perfect 0.7, run both `manual_agent` AND `rag` lanes for the #74 comparison, commit a phase-6 baseline doc.  See v1.5 below for the underlying two-tier mechanism.<br><br>**Original v1.5 entry (HARNESS-20 phase 1):** two-tier golden corpus with promote-by-script lock-in. The v2 corpus splits into a **candidate** tier (`tests/harness/evals/golden/v2/*.jsonl`, mutable, dashboard-graded) and an **append-only locked** tier (`tests/harness/evals/golden/v2/locked/*.jsonl`) that is the only source the eval harness reads. New `GoldenEntry.tier` column (Alembic `z0a1b2c3d4e5`, default `'candidate'`, CHECK constraint) propagated through `golden_sync.py` (recursive walk under `v2/`, path-based tier detection) and surfaced via `GoldenEntrySummary.tier` / `GoldenEntryDetail.tier` for a future dashboard lock badge. New `scripts/promote_golden.py` is the one-way bridge: enforces a review-quality gate (latest expert review must be `status='accept'` with `star_rating >= 4`), appends the candidate's raw JSONL line verbatim into the locked file, computes SHA-256 of the canonical-serialised payload, and writes one row to `locked/PROMOTIONS.md` recording timestamp, hash, reviewer, expert review id, and reason. `--force` bypasses the gate and is itself recorded. `tests/harness/evals/test_manual_agent_eval.py` now loads from `v2/locked/mws150a.jsonl`; the shipped locked file is empty so the eval suite collects zero parametrised cases until the first promotion — the deliberate safety net that prevents publishing any agent-vs-RAG number until an expert-approved entry exists. 24 new unit tests (`tests/scripts/test_promote_golden.py` × 17, `tests/test_golden_sync.py` × 7). README rewritten with the two-tier policy. Design rationale: Option A (two-tier files) chosen over Option B (in-place `frozen` flag + content hash) and Option C (immutable revisions) — the dashboard already treats the candidate file as canonical-source-on-disk, two files cost near-zero operationally, and "edit a locked entry needs a new id" falls out of file layout rather than requiring schema changes. |

### Revision history

| Version | Date | Summary |
|---------|------|---------|
| v1.8.4 | 2026-06-25 | #144 (manual-agent eval latency): eval now uses `OllamaNativeLLMClient` (native `/api/chat`, `think=false`) instead of the `/v1` adapter, because `/v1` + `/no_think` cannot suppress qwen3 reasoning (~36 s/call → ~14 s/call). Fixes adversarial timeouts: `P9999` 0-timeout → `complete`/0.73 (123 s); `adversarial-003` 0.79, ~30 % faster. Eval-only (production delegation on OpenRouter unaffected). Complements #165; feeds #155. Tests: `test_ollama_native_client.py`. |
| v1.8.3 | 2026-06-25 | HARNESS-23 T2 (GitHub issue #144): graceful finalize on unanswerable manual questions. Prompt gains a "When to decline early" section; a forced-synthesis backstop in `run_manual_agent` counts `read_manual_section` calls and, once `_MAX_SECTION_READS_BEFORE_FINAL` (3) is reached (or a byte-identical call recurs), re-prompts with the tools withheld (`tools=[]`) so the model must answer-or-decline from gathered evidence — terminating at `stopped_reason="complete"` instead of the 240 s wall. A branch-deploy server smoke drove the design: T1's budget already completes simpler adversarials, but `P9999` still timed out because the live model spins on *distinct* sections (not byte-identical repeats), which a repeat-detector misses. Forced *synthesis* (not canned refusal) so the corrective answer the goldens want survives; error/empty falls back to a canned decline. Agent-only; pairs with T5 (#146); feeds #155. Tests: `TestRunManualAgentForcedSynthesis` + `TestForceNotFoundFinalize` in `test_manual_agent.py`. |
| v1.8.2 | 2026-06-20 | HARNESS-26 (paired with V1 APP-60): agent vehicle grounding. `build_user_message` renders `Vehicle: {Make} {Model} (VIN …)` (new `_format_vehicle`) from the make/model APP-60 stamps into `parsed_summary`, so the HARNESS-25 match-or-refuse rule resolves the correct manual positively instead of reverse-reasoning the model from the only same-make manual. Falls back to the bare `vehicle_id` for legacy sessions. Prompt-only; tests in `test_harness_prompts.py`. |
| v1.8.1 | 2026-06-19 | HARNESS-25 (GitHub issue #136, paired with V1 APP-59): honest manual agent. `list_manuals` renders the canonical `vehicle="<Manufacturer> <Model>"` (from `.md` frontmatter), filters leniently on make/model, and appends a match-or-refuse footer; a "Vehicle grounding (critical)" rule added to the main system prompt + manual sub-agent prompt so the agent refuses to treat a non-matching manual as authoritative (fixes the P00AF Hiace → "Yamaha scooter" mis-grounding, #135). Prompt/tool-output only; no schema or SSE change. Tests in `test_manual_tools.py`. |
| v1.8.0 | 2026-06-14 | HARNESS-24 (GitHub issue #127): fix `400 provider mismatch` blocking expert feedback on Agent AI diagnoses. Dedicated `OBDAgentDiagnosisFeedback` table + Alembic migration `e4f5a6b7c8d9`; new `POST /v2/obd/{id}/feedback/agent_diagnosis` endpoint validating `provider='agent'`; `AgentDiagnosisView` feedback form rewired from `ai_diagnosis` → `agent_diagnosis`. Related gaps: session History tab gains an **Agent Model** lane (`/history` provider filter + `DiagnosisHistoryItem.provider` / `FeedbackHistoryItem.tab_name` Literals widened to `agent` / `agent_diagnosis`, also closing a latent 500 on agent-row serialisation); force-agent toggle surfaced beside Regenerate so it persists past the initial form. i18n in 3 locales. Backend unit + integration tests added; no agent-loop or SSE change. |
| v1.7.1 | 2026-06-14 | HARNESS-23 (refactor, paired with V1 APP-58 / GitHub issue #128): relocated the timer-based `_with_keepalive` SSE wrapper from `harness/router.py` to a shared helper beside `_sse_event` in `obd_analysis.py`, so the V1 local + premium diagnose endpoints reuse the same implementation. `harness/router.py` now imports it (duplicate definition + unused `asyncio`/`AsyncIterator` imports removed) and additionally wraps the Tier‑0 one-shot path (local Ollama, same post-deploy cold-load risk) that was previously unwrapped. No behavioural change to the agent stream; no Alembic migration; no frontend change. 6 new tests in V1 `tests/test_sse_keepalive.py` exercise the shared helper from its new home. |
| v1.7.0 | 2026-06-09 | HARNESS-22: live reasoning streaming for the Agent AI diagnosis (GitHub Issue #119 follow-up). Agent loop converted from a blocking per-iteration `chat()` to a streaming `chat_stream()` that surfaces qwen3 thinking + answer tokens live over SSE while reassembling tool-call deltas into the same `LLMResponse`; graceful fallback to blocking `chat()` on any streaming error. `_with_keepalive` SSE wrapper injects `: ping` during >15s gaps (closes the tunnel-timeout risk for the agent path). Reasoning is ephemeral (not persisted; no migration). Frontend `ReasoningPanel` (collapsible, per-iteration, auto-scroll); final answer types out live; `&check;`→`✓`. `EventType` += `reasoning`, `token`. New `tests/harness/test_loop_streaming.py`. Design doc: `docs/plans/2026-06-09-agent-reasoning-streaming-design.md`. |
| v1.6.0 | 2026-05-24 | Removed `search_manual` from main-agent registry — registry 12 → 11 tools, manual primitives 4 → 3. No RAG anywhere in the agent pipeline. `lookup_dtc` pivot guidance updated to `get_manual_toc` → `read_manual_section` navigation. 15 files updated (5 production, 10 tests). No Alembic migration. |
| v1.5.3 | 2026-05-24 | HARNESS-21 PR series rescoped to **4 PRs** (was 3) after a follow-up discussion of bucket distribution and UI surface. Decisions: (1) v1 OBD golden distribution rebalanced from 3/2/2/2/2/1 (12) → **2/2/2/3/3/3 (15)** — more slots for `dtc_decode`, `compound_obd`, `adversarial_obd` where failure modes actually live; (2) OBD goldens get a parallel team-review UI at `/goldens/obd` (separate route from `/goldens/manual`), backed by a `?lane=obd` query param on `/v2/goldens` and a `lane` column on `GoldenReview`; (3) **Tier strategy: Path C (hybrid)** — PR [2a] authors at `golden/v1/yamaha_road_test.jsonl` (eval reader unchanged from PR [1/3]), PR [2b] migrates the OBD eval reader to `golden/v2/locked/yamaha_road_test.jsonl` AND seeds the v2 candidate file from v1 so the first OBD promotions happen through the UI's expert-review workflow rather than author self-promotion (keeps `PROMOTIONS.md` audit trail honest). New PR breakdown: **[1/3]** ✅ merged · **[2a/4]** real goldens + eval-side fixes (in progress) · **[2b/4]** UI lane + v1→v2-tier migration · **[3/4]** baseline scorecard + prompt iteration. No code change in this revision — design-doc only. Design doc: `docs/plans/2026-05-17-harness-21-obd-eval-design.md`. |
| v1.5.2 | 2026-05-24 | HARNESS-20 schema fix (post-phase-2 follow-up to GitHub Issue #90).  Replaces `GoldenEntry.tier` (string column from phase 1) with `is_locked` (boolean) via Alembic migration `a1b2c3d4e5f6`.  Rewrites `golden_sync` as two passes: pass 1 walks `v2/*.jsonl` non-recursively and upserts content with `is_locked=False`; pass 2 walks `v2/locked/*.jsonl` and issues a batched UPDATE that flips `is_locked=True` on every matching id.  Content is never overwritten by the locked pass — the DB always reflects the latest candidate content (so dashboard edits show up), and locked-tier content stays on disk for the eval harness to read directly.  Surfaces a `locked_orphans` counter for the data-integrity case where a locked id has no candidate row (non-fatal; logged per-id).  API responses rename `tier` → `is_locked` on `GoldenEntrySummary` / `GoldenEntryDetail`.  12 unit tests cover the new helpers (3 for `_extract_entry_fields` invariants, 3 for `_iter_candidate_jsonl_files`, 3 for `_iter_locked_jsonl_files`, 3 for `_apply_locked_overlay`).  Migration downgrade is deliberately lossy (re-adds `tier='candidate'` for every row — same wrong state the buggy column reached on its own).  Verifiable post-deploy: re-run the same drift-check pattern from phase 2 and confirm `golden_entries.is_locked=True` for all 30 rows. |
| v1.5.1 | 2026-05-24 | HARNESS-20 phase 2 (GitHub Issue #90): retro-lock of 30 expert-approved candidates.  Server-side enumeration confirmed all 30 v2 candidates carry a 5★ `accept` review from one workshop expert (Towngas).  Batch driver promoted them in one pass via a new `expert_review_id_override` kwarg on `promote_entry` (CLI: `--expert-review-id`) — lets `--force` runs stamp the qualifying review id into `PROMOTIONS.md` even without a live DB lookup, preserving auditability for offline / cross-environment batch flows.  Result: `golden/v2/locked/mws150a.jsonl` is now 30 lines, `PROMOTIONS.md` has 30 attributable rows, eval harness collects 30 parametrised cases (was 1 skipped placeholder).  2 new unit tests cover the override (`test_expert_review_id_override_stamps_audit_row`, `test_expert_review_id_override_wins_over_db_lookup`).  No other code change; mechanism unchanged from v1.5.  Next: first baseline eval run (phase 3 — out of scope for this PR pending decisions on pass-threshold pinning and lane scope). |
| v1.5 | 2026-05-24 | HARNESS-20 (GitHub Issue #90): two-tier golden corpus with promote-by-script lock-in. Candidate tier `golden/v2/*.jsonl` (mutable, dashboard-graded) ↔ locked tier `golden/v2/locked/*.jsonl` (append-only, eval-canonical). New `GoldenEntry.tier` column (Alembic `z0a1b2c3d4e5`, CHECK `tier IN ('candidate', 'locked')`, default `'candidate'`). `golden_sync.py` walks `v2/` recursively, classifies each `*.jsonl` by path parts (`"locked" in path.parts` → locked tier), excludes `candidates/` subdir. API surfaces `tier` on `GoldenEntrySummary` and `GoldenEntryDetail` (UI consumption deferred). New `scripts/promote_golden.py` enforces review-quality gate (latest expert review must be `status='accept'` with `star_rating >= 4`), refuses re-promote of an already-locked id, appends raw JSONL line verbatim, computes SHA-256 of canonical `sort_keys=True` payload, writes audit row to `locked/PROMOTIONS.md` (timestamp / entry_id / hash / reviewer / expert_review_id / reason). `--force` bypasses the gate and is recorded. `--dry-run` validates without writing. `tests/harness/evals/test_manual_agent_eval.py` now loads `v2/locked/mws150a.jsonl`; shipped file is empty (zero parametrised cases until first promotion — safety net). 24 new unit tests pass. Retro-lock of currently-graded candidates, UI promotion button, and content-hash consistency checker deferred to follow-up phases of HARNESS-20. |
| v1.4 | 2026-05-17 | HARNESS-21 PR [1/3]: OBD sub-agent evaluation framework scaffolding (GitHub Issue #97). Parallels HARNESS-14. Additive schemas (OBD-side `ExpectedSignalCitation`, `ExpectedDTC`, `expected_signal_citations`, `expected_dtcs`, `expected_no_evidence`, `obd_signal_citations`, `obd_dtc_citations`; widened `GoldenQuestionType` with `signal_statistics`/`event_finding`/`dtc_enumeration`/`dtc_decode`/`compound_obd`/`adversarial_obd`). New `metrics_obd.py` with four OBD-native dims (`signal_recall`, `signal_precision`, `value_accuracy`, `dtc_accuracy`) and the `expected_no_evidence` polarity flip for adversarial entries. Lane dispatcher in `metrics.py` routes by `question_type` membership in `OBD_QUESTION_TYPES`. New `obd_runner.py` adapter (output_text serialises summary + structured citations + limitations; `OBD_EVAL_AGENT_MODEL` env switches between Ollama and OpenRouter for ceiling runs). `Grade.value_accuracy` added (default 1.0 — manual lane unaffected); `DEFAULT_OVERALL_WEIGHTS` rebalanced to nine dims summing to 1.0. New `test_obd_agent_eval.py` parametrized over `golden/v1/yamaha_road_test.jsonl` (3 dummy entries; real ones in PR [2/3]).  New developer aid `scripts/compute_yamaha_reference.py` prints per-signal stats for golden authoring. Manual lane unchanged; 91 new tests; full plumbing run green via `pytest -m eval --run-eval --mock-agent --mock-judge`. Design doc: `docs/plans/2026-05-17-harness-21-obd-eval-design.md`. |
| v1.3 | 2026-05-16 | HARNESS-19: agent-native OBD investigation toolset (GitHub Issue #85). Replaces `read_obd_data` with 6 decomposed primitives + OBD sub-agent + 2 delegation wrappers (hybrid Pattern 2). New files: `app/harness_tools/{obd_loader, obd_signal_inventory, obd_signals, obd_dtcs, delegation_tools}.py`, `app/harness_agents/{obd_agent, obd_agent_prompts, result_formatters}.py`. New types in `app/harness_agents/types.py`: `SignalCitation`, `DTCCitation`, `DataExcerpt`, `OBDAgentResult`. Main-agent registry grew 5 → 12 tools. Yamaha-aware raw loader surfaces `A_YAM_*` proprietary columns; Yamaha-hex DTCs handled with honest "no decoder + `search_manual` pivot". 136 new unit tests; 223 total harness tests pass with no regressions. Design doc: `docs/plans/2026-05-16-obd-toolset-design.md`. |
| v0.1 | 2026-04-10 | Initial draft. Defines harness architecture for agent-driven diagnosis. 5-component model (Tools, Session, Harness, Sandbox, Orchestration). 7 diagnostic tools (4 wrapped from V1 + 3 new). Graduated autonomy with 4 tiers. New API endpoint, SSE event types, HarnessEventLog table. GitHub Issue #26. |
| v0.2 | 2026-04-10 | HARNESS-02: Core agent loop implemented. `run_diagnosis_loop()` async generator with ReAct cycle, `HarnessDeps` DI, `LLMClient` protocol + `OpenAILLMClient` adapter, `HarnessConfig`, dynamic system prompt assembly. 19 unit tests. GitHub Issue #52. |
| v0.3 | 2026-04-10 | HARNESS-03: Session event log. `HarnessEventLog` SQLAlchemy model + Alembic migration `p9q0`. `session_log.py` with `emit_event()`/`get_session_events()` (async, thread-pooled). Agent loop emits `session_start`, `tool_call`, `tool_result`, `diagnosis_done`, `error` events. `DiagnosisHistory.provider` CHECK now accepts `"agent"`. `EventType` Literal extended with new event types. 9 unit tests. GitHub Issue #53. |
| v0.4 | 2026-04-10 | HARNESS-04: Context management. `context.py` with token estimator (`estimate_tokens`, char/4), tool-result truncation (`truncate_tool_result`), and auto-compaction (`maybe_compact`). `HarnessConfig.max_tool_result_tokens` (default 2000). Agent loop integrates Tier 1 (truncation per result) and Tier 2 (compaction between iterations). Emits `context_compact` event. 28 unit tests. GitHub Issue #54. |
| v0.5 | 2026-04-10 | HARNESS-06: Graduated autonomy router. `autonomy.py` with `classify_complexity()` (deterministic Tier 0–3), `apply_overrides()` for `force_agent`/`force_oneshot` query params. `AutonomyDecision` dataclass. Helpers: `_count_dtcs()` (regex DTC extraction), `_max_severity()` (keyword-based severity grading), `_count_clues()` (tag/separator counting). Integrated into `router.py` — queries `DiagnosisHistory` for Tier 3 (follow-up), sets `max_iterations` from `suggested_max_iterations`, `done` SSE event includes `autonomy_tier` and `autonomy_strategy`. `force_oneshot` beats `force_agent` (safety-first). 44 unit tests. GitHub Issue #56. |
| v0.5 | 2026-04-10 | HARNESS-05: API endpoint + SSE streaming. `harness/router.py` with `POST /v2/obd/{session_id}/diagnose/agent`. Wires `run_diagnosis_loop()` async generator to `StreamingResponse`. Auth via `get_current_user`, session ownership, cached diagnosis check, `force` re-diagnosis. Stores result in `DiagnosisHistory` with `provider="agent"`. SSE events: `status`, `tool_call`, `tool_result`, `hypothesis`, `done`, `error`, `cached`. 2KB padding prefix. Registered in `main.py`. 12 unit tests. GitHub Issue #55. |
| v0.6 | 2026-04-10 | HARNESS-07: Frontend agent visualization. `AgentDiagnosisView.tsx` (main agent streaming view), `ToolCallCard.tsx` (collapsible tool card), `IterationProgress.tsx` (iteration counter + tier badge). `streamAgentSSE()` and `streamAgentDiagnosis()` in `api.ts`. Agent types in `types.ts`. "Agent AI" sub-tab in `AnalysisLayout.tsx`. i18n: `agent.*` namespace in 3 locales. V1 untouched. GitHub Issue #57. |
| v0.7 | 2026-04-12 | HARNESS-08: Integration and E2E tests. `test_integration.py` (golden-path, event log, autonomy routing, fallback). `test_e2e_agent.py` (HTTP E2E: stream, history, cache, force, fallback). JSON fixtures: `golden_path_responses.json` (4 tool-call sequence), `fallback_responses.json`. Fixture loader in `fixtures/__init__.py`. Agent-to-V1 fallback in `router.py`: `_stream()` except block now falls back to `_oneshot_stream(skip_padding=True)` after error event. `e2e_real_llm` pytest marker. 182 total harness tests. GitHub Issue #58. |
| v0.9 | 2026-04-12 | HARNESS-10: Manual ingestion pipeline (GitHub Issue #70). `Manual` DB model + Alembic migration `q1r2`. Background pipeline: upload PDF → marker-pdf conversion (GPU-serialized via semaphore) → per-vehicle-model filesystem storage → pgvector RAG ingestion. 5 endpoints under `/v2/manuals` (upload, list, get, delete, status). Refactored `marker_convert.py` with `ConversionResult` dataclass and `vehicle_model_subdir` param. Frontend `/manuals` page: `ManualUploadForm` (drag-drop), `ManualList` (auto-polling status), `ManualViewer`. Startup recovery marks stuck conversions as failed. Config: `manual_storage_path`, `manual_max_file_size_bytes`, `manual_use_llm`. i18n (EN, zh-CN, zh-TW). 16 unit tests. |
| v1.0 | 2026-04-13 | HARNESS-11: Multimodal manual navigation tools (GitHub Issue #71). 3 new filesystem tools: `list_manuals` (discover manuals, filter by vehicle model), `get_manual_toc` (heading tree with slugs + DTC index), `read_manual_section` (full section content with images as base64 content blocks). Multimodal infrastructure: `ToolOutput = str | List[ContentBlock]`, `ToolResult.output` accepts multimodal content, `_make_tool_message()` passes list content to OpenAI format, `_extract_text_for_sse()` strips images from SSE events. Context management: `estimate_content_tokens()` for multimodal token counting (images at 1000 tokens each), `truncate_tool_result()` handles list content (preserves images, truncates text), `_summarize_iteration()` extracts text snippets and drops images during compaction. Shared utilities in `manual_fs.py`: `slugify`, `parse_frontmatter`, `parse_heading_tree`, `extract_section`, `find_closest_slug`, `resolve_image_refs`, `load_image_as_content_block`, `build_multimodal_section`. Security: path traversal protection in image resolution, 5 MB per-image cap. 70 unit tests. |
| v1.2 | 2026-04-23 | HARNESS-14 phase 2: GLM 5.1 judge + manual-search sub-agent (GitHub Issue #73). **Commit 2 — judge**: `tests/harness/evals/judge.py` replaced stub with real `z-ai/glm-5.1` call (OpenRouter, temp=0, JSON mode, retry-once on parse failure, fallback to zero-score `[judge failure]` Grade after double failure). Pinned constants (`_JUDGE_MODEL`, `_JUDGE_TEMPERATURE`, `_JUDGE_MAX_TOKENS`, `_MAX_SECTION_CHARS=3000`). Judge client injectable via `client` kwarg (tests use fake, production falls back to cached default built from `settings.premium_llm_api_key`). New `judge_prompts.py` with rubric-pinned system prompt + `build_user_prompt()`. New `--mock-judge` CLI flag + `judge_client` fixture. 21 unit tests. **Commit 3 — agent**: New `app/harness_agents/` package. `types.py` defines production shapes (`Citation`, `SectionRef`, `ToolCallTrace`, `ManualAgentResult`, `StoppedReason`) re-exported by `tests/harness/evals/schemas.py`. `manual_agent.py` implements `run_manual_agent(question, obd_context, deps)` — restricted 4-tool ReAct loop (no `read_obd_data`), `asyncio.timeout` budget, max-iteration guard, graceful LLM-error handling (stopped_reason=error). Default model `qwen3.5:27b-q8_0` (what ships); override via `ManualAgentConfig.model` for ceiling comparison. Final-answer contract enforced via `_parse_final_json()` with 3 fallback strategies (direct JSON, markdown-fence strip, first-`{...}`-block regex). `read_manual_section` outputs auto-captured into `raw_sections` with `had_images` detected from multimodal blocks. Tool inputs sanitised for trace (strips `_`-prefixed keys, truncates long strings). `create_manual_agent_registry()` factory builds a fresh registry with exactly 4 tools. `manual_agent_prompts.py` pins citation-format rules and adversarial-entry handling. Eval `runner.py` replaced stub with thin wrapper building process-cached deps pointing at local Ollama. New `--mock-agent` flag + `manual_agent_deps` fixture. 33 unit tests (registry, parsers, full-loop happy path, budget/error). All 54 new tests pass; 680 total across project (1 pre-existing DB-env failure unrelated). |
| v1.1 | 2026-04-23 | HARNESS-14 phase 1: manual-agent evaluation suite scaffolding (GitHub Issue #73). New §12.5 documents the approach: a restricted ReAct sub-agent (4 manual tools, no `read_obd_data`) produces `ManualAgentResult` (summary + citations + raw_sections + tool_trace); `z-ai/glm-5.1` via OpenRouter grades it against a frozen `GoldenEntry` with a 5-dimension rubric (`section_match`, `fact_recall`, `hallucination`, `citation_present`, `trajectory_ok`) + weighted `overall`. HK model-selection constraint locks judge = `glm-5.1`, agent primary = local `qwen3.5:27b-q8_0`. Phase-1 deliverables: Pydantic schemas, stub runner + judge (no LLM calls), parametrized pytest with `--run-eval` CLI gate registered in `tests/conftest.py`, session-scoped `eval_report` fixture that writes `reports/eval_{timestamp}.json` at teardown, 3 dummy `v1/mws150a.jsonl` entries. Goldens are immutable once frozen (corrections bump `v1/` → `v2/`). All 3 phase-1 eval tests pass with `--run-eval`; 242 pre-existing harness tests unchanged. Known limitation: one-manual corpus (MWS150A only) — adversarial category swapped to intra-manual edge cases until a second manual is acquired. |

### Relationship to V1

This document describes the **V2 diagnosis orchestration layer** only. The following components are shared with V1 and documented in `design_doc.md`:

- Infrastructure and compute (V1 §12)
- Security, privacy, and compliance (V1 §13)
- Endpoint security and JWT auth (V1 §13.2, §13.3)
- RAG knowledge sources and PDF parsing pipeline (V1 §10.3)
- OBD-II diagnostic summarization pipeline (V1 §8.3)
- Database schema for sessions, feedback, and RAG chunks (V1 §8.2)
- Training and improvement pipeline / Phase 1.5 / Phase 2 (V1 §11)

V2 replaces the diagnosis orchestration described in V1 §10.4 ("golden workflow") with an agent loop, while preserving V1 endpoints as a fast-path fallback.

---

## 1) Executive summary

The V1 diagnosis pipeline is a fixed deterministic workflow: OBD data flows through rule-based preprocessing, anomaly detection, clue generation, and RAG retrieval, then the LLM performs a **single-shot text generation**. The LLM has no agency — all data selection, feature engineering, and retrieval decisions are made by procedural code.

V2 replaces this with a **harness engineering** architecture where the LLM drives the diagnostic investigation through tool calls. Instead of receiving a pre-chewed context and generating text, the LLM iteratively calls tools (`search_manual`, `detect_anomalies`, `get_pid_statistics`, etc.) to investigate faults, form hypotheses, gather evidence, and produce a structured diagnosis when satisfied.

The core formula: **Agent = Model + Harness**, where:
- **Model**: Premium LLM (via OpenRouter) with tool-calling capability
- **Harness**: Agent loop + tool registry + session log + context management + orchestration

Existing V1 pipeline functions are not discarded — they become **tools** that the LLM calls through a universal `execute(name, input) → string` interface. V1 one-shot endpoints remain as a fast-path for simple single-DTC diagnoses.

## 2) Problem statement

### 2.1 Limitations of V1 context engineering

The V1 architecture (described in V1 `design_doc.md` §10.4) has five structural limitations:

| # | Limitation | Impact |
|---|-----------|--------|
| 1 | **No iterative reasoning** — single-pass diagnosis, no follow-up investigation | Low-confidence diagnoses cannot be refined; LLM cannot say "I need more data on fuel trim" |
| 2 | **Fixed RAG query** — RAG query is deterministically constructed from rule-based clues (`summary_formatter.py:94-120`) | If the diagnosis mentions fuel pump failure, the system cannot do a second RAG search for "fuel pump failure modes" |
| 3 | **No tool use** — LLM cannot invoke external functions or request additional data | Cannot check parts databases, recall status, repair cost, or Mode 06 results |
| 4 | **No multi-turn** — each diagnosis is stateless, no chat history | Users cannot ask follow-up questions like "Why did you rule out the transmission?" |
| 5 | **No cross-session learning** — feedback is stored (`diagnosis_history`, feedback tables) but never retrieved for future diagnoses | Similar past cases with known root causes are not leveraged |

### 2.2 Goals (V2)

- **G1 — LLM-driven investigation**: The LLM decides what data to examine, what manual sections to retrieve, and when it has enough evidence.
- **G2 — Adaptive RAG**: Retrieval queries are generated by the LLM based on intermediate findings, not by deterministic rules.
- **G3 — Graduated autonomy**: Simple cases use V1 one-shot (fast, cheap); complex cases use agent loop (thorough, premium).
- **G4 — Full auditability**: Every tool call, result, hypothesis, and reasoning step is persisted in an append-only event log.
- **G5 — Coexistence**: V1 one-shot endpoints remain functional as fallback. V2 adds parallel agent endpoints.

### 2.3 Non-goals (V2)

- Replacing V1 infrastructure (Docker, Postgres, Ollama, Nginx) — shared as-is.
- Model fine-tuning or preference optimization — stays in V1 Phase 1.5/2 (V1 §11).
- Real-time OBD streaming or live sensor data.
- Multi-user agent collaboration or team-based diagnosis.
- MCP (Model Context Protocol) server implementation — direct tool handlers are sufficient for V2 scope.

## 3) Architecture overview

### 3.1 The 5-component model

V2 follows the agent architecture model with five decoupled components, inspired by the Anthropic managed-agent engineering design (see References §14). Each component can be developed and tested independently.

```
┌─────────────────────────────────────────────────────────────────────┐
│                     STF Diagnosis Harness                           │
│                                                                     │
│  ┌──────────────────────┐            ┌───────────────────────────┐  │
│  │   Tools / Resources   │            │         Session            │  │
│  │                       │            │   (Append-only event log)  │  │
│  │  get_pid_statistics   │            │                           │  │
│  │  detect_anomalies     │            │  tool_call + tool_result  │  │
│  │  generate_clues       │            │  hypothesis events        │  │
│  │  search_manual        │            │  diagnosis drafts         │  │
│  │  refine_search        │            │  confidence scores        │  │
│  │  search_case_history  │            │  error events             │  │
│  │  get_session_context  │            │                           │  │
│  └──────────┬────────────┘            └─────────────┬─────────────┘  │
│             │                                       │                │
│             ▼                                       ▼                │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                      Harness Loop                            │   │
│  │                                                              │   │
│  │  1. Read session → build context window                      │   │
│  │  2. Call LLM with tool schemas (function calling format)     │   │
│  │  3. LLM returns: tool_use blocks OR stop_reason=end_turn     │   │
│  │  4. Execute tool calls → write events to session             │   │
│  │  5. If stop → extract structured diagnosis → return          │   │
│  │  6. Else → compact context if needed → goto 1               │   │
│  └──────────────────────────────────────────────────────────────┘   │
│             │                                       │                │
│             ▼                                       ▼                │
│  ┌──────────────────────┐            ┌───────────────────────────┐  │
│  │      Sandbox          │            │      Orchestration         │  │
│  │   (OBD data runtime)  │            │   (Graduated autonomy)    │  │
│  │                       │            │                           │  │
│  │  OBD log files        │            │  Complexity classifier    │  │
│  │  Parsed summaries     │            │  Tier routing (0→3)       │  │
│  │  Temp computation     │            │  Timeout / cost guards    │  │
│  │  Audio files          │            │  V1 fallback on failure   │  │
│  └───────────────────────┘            │  Sub-agent lifecycle (*)  │  │
│                                       └───────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                              (*) Future — HARNESS-09
```

**Component responsibilities:**

| Component | Responsibility | Implementation |
|-----------|---------------|----------------|
| **Tools** | Execute diagnostic functions; return text summaries (never raw arrays) | `diagnostic_api/app/harness_tools/` |
| **Session** | Append-only event log for auditability and resumability | `HarnessEventLog` table in Postgres |
| **Harness** | Agent loop: call LLM → dispatch tools → manage context → iterate | `diagnostic_api/app/harness/loop.py` |
| **Sandbox** | OBD data files, parsed summaries, temp storage | Existing filesystem storage (`/app/data/`) |
| **Orchestration** | Graduated autonomy: route simple→one-shot, complex→agent | `diagnostic_api/app/harness/autonomy.py` |

### 3.2 V1 vs V2 comparison

| Aspect | V1 (Context Engineering) | V2 (Harness Engineering) |
|--------|-------------------------|--------------------------|
| **LLM role** | Terminal text generator | Autonomous investigator with tools |
| **Data selection** | Deterministic pipeline (4 stages) | LLM-driven via tool calls |
| **RAG query** | Rule-based from clues (`summary_formatter.py`) | LLM-generated, adaptive |
| **Iteration** | Single pass | Multi-turn agent loop (1–10 iterations) |
| **Context** | Static (system + user, 2 messages) | Dynamic (grows with tool results, compacted) |
| **Latency** | 2–5s | 2–60s (tier-dependent) |
| **Cost** | ~$0.01 per diagnosis | $0.01–$0.15 (tier-dependent) |
| **Model** | Local (qwen3.5:27b) or premium (one-shot) | Premium only (tool calling required) |
| **Auditability** | Final text stored | Every tool call and result logged |

### 3.3 Code layout

```
diagnostic_api/
  app/
    harness/                     # NEW — Agent loop and orchestration
      __init__.py
      loop.py                    # Core agent loop (async generator)
      deps.py                    # Dependency injection (LLM client, tools)
      tool_registry.py           # Tool dispatch map + schema assembly
      context.py                 # Token budget tracking + compaction
      session_log.py             # Append-only event persistence
      harness_prompts.py         # V2 system prompt (dynamic)
      autonomy.py                # Graduated autonomy router
      router.py                  # FastAPI endpoint: /diagnose/agent
    harness_tools/               # NEW — Tool handler implementations
      __init__.py
      obd_tools.py               # Wraps obd_agent functions
      rag_tools.py               # Wraps app/rag/retrieve
      history_tools.py           # Wraps diagnosis_history queries
    expert/                      # SHARED — LLM clients (V1 + V2)
    rag/                         # SHARED — RAG pipeline (becomes tool backend)
    auth/                        # SHARED — JWT auth
    models_db.py                 # SHARED + new HarnessEventLog model
    config.py                    # SHARED + new harness config fields
  tests/
    harness/                     # NEW — V2 test suite
      __init__.py
      test_tool_registry.py
      test_obd_tools.py
      test_rag_tools.py
      test_loop.py
      test_session_log.py
      test_context.py
      test_autonomy.py
      test_router.py
      test_integration.py
      fixtures/                  # Recorded LLM responses for deterministic tests
```

## 4) Harness loop design

### 4.1 Core agent loop

The harness loop follows the ReAct pattern (Reason → Act → Observe → Repeat), implemented as a Python async generator that yields SSE-compatible events:

```python
async def run_diagnosis_loop(
    session_id: uuid.UUID,
    parsed_summary: dict,
    deps: HarnessDeps,
) -> AsyncIterator[HarnessEvent]:
    """Core agent loop for diagnosis investigation.

    Yields HarnessEvent objects (tool_call, tool_result, hypothesis,
    token, done, error) consumable by SSE streaming.

    Args:
        session_id: OBD analysis session to diagnose.
        parsed_summary: Pre-computed summary from V1 pipeline.
        deps: Injected dependencies (LLM client, tools, config).

    Yields:
        HarnessEvent with event_type and payload.
    """
    messages = _build_initial_context(parsed_summary, deps.tool_schemas)
    iteration = 0

    while iteration < deps.config.max_iterations:
        # 1. Call LLM with tool schemas
        response = await deps.llm_client.chat(
            messages=messages,
            tools=deps.tool_schemas,
            temperature=0.3,
            max_tokens=deps.config.max_tokens,
        )

        # 2. Check if LLM wants to stop
        if response.stop_reason == "end_turn":
            diagnosis = _extract_diagnosis(response.content)
            yield HarnessEvent("done", diagnosis)
            return

        # 3. Execute tool calls
        for tool_call in response.tool_calls:
            yield HarnessEvent("tool_call", {
                "name": tool_call.name,
                "input": tool_call.input,
            })

            result = await deps.tool_registry.execute(
                tool_call.name, tool_call.input
            )

            yield HarnessEvent("tool_result", {
                "name": tool_call.name,
                "output": result,
            })

            messages.append(tool_result_message(
                tool_call.id, result
            ))

        # 4. Compact context if approaching token limit
        if _estimate_tokens(messages) > deps.config.compact_threshold:
            messages = await _compact_context(messages, deps)

        iteration += 1

    # Max iterations reached — return best effort
    yield HarnessEvent("done", _extract_partial_diagnosis(messages))
```

**Key design decisions:**

- **Async generator** (`AsyncIterator[HarnessEvent]`): Matches the V1 SSE streaming pattern. The router wraps this generator in `StreamingResponse` — same pattern as `generate_obd_diagnosis_stream()` in V1.
- **Dependency injection** (`HarnessDeps`): LLM client, tool registry, and config are injected, enabling test doubles without module-level mocking.
- **Max iteration guard**: Configurable (default: 10). Prevents infinite loops if the LLM never decides to stop.
- **Partial diagnosis**: If max iterations are reached, the harness extracts the best available diagnosis from the conversation history rather than failing.

### 4.2 LLM integration

The agent loop requires a model that supports **function calling** (tool use). The OpenAI-compatible API format is used:

```python
# Tool schema format (OpenAI function calling)
tools = [
    {
        "type": "function",
        "function": {
            "name": "search_manual",
            "description": "Search vehicle service manuals...",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "..."},
                    "top_k": {"type": "integer", "default": 3},
                },
                "required": ["query"],
            },
        },
    },
    # ... more tools
]

response = await client.chat.completions.create(
    model=model_name,
    messages=messages,
    tools=tools,
    tool_choice="auto",  # LLM decides whether to call tools
    temperature=0.3,
    stream=True,
)
```

**Model requirements:**

| Requirement | Rationale |
|-------------|-----------|
| Tool calling support | Core to the agent pattern |
| Reliable JSON tool arguments | Tools validate via Pydantic; malformed args fail gracefully |
| Reasonable cost per token | Agent sessions use 3–10x more tokens than one-shot |
| Available in HK region | PolyU server is in Hong Kong (see GitHub Issue #23) |

**Candidate models** (via OpenRouter, subject to region availability):

- `deepseek/deepseek-v3.2` — current server default, tool calling supported
- `qwen/qwen3.5-plus-02-15` — HK-accessible, tool calling supported
- Future: Anthropic/OpenAI models if region restrictions are resolved

**Local models** (qwen3.5:27b via Ollama) are **not used for agent mode** due to unreliable tool-calling behavior at small parameter counts. Local models continue to serve V1 one-shot diagnoses.

### 4.3 Error handling

| Error type | Handling | User experience |
|-----------|---------|-----------------|
| Tool execution error | Error string returned as `tool_result`; LLM can self-correct or skip | SSE `tool_result` event shows error; agent continues |
| Malformed tool call (bad JSON args) | Pydantic validation error returned as `tool_result` | Agent retries with corrected args |
| LLM timeout (no response) | After 60s, yield partial diagnosis from conversation | SSE `error` event with partial diagnosis |
| LLM returns unknown tool name | Error string: "Unknown tool: {name}" | Agent self-corrects on next iteration |
| Max iterations reached | Extract best diagnosis from conversation history | SSE `done` event with `"partial": true` flag |
| Agent loop fails entirely | Fall back to V1 one-shot diagnosis | SSE `error` event, then automatic fallback to `_oneshot_stream()` (implemented HARNESS-08) |

## 5) Tool registry

### 5.1 Tool interface

Every tool follows the universal interface: `execute(name: str, input: dict) → ToolResult`

```python
ContentBlock = Dict[str, Any]   # {"type": "text", "text": "..."} or {"type": "image_url", ...}
ToolOutput = Union[str, List[ContentBlock]]

@dataclass(frozen=True)
class ToolDefinition:
    """Schema and handler for a single diagnostic tool."""

    name: str
    description: str
    input_schema: dict          # JSON Schema for function calling
    handler: Callable           # async (input: dict) -> ToolOutput
    max_result_chars: int = 50_000
```

**Multimodal support (HARNESS-11)**: Tool handlers may return plain `str` (text-only) or `List[ContentBlock]` for multimodal results containing images. The loop passes list content directly to the OpenAI-compatible API. SSE events strip base64 images via `_extract_text_for_sse()`.

**Privacy invariant**: Tool handlers return text summaries, scores, or multimodal content blocks (text + images from local filesystem). No tool ever returns raw sensor arrays or time-series DataFrames. Images come only from locally stored service manuals.

### 5.2 Agent-native tools

These tools give the agent direct access to data and knowledge. The agent does its own analysis — no pre-computed statistics, anomalies, or clues. `session_id` is auto-injected by the loop; the LLM never passes UUIDs.

> **Design principle**: The LLM is the diagnostic expert. Tools provide data access and knowledge retrieval, not pre-digested analysis. (HARNESS-09, GitHub Issue #69)

#### `read_obd_data`

| Field | Value |
|-------|-------|
| **Purpose** | Read OBD-II sensor data from the session's raw log file |
| **Input** | `{ "signals": ["RPM", "COOLANT_TEMP"], "start_time": "...", "end_time": "...", "every_nth": N }` |
| **Overview mode** | Omit `signals` to get available PIDs, time range, DTCs, and row count |
| **Signal query mode** | Provide `signals` to get a filtered table of PID values over time |
| **Implementation** | Reads raw TSV via `obd_agent.log_parser.parse_log_file()`, filters to numeric PIDs |
| **Name resolution** | Accepts both PID names (RPM) and semantic names (engine_rpm) |
| **Privacy** | Returns filtered numeric values only; max 50 rows per call with truncation notice |
| **File** | `harness_tools/obd_data_tools.py` |

#### `search_manual`

| Field | Value |
|-------|-------|
| **Purpose** | Search vehicle service manuals via RAG (pgvector cosine similarity) |
| **Input** | `{ "query": "search text", "vehicle_model": "MWS-150-A", "top_k": 5, "exclude_chunk_ids": [10, 20] }` |
| **Output** | Text of matched manual sections with doc_id, section_title, and similarity score |
| **Example output** | `"[0.87] MWS150-A#3.2 — Fuel System Inspection: Check fuel pressure regulator..."` |
| **Vehicle model filter** | Restricts search to chunks from a specific vehicle model (uses indexed `RagChunk.vehicle_model` column) |
| **Chunk exclusion** | `exclude_chunk_ids` for follow-up searches to get fresh results |
| **File** | `harness_tools/rag_tools.py` |

#### `list_manuals`

| Field | Value |
|-------|-------|
| **Purpose** | Discover available service manuals in the filesystem |
| **Input** | `{ "vehicle_model": "Toyota" }` (optional filter; matched leniently against manufacturer / model / canonical name) |
| **Output** | Text listing each manual's ID, canonical `vehicle="<Manufacturer> <Model>"` identity, page count, and section count, plus a match-or-refuse footer |
| **Implementation** | Scans `manual_storage_path` for `.md` files, parses YAML frontmatter (`manufacturer` + `vehicle_model`, stamped by APP-59's `write_frontmatter_identity`) |
| **Grounding (HARNESS-25, #136)** | The output footer instructs the agent to treat a manual as authoritative only if its make/model matches the vehicle under diagnosis (session `vehicle_id` / VIN), and to state "no service manual is available for this vehicle" when none matches — rather than adopting an unrelated manual's vehicle identity. Reinforced by the "Vehicle grounding (critical)" rule in the main system prompt and the manual sub-agent prompt. |
| **File** | `harness_tools/manual_tools.py` |

#### `get_manual_toc`

| Field | Value |
|-------|-------|
| **Purpose** | Get the heading structure of a specific manual for targeted section reading |
| **Input** | `{ "manual_id": "MWS150A_Service_Manual" }` |
| **Output** | Indented heading tree with slugs + DTC quick-reference index |
| **Implementation** | Parses markdown headings, builds tree with `parse_heading_tree()`, extracts DTC index from Appendix |
| **File** | `harness_tools/manual_tools.py` |

#### `read_manual_section`

| Field | Value |
|-------|-------|
| **Purpose** | Read a full manual section with embedded images (wiring diagrams, exploded views) |
| **Input** | `{ "manual_id": "MWS150A_Service_Manual", "section": "3-2-fuel-system-troubleshooting", "include_subsections": true }` |
| **Output** | **Multimodal**: `List[ContentBlock]` with interleaved text and base64-encoded PNG images |
| **Section matching** | Exact slug → slugified text → substring match. Actionable error with suggestions on miss. |
| **Image loading** | Resolves `![alt](images/...)` references, loads PNGs from disk, 5 MB per-image cap, path traversal protection |
| **File** | `harness_tools/manual_tools.py` + `harness_tools/manual_fs.py` (shared helpers) |

### 5.3 Removed tools (V1 wrappers, removed in HARNESS-09)

The following tools were removed because they read pre-computed V1 pipeline results from JSONB, giving the agent no investigative capability. The agent now reads raw data directly via `read_obd_data`.

- `get_pid_statistics` — read cached statistics from `result_payload`
- `detect_anomalies` — read cached anomaly events from `result_payload`
- `generate_clues` — read cached clues from `result_payload`
- `get_session_context` — read `parsed_summary_payload` (same data as user message)
- `refine_search` — merged into `search_manual` via `exclude_chunk_ids`
- `search_case_history` — deferred to future issue

### 5.4 Tool dispatch

The tool registry uses a dispatch map pattern. Adding a new tool requires one dict entry and one schema definition — zero changes to the agent loop.

```python
class ToolRegistry:
    """Registry of diagnostic tools with dispatch and schema assembly."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool

    async def execute(self, name: str, input_data: dict) -> str:
        if name not in self._tools:
            return f"Error: Unknown tool '{name}'"
        tool = self._tools[name]
        try:
            return await tool.handler(input_data)
        except Exception as e:
            return f"Error executing {name}: {e}"

    @property
    def schemas(self) -> list[dict]:
        """OpenAI function-calling tool schemas."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in self._tools.values()
        ]
```

**Implementation file**: `diagnostic_api/app/harness/tool_registry.py`

## 6) Session and event log

### 6.1 Event log design

Every action during an agent diagnosis session is persisted as an append-only event for auditability, debugging, and future training data extraction.

**Event types:**

| Event type | Payload | When emitted |
|-----------|---------|-------------|
| `session_start` | `{ session_id, parsed_summary_hash, model, autonomy_tier }` | Agent loop begins |
| `tool_call` | `{ name, input, iteration }` | Before tool execution |
| `tool_result` | `{ name, output, duration_ms }` | After tool execution |
| `hypothesis` | `{ fault, confidence, evidence_summary }` | LLM forms intermediate hypothesis (optional) |
| `context_compact` | `{ before_tokens, after_tokens, strategy }` | Context compaction triggered |
| `diagnosis_done` | `{ diagnosis_text, total_iterations, total_tokens }` | Agent loop completes |
| `error` | `{ error_type, message, iteration }` | Error during agent loop |

### 6.2 Database model

```python
class HarnessEventLog(Base):
    """Append-only event log for agent diagnosis sessions."""

    __tablename__ = "harness_event_log"

    id = Column(UUID, primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID, ForeignKey("obd_analysis_sessions.id"),
                        nullable=False, index=True)
    event_type = Column(String(50), nullable=False, index=True)
    iteration = Column(Integer, nullable=False, default=0)
    payload = Column(JSONB, nullable=False)
    created_at = Column(DateTime, server_default=func.now(),
                        nullable=False)
```

**Indexes**: `(session_id, created_at)` composite for chronological event retrieval.

### 6.3 Relationship to V1 models

| V1 Model | V2 Change |
|----------|-----------|
| `OBDAnalysisSession` | Unchanged. V1 pipeline populates `parsed_summary_payload` before agent loop starts. Adds `agent_diagnosis_feedback` relationship (HARNESS-24). |
| `DiagnosisHistory` | Extended: `provider` CHECK constraint adds `"agent"` alongside `"local"` and `"premium"`. |
| `HarnessEventLog` | **New table**. FK to `OBDAnalysisSession`. |
| `OBDAgentDiagnosisFeedback` | **New table** (HARNESS-24, issue #127). Sixth per-view feedback table, mirroring `OBDPremiumDiagnosisFeedback`: shared feedback mixin columns + `diagnosis_text` snapshot + nullable `diagnosis_history_id` FK to `DiagnosisHistory`. Holds expert feedback on `provider='agent'` generations so it stays separable from local/premium feedback for training-data collection. |

**Alembic migrations**: `p9q0` adds the `HarnessEventLog` table and updates the `DiagnosisHistory.provider` CHECK constraint; `e4f5a6b7c8d9` (HARNESS-24) adds the `obd_agent_diagnosis_feedback` table.

## 7) Context management

### 7.1 Context window strategy

The agent loop builds a conversation that grows with each tool call/result cycle:

```
Message 1: system     — Diagnosis instructions + tool descriptions
Message 2: user       — "Diagnose session {id}. Vehicle: {vehicle_id}, DTCs: ..."
Message 3: assistant  — tool_call: get_session_context({session_id})
Message 4: tool       — tool_result: "Vehicle: V12345, Time: ..., DTCs: P0300 ..."
Message 5: assistant  — tool_call: detect_anomalies({session_id})
Message 6: tool       — tool_result: "[HIGH] RPM range_shift at 12:03 ..."
Message 7: assistant  — tool_call: search_manual({query: "P0300 misfire ..."})
Message 8: tool       — tool_result: "[0.87] MWS150-A#3.2 — Ignition ..."
Message 9: assistant  — "Based on my investigation, the diagnosis is: ..."
```

**Token budget**: Tracked per-iteration via `estimate_tokens()` (character-based: `len(text) // 4`). Fast enough to call every iteration (~20% accuracy vs real tokenizers for English diagnostic text). `estimate_messages_tokens()` sums content + tool-call arguments + 4 tokens overhead per message. **Multimodal**: `estimate_content_tokens()` handles both string and `List[ContentBlock]` content — text blocks use char/4 estimation, image blocks add a fixed 1000-token estimate each.

### 7.2 Compaction strategy

Two-tier compaction to prevent context overflow. **Implementation**: `diagnostic_api/app/harness/context.py`.

**Tier 1 — Tool result truncation** (`truncate_tool_result()`): Applied to every tool result in the agent loop after `ToolRegistry.execute()`. For string results: head+tail truncation with marker. For multimodal list results: subtracts image token cost from budget, truncates text blocks only (images are preserved intact since they cannot be meaningfully split).

**Tier 2 — Auto-compact** (`maybe_compact()`): Called between iterations in the agent loop after all tool results are appended. When `estimate_messages_tokens(messages)` exceeds `HarnessConfig.compact_threshold` (default: 60000 tokens):
1. Identify iteration boundaries (each = 1 assistant msg with `tool_calls` + subsequent tool msgs)
2. Keep the system prompt and initial user message intact (messages[0:2])
3. Keep the most recent 2 iterations uncompacted (`keep_recent=2`)
4. Replace older iterations with a single summary: `"[Compacted] Prior iterations summary:\n- Iter 1: tool_name -> first 80 chars of result..."`
5. Emit `context_compact` event with `before_tokens`, `after_tokens`, `compacted_iterations`, `kept_iterations`, `strategy`

If there are not enough iterations to compact (≤ `keep_recent`), compaction is skipped.

### 7.3 System prompt design

Unlike V1's static system prompt (`expert/prompts.py`), V2's system prompt is dynamically assembled:

```
1. Role definition (automotive diagnostic expert with tool access)
2. Available tool descriptions (from tool registry schemas)
3. Investigation protocol:
   a. Start by examining session context (get_session_context)
   b. Analyze anomalies and clues (detect_anomalies, generate_clues)
   c. Search relevant manual sections (search_manual, refine_search)
   d. Check similar past cases if available (search_case_history)
   e. Form diagnosis with evidence and confidence
4. Output format requirements (structured text, severity ratings)
5. Privacy rules (never request raw sensor data)
6. Stop criteria (stop when confident or after exhausting relevant tools)
```

**Implementation file**: `diagnostic_api/app/harness/harness_prompts.py`

## 8) Graduated autonomy (orchestration)

### 8.1 Complexity classification

A rule-based classifier analyzes the `parsed_summary_payload` to determine which diagnosis path to use:

```python
def classify_complexity(parsed_summary: dict) -> int:
    """Classify diagnostic complexity into autonomy tiers.

    Args:
        parsed_summary: Flat-string summary from V1 pipeline.

    Returns:
        Tier 0-3 indicating required autonomy level.
    """
    dtc_count = _count_dtcs(parsed_summary.get("dtc_codes", ""))
    anomaly_severity = _max_severity(
        parsed_summary.get("anomaly_events", "")
    )
    clue_count = _count_clues(
        parsed_summary.get("diagnostic_clues", "")
    )

    # Tier 0: Simple — single DTC, low severity, few clues
    if dtc_count <= 1 and anomaly_severity <= "MODERATE" \
       and clue_count <= 3:
        return 0

    # Tier 1: Moderate — multiple DTCs or high severity
    if dtc_count <= 3 and anomaly_severity != "CRITICAL":
        return 1

    # Tier 2: Complex — many DTCs or critical severity (future: sub-agents)
    if dtc_count > 3 or anomaly_severity == "CRITICAL":
        return 2

    # Tier 3: Follow-up — has prior diagnosis history
    return 3
```

### 8.2 Autonomy tiers

| Tier | Criteria | Strategy | Latency | Est. cost |
|------|---------|----------|---------|-----------|
| **0** | Single DTC, moderate severity, ≤3 clues | V1 one-shot (local or premium) | 2–5s | ~$0.01 |
| **1** | Multiple DTCs or high severity | Agent loop, 1–5 iterations | 10–20s | ~$0.05 |
| **2** | Many DTCs or critical severity | Full agent, future: sub-agents per subsystem | 30–60s | ~$0.10–0.15 |
| **3** | Follow-up visit with history | Agent + `search_case_history` tool | 15–30s | ~$0.05–0.10 |

**Override**: Query parameter `force_agent=true` or `force_oneshot=true` on the endpoint allows manual tier override.

### 8.3 Sub-agent pattern (future — Tier 2)

For multi-subsystem faults, a parent agent spawns isolated sub-agents per subsystem:

- Each sub-agent gets a fresh context with a focused subset of tools
- Sub-agents investigate independently (engine, transmission, electrical, etc.)
- Each returns a text summary to the parent agent
- Parent synthesizes sub-agent findings into a unified diagnosis

**This is out of scope for V2 initial release** (tracked as HARNESS-09). Tier 2 cases will use a single agent with more iterations until sub-agents are implemented.

## 9) API endpoints

### 9.1 New endpoint

```http
POST /v2/obd/{session_id}/diagnose/agent
```

**Query parameters:**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `force` | bool | false | Force re-diagnosis even if cached |
| `locale` | string | "en" | Response language (en, zh-CN, zh-TW) |
| `max_iterations` | int | 10 | Max agent loop iterations |
| `force_agent` | bool | false | Force agent mode (skip tier classification) |
| `force_oneshot` | bool | false | Force V1 one-shot (skip agent) |

**Auth**: Requires Bearer JWT token (same `get_current_user` dependency as V1).

**Response**: `StreamingResponse` with `media_type="text/event-stream"`.

#### Agent diagnosis feedback (HARNESS-24, issue #127)

```http
POST /v2/obd/{session_id}/feedback/agent_diagnosis
```

Defined in `obd_analysis.py` (alongside the other per-view feedback endpoints, reusing the shared `_submit_feedback` / `_validate_diagnosis_history_id` helpers — it pulls in no harness modules). Validates the optional `diagnosis_history_id` against `provider='agent'` and persists to `obd_agent_diagnosis_feedback` with `feedback_type='agent_diagnosis'`. Body is the shared `OBDFeedbackRequest`; returns `201`.

**Why a dedicated endpoint+table** rather than relaxing `feedback/ai_diagnosis` to accept `provider ∈ {local, agent}`: the repo's convention is one feedback table per diagnosis view (5 → 6), and keeping agent feedback in its own table preserves a clean local-vs-agent split for the pilot's training-data goal. The earlier wiring (Agent AI tab → `feedback/ai_diagnosis`, which hard-requires `provider='local'`) is what produced the `400 provider mismatch` in issue #127.

The `GET /v2/obd/{session_id}/history` provider filter and the `DiagnosisHistoryItem.provider` / `FeedbackHistoryItem.tab_name` response Literals are widened to include `agent` / `agent_diagnosis` so agent generations show in the History tab and agent feedback renders in Feedback history.

### 9.2 SSE event stream format

V2 extends the V1 SSE protocol with new event types:

```
event: status
data: "Initializing agent diagnosis..."

event: tool_call
data: {"name": "get_session_context", "input": {"session_id": "abc-123"}}

event: tool_result
data: {"name": "get_session_context", "output": "Vehicle: V12345, DTCs: P0300..."}

event: tool_call
data: {"name": "search_manual", "input": {"query": "P0300 misfire diagnosis"}}

event: tool_result
data: {"name": "search_manual", "output": "[0.87] MWS150-A#3.2 — Ignition..."}

event: hypothesis
data: {"fault": "ignition coil failure", "confidence": 0.72, "iteration": 2}

event: token
data: "Based on the investigation, the primary fault is..."

event: done
data: {"text": "Full diagnosis text...", "diagnosis_history_id": "uuid",
       "iterations": 3, "tools_called": 5, "autonomy_tier": 1}
```

**Backward compatibility**: V1 events (`status`, `token`, `done`, `cached`, `error`) retain their exact format. Frontend can detect V2 events by checking for `tool_call` event type.

### 9.3 Router registration

```python
# diagnostic_api/app/harness/router.py
from fastapi import APIRouter

harness_router = APIRouter(prefix="/v2/obd", tags=["harness"])

# diagnostic_api/app/main.py (addition)
from app.harness.router import harness_router
app.include_router(harness_router)
```

V1 endpoints in `obd_analysis.py` and `obd_premium.py` remain registered and functional.

## 10) Privacy and data boundaries

### 10.1 Architectural enforcement

V2 enforces the same privacy boundary as V1 (V1 §13.1) but through a **structural guarantee** rather than code convention:

- **V1**: Developers must remember not to pass raw data to the LLM prompt template.
- **V2**: Tools return `str` — the only way data reaches the LLM is through tool result strings. Tool handlers format summaries and scores; they never return `pd.DataFrame`, `np.ndarray`, or raw binary.

```python
# Tool handler signature enforces str return
async def get_pid_statistics(input_data: dict) -> str:
    # ... compute statistics ...
    return format_statistics_as_text(stats)  # Always str
```

If a developer writes a tool that returns raw data, the `ToolRegistry.execute()` method calls `str()` on the result as a safety net. Code review should catch this, but the interface provides a structural backstop.

### 10.2 Event log privacy

Event payloads in `HarnessEventLog` contain tool inputs and outputs — both are text summaries, not raw sensor data. The same privacy guarantees that apply to `diagnosis_text` in `OBDAnalysisSession` apply to event payloads.

## 11) Frontend changes

### 11.1 Agent diagnosis view

**Implemented in HARNESS-07** (GitHub Issue #57).

The agent SSE handler (`streamAgentSSE()` in `api.ts`) recognizes V2 event types alongside V1 events. V1 `streamSSE()` is untouched.

```typescript
// V1 events (also used for Tier 0 fallback)
case "token":       cb.onToken(parsed); break;
case "status":      cb.onStatus(parsed); break;

// V2 agent events
case "tool_call":   cb.onToolCall(parsed); break;
case "tool_result": cb.onToolResult(parsed); break;
case "done":        cb.onDone(parsed); break;  // enriched with autonomy_tier, iterations, tools_called
case "cached":      cb.onCached(parsed); break;
case "error":       cb.onError(parsed); break;
```

**Components**:

- `AgentDiagnosisView.tsx` — Main agent streaming view. Manages `ToolInvocation[]` state, pairs `tool_call` → `tool_result` events by name+iteration. Renders IterationProgress + ToolCallCard list + final diagnosis text. Tier 0 fallback: renders token-by-token text identical to V1.
- `ToolCallCard.tsx` — Collapsible card per tool invocation. Header: tool name badge, input summary, status icon (spinner/check/X), duration. Body: full input JSON, output text (truncated at 500 chars with Show more/less). Left border: blue=calling, green=done, red=error.
- `IterationProgress.tsx` — Iteration counter ("Iteration 2/10") + autonomy tier badge (color-coded: gray=Tier 0, blue=Tier 1, amber=Tier 2, purple=Tier 3) + strategy label.

**Integration**: "Agent AI" sub-tab in the AI Diagnosis section of `AnalysisLayout.tsx`, visible when `premiumLlmEnabled` is true (agent endpoint requires premium API key).

**i18n**: ~25 strings under `agent.*` namespace in EN, zh-CN, zh-TW locale files.

### 11.2 Graduated autonomy UI

- Autonomy tier badge displayed via `IterationProgress.tsx` (Tier 0–3 with color coding)
- Strategy label shown in muted text next to badge
- Iteration counter with progress bar during streaming
- `force_agent` and `force_oneshot` query params supported by `streamAgentDiagnosis()` API function (UI toggle deferred to future iteration)

## 12) Testing strategy

### 12.1 Unit tests

| Test target | Strategy | File |
|-------------|----------|------|
| Tool handlers | Deterministic — same inputs as V1 pipeline functions | `test_obd_tools.py`, `test_rag_tools.py` |
| Tool registry | Dispatch correctness, unknown tool handling, schema assembly | `test_tool_registry.py` |
| Context compaction | Token counting, truncation thresholds, compact output | `test_context.py` |
| Autonomy classifier | Classification logic with representative parsed_summaries | `test_autonomy.py` |
| Session log | Event persistence, retrieval, ordering | `test_session_log.py` |

### 12.2 Integration tests

| Test target | Strategy | File |
|-------------|----------|------|
| Agent loop | Mocked LLM via `HarnessDeps` injection; recorded tool-call sequences | `test_loop.py` |
| API endpoint | FastAPI `TestClient` with mocked agent loop | `test_router.py` |
| Full integration | Mocked LLM + real tools + real DB | `test_integration.py` |

### 12.3 Agent behavior tests

| Test scenario | Expected behavior |
|---------------|-------------------|
| **Golden path** | LLM calls `get_session_context` → `detect_anomalies` → `search_manual` → produces diagnosis |
| **Adaptive RAG** | LLM calls `search_manual`, then calls `refine_search` with a more specific query |
| **Max iterations** | After 10 iterations, agent returns partial diagnosis with `"partial": true` |
| **Tool error** | Tool returns error string; LLM self-corrects or skips |
| **Fallback** | Agent loop raises exception; system falls back to V1 one-shot. Implemented in `router.py` `_stream()` except block (HARNESS-08). |
| **Unknown tool** | LLM calls non-existent tool; receives error; continues with valid tools |

### 12.4 End-to-end tests

Full flow with real premium model (run manually, not in CI):

1. Upload OBD log → `POST /v2/obd/analyze`
2. Request agent diagnosis → `POST /v2/obd/{id}/diagnose/agent`
3. Verify SSE events include `tool_call` and `tool_result`
4. Verify `diagnosis_history` row with `provider="agent"`
5. Verify `harness_event_log` has complete event sequence

### 12.5 Manual-agent evaluation suite (HARNESS-14)

Separate from the unit / integration / E2E layers above, this suite measures how well a **restricted manual-search sub-agent** uses the 3 manual-navigation tools (`list_manuals`, `get_manual_toc`, `read_manual_section`) to answer diagnostic inquiries. It isolates tool-use quality from OBD analysis quality and catches behaviours that deterministic unit tests miss — hallucinations, parameter misunderstanding, inefficient tool-call sequences, and omitted information.

**Architecture.** A thin restricted ReAct loop (max 12 iterations / 240 s wall, ~12K output tokens) calls only the 3 manual tools (`list_manuals`, `get_manual_toc`, `read_manual_section`) — `read_obd_data` and `search_manual` are explicitly excluded. The agent returns a structured `ManualAgentResult` (summary, citations, raw_sections, tool_trace, iterations, total_tokens). The output is graded by `z-ai/glm-5.1` via OpenRouter (temperature 0, JSON mode) against a human-reviewed golden entry (`GoldenEntry` — question, golden_summary, golden_citations, must_contain, must_not_contain, expected_tool_trace).

**Eval LLM transport — thinking suppressed (#144).** For the local-Ollama target the eval builds its `ManualAgentDeps` with `OllamaNativeLLMClient` (`app/harness/deps.py`), which posts to Ollama's **native `/api/chat` with `"think": false"`** rather than the OpenAI-compat `/v1` adapter. A server probe established that `/v1` — and the `/no_think` prompt directive the eval driver previously relied on — do **not** suppress qwen3's reasoning channel; only native `/api/chat` does. Left in thinking mode the agent ran at ~36 s/call and timed out adversarial goldens before it could navigate **and** synthesise within the 240 s wall (`stopped_reason=timeout`, `answer_quality=0`); with thinking off it runs at ~14 s/call and completes (e.g. `adversarial-006`/P9999 went from a 0-score timeout to `complete`/0.73). The client translates OpenAI↔Ollama-native message and tool-call shapes; multimodal tool content is flattened to text (`qwen3.5:27b` is text-only). **Scope:** eval-only — production sub-agent delegation runs on the shared OpenRouter client and was never affected. Complements the #165 graceful-decline backstop (a safety net once latency no longer dominates).

**Graceful finalize / decline (HARNESS-23 T2, #144).** The loop is biased toward *finalizing* (an answer or a `Not found:` decline) rather than searching until the budget is spent. First-round failure: all 6 adversarial runs timed out at `answer_quality=0`. A branch-deploy server smoke refined this: T1's 240 s budget already lets the simpler adversarial entries complete, but `P9999` (fake DTC) still timed out because the live model **spins by reading distinct sections** (6 of them) rather than re-issuing identical calls — so a byte-identical repeat-detector never fires. Two layers: (1) the prompt instructs the agent to return the documented `{"summary": "Not found: …", "citations": []}` shape *immediately* once no manual matches the vehicle or the answer is absent; (2) a **forced-synthesis backstop** in `run_manual_agent` counts cumulative `read_manual_section` calls — once it reaches `_MAX_SECTION_READS_BEFORE_FINAL` (or a byte-identical call recurs), the loop re-prompts the model **with the tools withheld (`tools=[]`)** so it must answer-or-decline from the evidence already gathered, terminating at `stopped_reason="complete"` instead of the wall. The forced turn also appends `/no_think` to the system message (`_suppress_thinking_in_system`): in thinking mode a single `qwen3.5:27b` synthesis call costs ~30-90 s and would otherwise blow the *remaining* budget mid-synthesis (the smoke caught exactly this — the backstop fired but the slow forced turn still rode the wall to a timeout); disabling reasoning for that one turn drops it to ~2.5 s. A forced *synthesis* turn (not a canned refusal) lets the model give the substantive corrective answer the goldens expect; if that turn errors or returns nothing, it degrades to a canned `Not found:`. Pairs with the judge change (T5, #146) that credits a correct refusal.

**Judge rubric.** The judge returns a `Grade` with five dimensions: `section_match` (did the agent cite the golden slug?), `fact_recall` (fraction of `must_contain` items present), `hallucination` (any `must_not_contain` found?), `citation_present`, and `trajectory_ok` (≤1.5× expected tool count, no brute-force read-all). Overall = 0.4·section_match + 0.3·fact_recall + 0.2·(1−hallucination) + 0.1·citation_present. Trajectory is reported but not enforced in the pass threshold so cost regressions surface without failing tests.

**Model selection under HK constraint.** The PolyU server is in Hong Kong; Claude, OpenAI, and Gemini are geo-blocked (see §10 of `docs/v2_dev_plan.md` and Issue #23). Locked choices: judge = `z-ai/glm-5.1`; agent primary = local `qwen3.5:27b-q8_0` (what ships); phase-5 ceiling comparison = `z-ai/glm-5.1` or `moonshotai/kimi-k2`.

**Golden set immutability — two-tier corpus (HARNESS-20).** Goldens live under `tests/harness/evals/golden/v{N}/`. Since HARNESS-20 (Issue #90) the v2 set is split into two tiers:

- **Candidate** (`v2/*.jsonl`) — mutable. The dashboard (`/v2/goldens` API + `/goldens` UI) syncs this set into `golden_entries` on app startup; experts grade entries; in-place edits land here in response to review feedback. Followed by an "admin note" review post so the expert is asked to re-grade.
- **Locked** (`v2/locked/*.jsonl`) — append-only. The eval harness (`tests/harness/evals/test_manual_agent_eval.py`) reads ONLY this tier. The only legitimate write path is `scripts/promote_golden.py`, which enforces a review-quality gate (latest expert review must be `status='accept'` with `star_rating >= 4`), appends the candidate's raw JSONL line verbatim, computes SHA-256 of the canonical-serialised payload, and writes one row to `locked/PROMOTIONS.md` (timestamp / entry_id / hash / reviewer / expert_review_id / reason). `--force` bypasses the gate and is itself recorded. Revising a locked entry requires cloning to a new id; the script refuses to re-promote an existing id.

The `GoldenEntry` DB row carries an `is_locked: bool` flag set by a two-pass `golden_sync` (post-v1.5.2 schema fix): pass 1 mirrors candidate-tier content with `is_locked=False`; pass 2 walks `v2/locked/*.jsonl` and flips `is_locked=True` on every matching id. The row's *content* always reflects the latest candidate (so dashboard edits show up); the lock flag is just the badge meaning "this id has been promoted and the eval harness now reads the locked-file copy". Locked-tier *content* stays on the filesystem — the DB never tries to mirror it (an earlier `tier` string column did, which created a primary-key collision since both tiers share entry ids by design; see v1.5.2 revision entry). `GoldenEntrySummary.is_locked` and `GoldenEntryDetail.is_locked` surface the flag on the API. Deletions from the locked tier always require a version bump (`v2/` → `v3/locked/`).

Generation (unchanged): Claude reads a specific manual section and emits one `(question, summary, citations)` tuple, then a human accepts / edits / rejects in the dashboard before the entry lands in `v2/mws150a.jsonl`. Promotion to `v2/locked/mws150a.jsonl` is a separate, deliberate, audit-trailed step.

**Run mode.** The eval suite is gated behind `--run-eval` (pytest CLI flag registered in `tests/conftest.py`). Default `pytest` runs skip it so normal development stays fast and free. The session fixture `eval_report` accumulates `(entry, result, grade)` triples and writes a timestamped JSON artifact to `tests/harness/evals/reports/` at session teardown — enables regression tracking across branches. Intended cadence: nightly (not per-commit).

**Taxonomy — v1 distribution (30 entries):** DTC easy (8) / Symptom medium (6) / Component medium (6) / Image-required medium (4) / Adversarial hard (6, intra-manual: fake DTC `P9999`, out-of-scope query, typo'd slug, multi-section answer).

**Known limitation.** Only `MWS150A_Service_Manual` is ingested. Cross-manual adversarial scenarios are deferred to `v2/` once a second manual becomes available; `list_manuals` is therefore tested only at the unit level in v1.

See `docs/v2_dev_plan.md` HARNESS-14 and GitHub Issue #73 for the full plan and phasing.

## 13) Open questions

| # | Question | Impact | Resolution path |
|---|---------|--------|----------------|
| 1 | Can qwen3.5:27b handle function calling reliably enough for a local agent path? | Would enable agent mode without OpenRouter cost | Benchmark with 10 test cases; compare tool-call accuracy vs premium models |
| 2 | How to track and limit per-session token cost? | Cost control for premium agent sessions | Add token counter to `HarnessDeps`; emit `token_usage` events; enforce budget |
| 3 | How to evaluate agent vs one-shot diagnosis quality? | Validate that V2 improves over V1 | A/B testing with expert feedback; compare ratings on identical OBD logs |
| 4 | Minimum past cases for useful `search_case_history`? | Tool may return empty results initially | Start with empty; tool returns "no similar cases found"; LLM proceeds without |

## 14) References

- V1 design document: `docs/design_doc.md`
- V1 development plan: `docs/dev_plan.md`
- GitHub Issue #26: discussion: From Context Engineering to Harness Engineering
- Anthropic engineering blog: "Scaling Managed Agents: Decoupling the Brain from the Hands"
- Anthropic engineering blog: "Harness Design for Long-Running Application Development"
- Anthropic engineering blog: "Effective Harnesses for Long-Running Agents"
- Learning notes: `talonMMA/My-Brain/02-Study/AI-ML/Learn-Claude-Code` (S01–S12)
- Claude Code source reference: `talonMMA/claude-code`

## Appendix A — Tool schema definitions

```json
[
  {
    "type": "function",
    "function": {
      "name": "get_pid_statistics",
      "description": "Retrieve per-signal statistics (mean, std, min, max, percentiles) for the OBD session's PID data. Returns a text summary, never raw arrays.",
      "parameters": {
        "type": "object",
        "properties": {
          "session_id": {
            "type": "string",
            "description": "UUID of the OBD analysis session"
          }
        },
        "required": ["session_id"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "detect_anomalies",
      "description": "Run anomaly detection on the OBD session data. Returns text descriptions of detected anomaly events with severity, pattern, and time window.",
      "parameters": {
        "type": "object",
        "properties": {
          "session_id": {
            "type": "string",
            "description": "UUID of the OBD analysis session"
          },
          "focus_signals": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Optional list of signal names to focus anomaly detection on"
          }
        },
        "required": ["session_id"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "generate_clues",
      "description": "Generate diagnostic clues using rule-based inference on session statistics and anomalies. Returns clue text with rule ID, category, and evidence.",
      "parameters": {
        "type": "object",
        "properties": {
          "session_id": {
            "type": "string",
            "description": "UUID of the OBD analysis session"
          }
        },
        "required": ["session_id"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "search_manual",
      "description": "Search vehicle service manuals via RAG (pgvector cosine similarity). Returns matched sections with source doc_id, section title, and similarity score.",
      "parameters": {
        "type": "object",
        "properties": {
          "query": {
            "type": "string",
            "description": "Search query for manual sections (e.g., 'P0300 misfire diagnosis procedure')"
          },
          "top_k": {
            "type": "integer",
            "default": 3,
            "description": "Number of results to return"
          }
        },
        "required": ["query"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "refine_search",
      "description": "Adaptive RAG search — use this to search for additional manual sections based on intermediate diagnosis findings. Supports excluding already-retrieved documents.",
      "parameters": {
        "type": "object",
        "properties": {
          "query": {
            "type": "string",
            "description": "Refined search query based on current investigation findings"
          },
          "top_k": {
            "type": "integer",
            "default": 3,
            "description": "Number of results to return"
          },
          "exclude_doc_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Document IDs to exclude from results (already retrieved)"
          }
        },
        "required": ["query"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "search_case_history",
      "description": "Search past diagnosis cases for similar faults. Returns summaries of past diagnoses with provider, model, and date.",
      "parameters": {
        "type": "object",
        "properties": {
          "dtc_codes": {
            "type": "array",
            "items": {"type": "string"},
            "description": "DTC codes to search for (e.g., ['P0300', 'P0301'])"
          },
          "vehicle_id": {
            "type": "string",
            "description": "Optional vehicle ID to filter by"
          },
          "limit": {
            "type": "integer",
            "default": 5,
            "description": "Maximum number of past cases to return"
          }
        },
        "required": ["dtc_codes"]
      }
    }
  },
  {
    "type": "function",
    "function": {
      "name": "get_session_context",
      "description": "Retrieve the current OBD session's parsed summary including vehicle ID, time range, DTC codes, PID summary, anomaly events, and diagnostic clues. Call this first to understand the diagnostic case.",
      "parameters": {
        "type": "object",
        "properties": {
          "session_id": {
            "type": "string",
            "description": "UUID of the OBD analysis session"
          }
        },
        "required": ["session_id"]
      }
    }
  }
]
```

## Appendix B — Event log schema (Postgres DDL)

```sql
CREATE TABLE harness_event_log (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  UUID NOT NULL REFERENCES obd_analysis_sessions(id)
                    ON DELETE CASCADE,
    event_type  VARCHAR(50) NOT NULL,
    iteration   INTEGER NOT NULL DEFAULT 0,
    payload     JSONB NOT NULL,
    created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
);

CREATE INDEX ix_harness_event_session_time
    ON harness_event_log (session_id, created_at);

CREATE INDEX ix_harness_event_type
    ON harness_event_log (event_type);

-- Update DiagnosisHistory provider CHECK constraint
ALTER TABLE diagnosis_history
    DROP CONSTRAINT IF EXISTS ck_diagnosis_history_provider;

ALTER TABLE diagnosis_history
    ADD CONSTRAINT ck_diagnosis_history_provider
    CHECK (provider IN ('local', 'premium', 'agent'));
```

## Appendix C — V1 to V2 function mapping

| V1 Pipeline Stage | V1 Function | V1 File:Line | V2 Tool Name | V2 Wrapper File |
|-------------------|-------------|-------------|-------------|----------------|
| Statistics extraction | `extract_statistics()` | `obd_agent/statistics_extractor.py:212` | `get_pid_statistics` | `harness_tools/obd_tools.py` |
| Anomaly detection | `detect_anomalies()` | `obd_agent/anomaly_detector.py:529` | `detect_anomalies` | `harness_tools/obd_tools.py` |
| Clue generation | `generate_clues()` | `obd_agent/clue_generator.py:552` | `generate_clues` | `harness_tools/obd_tools.py` |
| RAG retrieval | `retrieve_context()` | `diagnostic_api/app/rag/retrieve.py:115` | ~~`search_manual`~~ (removed — no RAG in agent pipeline) | `harness_tools/rag_tools.py` |
| Summary formatting | `format_summary_flat_strings()` | `obd_agent/summary_formatter.py:25` | `get_session_context` | `harness_tools/obd_tools.py` |
| (none — new) | — | — | `refine_search` | `harness_tools/rag_tools.py` |
| (none — new) | — | — | `search_case_history` | `harness_tools/history_tools.py` |

## 9. Manual Ingestion Pipeline (HARNESS-10)

### 9.1 Overview

End-to-end pipeline for uploading PDF service manuals, converting them to structured markdown via marker-pdf, storing them in per-vehicle-model directories, and ingesting into pgvector for agent RAG retrieval.

### 9.2 Filesystem Structure

```
/app/data/manuals/           (Docker volume: diagnostic_api_manuals)
  uploads/                   Staging area for uploaded PDFs
  TRICITY-155/               Per-vehicle-model subdirectory
    MWS150A_Service_Manual.md
    images/MWS150A_Service_Manual/
  MWS-150-A/
    ...
  Generic/                   Fallback for undetected models
```

### 9.3 Database Schema

**Table: `manuals`** (Alembic migration `q1r2`)

| Column | Type | Description |
|--------|------|-------------|
| `id` | UUID PK | Primary key |
| `user_id` | UUID FK → users | Uploader |
| `filename` | String(500) | Original PDF filename |
| `file_hash` | String(64), unique | SHA-256 for dedup |
| `vehicle_model` | String(100) | Detected or user-provided |
| `status` | String(20) | `uploading` / `converting` / `ingested` / `failed` |
| `file_size_bytes` | Integer | Upload size |
| `page_count` | Integer | PDF page count |
| `section_count` | Integer | `##` heading count |
| `language` | String(20) | `en` / `zh-CN` |
| `converter` | String(100) | `marker-pdf` or `marker-pdf (LLM)` |
| `error_message` | Text | Failure details |
| `md_file_path` | String(500) | Relative path to output .md |
| `pdf_file_path` | String(500) | Relative path to source PDF |
| `chunk_count` | Integer | RagChunks ingested |
| `created_at` | DateTime | |
| `updated_at` | DateTime | |

### 9.4 API Endpoints

All under `/v2/manuals`, all require JWT auth.

| Method | Path | Description |
|--------|------|-------------|
| POST | `/upload` | Upload PDF, start background conversion (201) |
| GET | `/` | List manuals with filters (status, vehicle_model) |
| GET | `/{id}` | Manual detail + markdown content |
| DELETE | `/{id}` | Delete manual, files, and RAG chunks |
| GET | `/{id}/status` | Conversion status snapshot |

### 9.5 Background Pipeline

1. **Upload**: validate PDF magic bytes + size, compute SHA-256, save to `uploads/`
2. **Convert**: marker-pdf (GPU-serialized via `asyncio.Semaphore(1)`) → `.md` + images under `{vehicle_model}/`
3. **Ingest**: existing `process_file()` → chunk + embed → insert `rag_chunks`
4. **Recovery**: on startup, mark `status="converting"` rows as `"failed"`

### 9.6 Frontend

New page at `/manuals` (Next.js App Router):
- `ManualUploadForm`: drag-drop PDF upload with optional vehicle model input
- `ManualList`: table with status badges, auto-polls converting items every 5s
- `ManualViewer`: displays markdown content with metadata banner

Navigation: "Manuals" link in header (after "My Sessions").

— End of document —
