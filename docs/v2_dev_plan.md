# V2 Development Plan — Harness Engineering (v1.0)

**Agent-driven vehicle diagnosis via harness loop, tool registry, and graduated autonomy**

| Field | Value |
|-------|-------|
| **Architecture doc** | `docs/v2_design_doc.md` |
| **GitHub Issue** | #26 (discussion: From Context Engineering to Harness Engineering) |
| **Version** | v1.0 |
| **Last updated** | 2026-05-16 (HARNESS-18: drafted 25 bilingual golden candidates — 5 per bucket — for triage and promotion to mws150a.jsonl) |

## 1. Scope Boundary

### 1.1 In Scope

- Core harness loop (agent loop pattern, async generator)
- Tool registry with dispatch map + 5 agent-native tools (multimodal-capable)
- Session event log (new `harness_event_log` Postgres table)
- Context management (token budget tracking + 2-tier compaction)
- New API endpoint: `POST /v2/obd/{session_id}/diagnose/agent`
- SSE streaming with extended event types (`tool_call`, `tool_result`, `hypothesis`)
- Graduated autonomy router (rule-based tier classification)
- Frontend agent visualization (tool-call cards, iteration counter)
- Dependency injection for testable agent loop
- Tests for all new code (unit, integration, agent behavior)
- Alembic migration for new table + updated CHECK constraint
- Documentation updates (this plan + v2_design_doc.md)

### 1.2 Out of Scope

- Sub-agent orchestration (Tier 2 multi-subsystem — future HARNESS-09)
- Model fine-tuning / LoRA / SFT (V1 Phase 1.5, `design_doc.md` §11)
- Preference optimization (V1 Phase 2, `design_doc.md` §11.5)
- Real-time OBD streaming or live sensor data
- MCP server protocol (direct tool handlers sufficient for V2)
- Infrastructure changes (Docker, Postgres, Ollama, Nginx — all unchanged)
- Background/async agent tasks (future HARNESS-11)

## 2. Engineer Order and Dependencies

### 2.1 Critical Path

```
HARNESS-01 (tool registry + wrappers)
    │
    ▼
HARNESS-02 (core agent loop)
    │
    ├──────────────────┐
    ▼                  ▼
HARNESS-03          HARNESS-04
(session log)       (context mgmt)
    │                  │
    └────────┬─────────┘
             ▼
HARNESS-05 (API endpoint + SSE)
    │
    ├──────────────────┐
    ▼                  ▼
HARNESS-06          HARNESS-07
(graduated          (frontend
 autonomy)           agent view)
    │                  │
    └────────┬─────────┘
             ▼
HARNESS-08 (integration + E2E tests)
```

**Parallelizable**: HARNESS-03 and HARNESS-04 can be developed in parallel (both depend on HARNESS-02). HARNESS-06 and HARNESS-07 can be developed in parallel (both depend on HARNESS-05).

### 2.2 Definition of Done (applies to every ticket)

A ticket is DONE only if:
- Code merged with tests passing
- Documentation updated (v2_design_doc.md and this plan)
- Privacy boundary preserved: no tool returns raw sensor data
- V1 endpoints continue to work unchanged (regression-free)

## 3. Tickets

### 3.1 Phase 1: Core Harness (HARNESS-01 through HARNESS-05)

#### HARNESS‑01 — Tool Registry and Tool Wrappers ✅ DONE

Owner: AI Application Engineer
Depends on: none
Status: **DONE** — GitHub Issue #51

PROMPT (task ticket):
Title: HARNESS‑01 Implement tool registry with dispatch map and 7 diagnostic tool wrappers

Context:
The V2 harness architecture requires existing V1 pipeline functions to be accessible as tools through a universal `execute(name, input) → str` interface. The tool registry is the foundation that all other harness components depend on. Each tool wraps an existing function (or implements new logic) and returns text summaries only — never raw sensor arrays (privacy boundary).

Task:
Create the tool registry infrastructure and implement 7 diagnostic tools:

**Existing function wrappers (4):**
1. `get_pid_statistics` — wraps `extract_statistics()` from `obd_agent/statistics_extractor.py:212`
2. `detect_anomalies` — wraps `detect_anomalies()` from `obd_agent/anomaly_detector.py:529`
3. `generate_clues` — wraps `generate_clues()` from `obd_agent/clue_generator.py:552`
4. `search_manual` — wraps `retrieve_context()` from `diagnostic_api/app/rag/retrieve.py:115`

**New tools (3):**
5. `refine_search` — adaptive RAG with `exclude_doc_ids` support
6. `search_case_history` — query `DiagnosisHistory` for similar past cases
7. `get_session_context` — retrieve session's `parsed_summary_payload`

Requirements:

- `ToolRegistry` class with `register()`, `execute()`, and `schemas` property
- `ToolDefinition` dataclass with `name`, `description`, `input_schema`, `handler`
- Dispatch map pattern: adding a tool = one registration call, zero loop changes
- Input validation via Pydantic or JSON Schema
- All tool handlers return `str` (privacy invariant)
- Error handling: tool exceptions caught and returned as error strings
- OpenAI function-calling format for tool schemas (see `v2_design_doc.md` Appendix A)

Deliverables:

`diagnostic_api/app/harness/__init__.py`
`diagnostic_api/app/harness/tool_registry.py`
`diagnostic_api/app/harness_tools/__init__.py`
`diagnostic_api/app/harness_tools/obd_tools.py`
`diagnostic_api/app/harness_tools/rag_tools.py`
`diagnostic_api/app/harness_tools/history_tools.py`
`diagnostic_api/tests/harness/__init__.py`
`diagnostic_api/tests/harness/test_tool_registry.py`
`diagnostic_api/tests/harness/test_obd_tools.py`
`diagnostic_api/tests/harness/test_rag_tools.py`

Acceptance Criteria:

- `ToolRegistry` dispatches all 7 tools by name
- Unknown tool name returns error string (not exception)
- Each tool's output is `str` type (assert in tests)
- `schemas` property returns valid OpenAI function-calling format
- `get_pid_statistics` returns formatted stats matching `extract_statistics()` output
- `detect_anomalies` returns anomaly event descriptions
- `generate_clues` returns clue text with rule IDs
- `search_manual` returns RAG results with doc_id and similarity score
- `refine_search` excludes specified doc_ids from results
- `search_case_history` returns past diagnosis summaries (empty list if none)
- `get_session_context` returns formatted parsed_summary text
- Unit tests for each tool handler and registry dispatch

---

#### HARNESS‑02 — Core Agent Loop ✅ DONE

Owner: AI Application Engineer
Depends on: HARNESS-01
Status: **DONE** — GitHub Issue #52

PROMPT (task ticket):
Title: HARNESS‑02 Implement core agent loop as async generator with dependency injection

Context:
The agent loop is the heart of V2 — a `while True` loop that calls the LLM with tool schemas, executes tool calls, appends results to the conversation, and iterates until the LLM produces a final diagnosis. It must be implemented as a Python async generator (matching V1's SSE streaming pattern) and use dependency injection for testability. See `v2_design_doc.md` §4 for full design.

Task:
Implement `run_diagnosis_loop()` async generator and supporting infrastructure:

1. `HarnessDeps` dataclass for dependency injection (LLM client, tool registry, config)
2. `HarnessEvent` dataclass for typed event yields (tool_call, tool_result, hypothesis, token, done, error)
3. `HarnessConfig` with configurable `max_iterations`, `max_tokens`, `compact_threshold`, `timeout_seconds`
4. `run_diagnosis_loop()` async generator implementing the ReAct cycle
5. Initial context builder from `parsed_summary_payload`
6. System prompt assembly (dynamic, includes tool descriptions)

Requirements:

- Async generator yields `HarnessEvent` objects
- Max iteration guard (configurable, default 10)
- Graceful timeout handling (default 120s total)
- Partial diagnosis extraction if max iterations reached
- LLM called via OpenAI-compatible API (`chat.completions.create` with `tools=`)
- Tool calls dispatched through `ToolRegistry.execute()`
- Dependency injection: `HarnessDeps` allows mocked LLM in tests
- System prompt loaded from `harness_prompts.py`

Deliverables:

`diagnostic_api/app/harness/loop.py`
`diagnostic_api/app/harness/deps.py`
`diagnostic_api/app/harness/harness_prompts.py`
`diagnostic_api/tests/harness/test_loop.py`
`diagnostic_api/tests/harness/fixtures/` (directory for recorded LLM responses)

Acceptance Criteria:

- Agent loop calls LLM, dispatches tool calls, appends results, iterates
- Yields `HarnessEvent` for each tool_call, tool_result, and final done
- Stops when LLM returns `stop_reason=end_turn` (no more tool calls)
- Stops after `max_iterations` with partial diagnosis
- `HarnessDeps` injection allows test with mocked LLM client
- Tests use recorded LLM responses (deterministic replay)
- Golden-path test: mock LLM calls 3 tools then produces diagnosis
- Error test: mock LLM calls unknown tool; receives error; continues

---

#### HARNESS‑03 — Session Event Log ✅ DONE

Owner: AI Application Engineer
Depends on: HARNESS-02
Status: **DONE** — GitHub Issue #53

PROMPT (task ticket):
Title: HARNESS‑03 Create HarnessEventLog table and session event persistence

Context:
Every tool call, result, and reasoning step during an agent diagnosis session must be persisted for auditability, debugging, and future training data extraction. The event log is append-only and uses the same Postgres database as existing V1 tables. See `v2_design_doc.md` §6 for design and DDL.

Task:
1. Add `HarnessEventLog` SQLAlchemy model to `diagnostic_api/app/models_db.py`
2. Create `session_log.py` module with `emit_event()` and `get_session_events()` functions
3. Update `DiagnosisHistory.provider` CHECK constraint to include `"agent"`
4. Create Alembic migration
5. Integrate event emission into the agent loop (`loop.py`)

Requirements:

- Append-only: no UPDATE or DELETE operations on event log
- Event types: `session_start`, `tool_call`, `tool_result`, `hypothesis`, `context_compact`, `diagnosis_done`, `error`
- JSONB payload for flexible event data
- Composite index on `(session_id, created_at)` for chronological retrieval
- `get_session_events(session_id)` returns ordered list of events

Deliverables:

Updated `diagnostic_api/app/models_db.py` (add `HarnessEventLog`)
`diagnostic_api/app/harness/session_log.py`
New Alembic migration file
`diagnostic_api/tests/harness/test_session_log.py`

Acceptance Criteria:

- `HarnessEventLog` table created by Alembic migration
- `emit_event()` persists event with correct type and payload
- `get_session_events()` returns events ordered by `created_at`
- `DiagnosisHistory.provider` accepts `"agent"` value
- Migration applies cleanly on existing schema (forward-compatible)
- Migration is reversible (downgrade drops table, restores CHECK)
- Unit tests for emit, retrieve, and ordering

---

#### HARNESS‑04 — Context Management ✅

Owner: AI Application Engineer
Depends on: HARNESS-02
Status: **DONE** — GitHub Issue #54

PROMPT (task ticket):
Title: HARNESS‑04 Implement token budget tracking and 2-tier context compaction

Context:
Agent loops accumulate tool results that can exhaust the LLM's context window. V2 uses a 2-tier compaction strategy: (1) truncate individual tool results exceeding a per-result budget, (2) auto-compact older conversation turns when approaching the total token limit. See `v2_design_doc.md` §7 for design.

Task:
1. Token estimator (character-based approximation or tiktoken if available)
2. Per-tool-result truncation (`max_tool_result_tokens`, default 2000)
3. Conversation auto-compaction when exceeding `compact_threshold`
4. Compact strategy: keep system+user messages intact, summarize old tool interactions, keep recent 2 iterations
5. Integration point in agent loop (called between iterations)

Requirements:

- Token estimation must be fast (called every iteration)
- Truncation appends `"[truncated — N chars total]"` marker
- Auto-compact produces a summary message replacing old tool interactions
- System prompt and initial user message are never compacted
- Most recent 2 iterations are always preserved (LLM needs recent context)
- Configurable thresholds via `HarnessConfig`

Deliverables:

`diagnostic_api/app/harness/context.py`
`diagnostic_api/tests/harness/test_context.py`

Acceptance Criteria:

- Token estimation within 20% of actual tokenizer count
- Tool results exceeding budget are truncated with marker
- Auto-compact triggers at configured threshold
- Compacted messages are shorter than originals
- System prompt and initial user message survive compaction
- Recent 2 iterations survive compaction
- Tests verify truncation, compaction trigger, and message preservation

---

#### HARNESS‑05 — API Endpoint and SSE Streaming ✅ DONE

Owner: AI Application Engineer
Depends on: HARNESS-02, HARNESS-03
Status: **DONE** — GitHub Issue #55

PROMPT (task ticket):
Title: HARNESS‑05 Create /v2/obd/{session_id}/diagnose/agent endpoint with extended SSE events

Context:
The agent loop needs to be exposed as a FastAPI endpoint that streams events to the frontend via SSE. The endpoint follows the same pattern as V1's `/diagnose` endpoint (`StreamingResponse` with `text/event-stream`) but adds new event types for tool calls and results. V1 endpoints must remain unchanged. See `v2_design_doc.md` §9 for endpoint spec.

Task:
1. Create `harness/router.py` with the new endpoint
2. Wire the agent loop async generator to SSE `StreamingResponse`
3. Handle auth, session lookup, cached diagnosis, and force re-diagnosis
4. Store completed diagnosis in `DiagnosisHistory` with `provider="agent"`
5. Register router in `main.py`

Requirements:

- `POST /v2/obd/{session_id}/diagnose/agent` with query params: `force`, `locale`, `max_iterations`, `force_agent`, `force_oneshot`
- Auth: `get_current_user` dependency (same as V1)
- SSE events: `status`, `tool_call`, `tool_result`, `hypothesis`, `token`, `done`, `error`
- `done` event includes `diagnosis_history_id`, `iterations`, `tools_called`, `autonomy_tier`
- Diagnosis stored in `DiagnosisHistory` and `OBDAnalysisSession.diagnosis_text`
- V1 endpoints (`/diagnose`, `/diagnose/premium`) continue working unchanged
- Same 2KB padding prefix as V1 for browser buffer flush

Deliverables:

`diagnostic_api/app/harness/router.py`
Updated `diagnostic_api/app/main.py` (register harness router)
`diagnostic_api/tests/harness/test_router.py`

Acceptance Criteria:

- Endpoint returns `StreamingResponse` with `text/event-stream`
- SSE events include `tool_call` and `tool_result` types
- `done` event contains `diagnosis_history_id` UUID
- `DiagnosisHistory` row created with `provider="agent"`
- Auth required (401 without token)
- Session ownership enforced (404 for other user's session)
- `force=false` returns cached diagnosis if exists
- V1 `/diagnose` and `/diagnose/premium` endpoints still work (regression test)

---

### 3.2 Phase 2: Orchestration and Frontend (HARNESS-06 through HARNESS-08)

#### HARNESS‑06 — Graduated Autonomy Router ✅ DONE

Owner: AI Application Engineer
Depends on: HARNESS-05

Status: **DONE** — GitHub Issue #56

PROMPT (task ticket):
Title: HARNESS‑06 Implement complexity classifier and graduated autonomy routing

Context:
Not all diagnoses benefit from the full agent loop. Simple single-DTC cases should use the fast V1 one-shot path (2-5s, ~$0.01), while complex multi-fault cases should use the agent loop (10-60s, ~$0.05-0.15). A rule-based complexity classifier analyzes the `parsed_summary_payload` to determine the appropriate diagnosis tier. See `v2_design_doc.md` §8 for tier definitions.

Task:
1. `classify_complexity(parsed_summary) → int` function (Tier 0-3)
2. Unified routing logic that dispatches to V1 one-shot or V2 agent based on tier
3. Override support via query params (`force_agent`, `force_oneshot`)
4. Integration into the agent endpoint (or a new unified endpoint)

Requirements:

- Tier 0 (simple): single DTC, moderate severity, ≤3 clues → V1 one-shot
- Tier 1 (moderate): multiple DTCs or high severity → agent loop 1-5 iterations
- Tier 2 (complex): many DTCs or critical severity → full agent (future: sub-agents)
- Tier 3 (follow-up): has prior diagnosis history → agent + case history tools
- `force_agent=true` overrides tier 0 to use agent
- `force_oneshot=true` overrides any tier to use V1 one-shot
- Classification is deterministic (same parsed_summary always yields same tier)

Deliverables:

`diagnostic_api/app/harness/autonomy.py`
`diagnostic_api/tests/harness/test_autonomy.py`

Acceptance Criteria:

- Single DTC + moderate severity → Tier 0
- 3 DTCs + high severity → Tier 1
- 5 DTCs + critical severity → Tier 2
- Session with prior diagnosis history → Tier 3
- `force_agent=true` escalates Tier 0 to agent mode
- `force_oneshot=true` forces V1 one-shot regardless of tier
- Tests cover all 4 tiers with representative parsed_summary fixtures

---

#### HARNESS‑07 — Frontend Agent Visualization ✅ DONE

Owner: Frontend Engineer
Depends on: HARNESS-05

Status: **DONE** — GitHub Issue #57

PROMPT (task ticket):
Title: HARNESS‑07 Add agent diagnosis streaming view with tool-call visualization

Context:
The frontend SSE handler needs to render new V2 event types (`tool_call`, `tool_result`, `hypothesis`) during agent diagnosis streaming. Users should see the agent's investigation process in real-time — which tools are being called, what results are returned, and how the diagnosis evolves. V1 streaming (plain text tokens) must continue to work for one-shot diagnoses. See `v2_design_doc.md` §11 for UI design.

Task:
1. `AgentDiagnosisView.tsx` — renders agent streaming with tool-call cards
2. `ToolCallCard.tsx` — collapsible card showing tool name, input, result, duration
3. Update SSE handler to recognize V2 event types
4. Autonomy tier indicator (shows which tier was selected)
5. Iteration counter during agent execution
6. Graceful fallback: if no `tool_call` events arrive, render as V1 text stream

Requirements:

- Tool calls shown in real-time as collapsible cards during streaming
- Cards show: tool name (bold), input parameters (code), result text (expandable)
- Final diagnosis rendered below tool cards (same format as V1)
- Iteration counter: "Iteration 2/10" during agent execution
- Autonomy tier badge: "Agent Mode (Tier 1: Multiple DTCs detected)"
- Toggle to force agent or one-shot mode (optional UI control)
- i18n: all new strings in EN, zh-CN, zh-TW locale files
- V1 streaming (no tool_call events) renders identically to current UI

Deliverables:

`obd-ui/src/components/AgentDiagnosisView.tsx`
`obd-ui/src/components/ToolCallCard.tsx`
Updated SSE handler in analysis page
Updated locale files (EN, zh-CN, zh-TW)

Acceptance Criteria:

- V2 agent stream shows tool-call cards in real-time
- Cards are collapsible (collapsed by default after streaming completes)
- Final diagnosis text renders below tool cards
- V1 one-shot stream renders identically to current UI (no regression)
- Iteration counter updates during agent execution
- Autonomy tier displayed when agent mode is active
- All new strings translated in 3 locales

---

#### HARNESS‑08 — Integration and E2E Tests ✅ DONE

Owner: AI Application Engineer
Depends on: HARNESS-05, HARNESS-06

Status: **DONE** — GitHub Issue #58

PROMPT (task ticket):
Title: HARNESS‑08 Create integration test suite and E2E golden-path tests

Context:
Agent behavior is inherently non-deterministic — the same input may produce different tool-call sequences depending on the LLM's reasoning. Testing requires: (1) deterministic integration tests with mocked LLM using recorded responses, (2) golden-path E2E tests that verify the full flow works end-to-end. See `v2_design_doc.md` §12 for testing strategy.

Task:
1. Integration tests with fully mocked LLM (recorded tool-call sequences)
2. Golden-path test: upload OBD → agent diagnosis → verify events and stored result
3. Fallback test: agent loop failure → V1 one-shot succeeds
4. Event log completeness test: all events persisted in correct order
5. Graduated autonomy test: correct tier routing for different inputs
6. Record LLM response fixtures for deterministic replay

Requirements:

- Integration tests are fully deterministic (no real LLM calls)
- Mocked LLM uses `HarnessDeps` dependency injection
- Recorded responses stored in `tests/harness/fixtures/`
- E2E tests can optionally run with real premium model (not in CI)
- All tests follow Arrange / Act / Assert pattern
- Every test function has a docstring explaining intent

Deliverables:

`diagnostic_api/tests/harness/test_integration.py`
`diagnostic_api/tests/harness/test_e2e_agent.py`
`diagnostic_api/tests/harness/fixtures/golden_path_responses.json`
`diagnostic_api/tests/harness/fixtures/fallback_responses.json`

Acceptance Criteria:

- Golden-path test: agent calls ≥2 tools, produces diagnosis, stores in DB
- Fallback test: agent loop raises exception, V1 one-shot diagnosis returned
- Event log test: `HarnessEventLog` contains `session_start`, `tool_call`, `tool_result`, `diagnosis_done`
- Autonomy test: Tier 0 input routes to one-shot, Tier 1 input routes to agent
- All integration tests pass without network access
- Tests run in <10 seconds (no real LLM calls)

---

## 4. Future Tickets (Post-MVP, Out of Scope for V2.0)

These tickets are logged for planning purposes but will not be implemented in the initial V2 release.

#### HARNESS‑09 — Sub-agent per Subsystem (Tier 2)

Depends on: HARNESS-08
Scope: Spawn isolated sub-agents for multi-subsystem faults. Each sub-agent investigates one subsystem (engine, transmission, electrical) with a fresh context window. Parent agent synthesizes sub-agent findings.
Reference: `v2_design_doc.md` §8.3, learning notes S04.

#### HARNESS‑10 — Manual Ingestion Pipeline 🔧 IN PROGRESS

Depends on: none (standalone)
Status: **IN PROGRESS** — GitHub Issue #70

Scope: End-to-end pipeline for service manual PDF upload, conversion (marker-pdf), per-vehicle-model filesystem storage, and pgvector RAG ingestion. New dashboard page in obd-ui for uploading, viewing, and managing manuals. Background conversion with status polling.

Key files:
- `app/models_db.py` — `Manual` model
- `app/services/manual_pipeline.py` — background conversion + ingestion
- `app/api/v2/endpoints/manuals.py` — CRUD endpoints under `/v2/manuals`
- `scripts/marker_convert.py` — refactored with `ConversionResult` and `vehicle_model_subdir`
- `obd-ui/src/app/manuals/page.tsx` — frontend dashboard
- Alembic migration `q1r2` — `manuals` table

Acceptance criteria:
- [x] Manual model + migration
- [x] Upload, list, get, delete, status endpoints
- [x] Background marker-pdf conversion with GPU semaphore
- [x] Per-vehicle-model directory structure
- [x] RAG ingestion via existing `process_file()`
- [x] Frontend: upload form, manual list with status badges, manual viewer
- [x] i18n (EN, zh-CN, zh-TW)
- [x] 16 unit tests passing
- [ ] Integration test with real PDF conversion (requires marker-pdf)
- [ ] Deploy to server

#### HARNESS‑11 — Multimodal Manual Navigation Tools ✅ DONE

Depends on: HARNESS-09, HARNESS-10
GitHub Issue: #71
Status: **DONE** (2026-04-13)

Scope: 3 filesystem navigation tools (`list_manuals`, `get_manual_toc`, `read_manual_section`) that complement `search_manual` with structural navigation. Multimodal infrastructure enabling tool handlers to return `List[ContentBlock]` (interleaved text + base64 images). Context management updated for multimodal token estimation, truncation, and compaction. Design informed by [Anthropic tool design guide](https://www.anthropic.com/engineering/writing-tools-for-agents): 3 tools (not 1) because each maps to a distinct cognitive step at different token costs. Images mandatory because service manuals contain wiring diagrams, exploded views, and diagnostic flowcharts.

Files created: `harness_tools/manual_tools.py`, `harness_tools/manual_fs.py`, `tests/harness/test_manual_tools.py`, `tests/harness/test_manual_fs.py`, `tests/harness/test_multimodal_loop.py`.
Files modified: `harness/tool_registry.py`, `harness/loop.py`, `harness/context.py`, `harness/harness_prompts.py`, `harness_tools/input_models.py`.
Tests: 70 new (22 infrastructure + 31 utilities + 17 handlers), 242 total harness tests passing.

#### HARNESS‑12 — Background Agent Tasks

Depends on: HARNESS-08
Scope: Long-running agent sessions that execute in the background. Notification when complete. For multi-vehicle fleet analysis or overnight batch diagnosis.
Reference: Learning notes S08.

#### HARNESS‑13 — Case Library Tool (Feedback-Driven Learning)

Depends on: HARNESS-08
Scope: Use stored expert feedback to build a case library. Tool retrieves past cases where feedback was positive (helpful=true, rating≥4) and includes the expert-validated root cause.

#### HARNESS‑14 — Manual-Agent Evaluation Suite 🔧 IN PROGRESS

Depends on: HARNESS-11
GitHub Issue: #73
Status: **IN PROGRESS** — Phase 5 baseline run 2026-04-23: 3/10 passed (0.534 mean overall). See `docs/harness_14_phase5_baseline.md` for failure patterns + next iterations.

Scope: Standalone LLM-as-judge evaluation suite that measures how well a restricted manual-search sub-agent uses the 4 manual navigation tools (`list_manuals`, `get_manual_toc`, `read_manual_section`, `search_manual`) to answer diagnostic inquiries. Grades each run with `z-ai/glm-5.1` via OpenRouter against a human-reviewed frozen golden set stored under `tests/harness/evals/golden/v1/`. Isolates tool-use quality from OBD analysis quality. Design informed by [Anthropic guide: develop your tests](https://platform.claude.com/docs/en/test-and-evaluate/develop-tests).

Key design decisions (locked 2026-04-23):
- **Judge model**: `z-ai/glm-5.1` via OpenRouter (HK-accessible; Claude/OpenAI/Gemini geo-blocked per #23). Temperature 0, `response_format={"type": "json_object"}`, Pydantic-validated + retry-once on parse failure.
- **Agent under test (primary)**: local `qwen3.5:27b-q8_0` (what ships). **Ceiling comparison (phase 5)**: `z-ai/glm-5.1` or `moonshotai/kimi-k2`.
- **Rubric, not yes/no**: 5 dimensions (`section_match`, `fact_recall`, `hallucination`, `citation_present`, `trajectory_ok`) + weighted `overall`. Trajectory is reported but not enforced in the pass threshold.
- **Immutable goldens**: `golden/v1/` is append-only closed once frozen. Corrections bump to `v2/`. Prevents silent eval-set drift.
- **Grounded golden generation** (phase 3): Claude reads a specific manual section and emits one `(question, summary, citations, must_contain)` tuple; human reviewer accepts/edits/rejects before promotion to `v1/`.

Known limitation: only `MWS150A_Service_Manual` is currently ingested. Cross-manual adversarial scenarios (wrong `vehicle_model` filter) are deferred until a second manual becomes available — `v2/` will add them. Taxonomy adjusted: adversarial category uses intra-manual edge cases (fake DTC `P9999`, out-of-scope query, typo'd slug, multi-section answer).

Phasing:
1. **Scaffolding** ✅ DONE — schemas, runner stub, judge stub, pytest plumbing, 3 dummy golden entries, `--run-eval` CLI flag. No LLM calls. Verifies end-to-end pipeline.
2. **Real judge + manual agent** ✅ DONE
   - Commit 2 (GLM 5.1 judge) ✅ DONE — judge_prompts.py, real judge.py with retry + JSON mode, 21 unit tests, `--mock-judge` flag for plumbing.
   - Commit 3 (manual agent ReAct loop) ✅ DONE — restricted 4-tool loop (`app/harness_agents/`), structured output parser with markdown-fence tolerance, raw-section capture, 33 unit tests, `--mock-agent` flag for plumbing.
3. **Generator + reviewer scripts** ✅ DONE (2026-04-23) — `scripts/generate_golden_candidates.py` (grounded DeepSeek V3.2 generation with CJK-aware whitespace grounding, per-category section filtering, adversarial branch, dedup) and `scripts/review_golden_candidates.py` (interactive TUI). 55 unit tests. Ran against real MWS150-A Chinese manual on PolyU server; produced 44 candidates across 5 categories; human-reviewed; 10 strongest approved and committed to `v1/mws150a.jsonl`. v1 is under the 30-entry taxonomy target — prioritised quality over quantity for first freeze.
4. **Expand to 30 entries** — fill taxonomy (DTC 8 / Symptom 6 / Component 6 / Image 4 / Adversarial 6).
5. **Baseline + iterate** — run against local Qwen; read failures; tune `harness_prompts.py`; optional ceiling run (`glm-5.1` or `kimi-k2` as agent).

Pre-requisite config change (for phase 2+): add `z-ai/glm-5.1` to server `.env` `PREMIUM_LLM_CURATED_MODELS`.

Files (Phase 1): `tests/harness/evals/schemas.py`, `tests/harness/evals/runner.py`, `tests/harness/evals/judge.py`, `tests/harness/evals/conftest.py`, `tests/harness/evals/test_manual_agent_eval.py`, `tests/harness/evals/golden/v1/mws150a.jsonl`, `tests/harness/evals/golden/README.md`, `tests/harness/evals/reports/.gitignore`. Modified: `tests/conftest.py` (registered `eval` marker + `--run-eval` CLI flag + `pytest_collection_modifyitems` skip behavior).

## 5. Notes

### What this plan deliberately avoids

- **Over-engineering the first iteration**: V2 starts with a single agent loop and 7 tools. Sub-agents, skill loading, and background tasks are future tickets.
- **Replacing V1 prematurely**: V1 one-shot endpoints remain the default for simple cases. V2 agent mode is an additional option, not a replacement.
- **Speculative tool design**: Only tools that wrap existing functions or have clear implementation paths are included. Speculative tools (e.g., "run Mode 06 test") require hardware integration not currently available.

### Changelog

| Date | Version | Changes |
|------|---------|---------|
| 2026-04-10 | v1.0 | Initial V2 dev plan. 8 tickets (HARNESS-01 through HARNESS-08) across 2 phases. 4 future tickets (HARNESS-09 through HARNESS-12). Scope: core harness loop, 7 tools, session event log, context management, API endpoint, graduated autonomy, frontend visualization, integration tests. GitHub Issue #26. |
| 2026-04-10 | v1.1 | HARNESS-01 implemented (GitHub Issue #51). Tool registry (`ToolRegistry`, `ToolDefinition`) with dispatch map and 7 diagnostic tool wrappers. OBD tools read from `result_payload` JSONB (no re-run). 27 unit tests passing. Files: `harness/tool_registry.py`, `harness_tools/{obd,rag,history}_tools.py`. |
| 2026-04-10 | v1.2 | HARNESS-02 implemented (GitHub Issue #52). Core agent loop (`run_diagnosis_loop`) as async generator with DI. `HarnessDeps` container with `LLMClient` protocol, `OpenAILLMClient` adapter, `HarnessConfig`. Dynamic system prompt via `harness_prompts.py`. ReAct cycle with max-iteration guard, timeout handling, partial diagnosis extraction. 19 unit tests (golden-path, error recovery, budget limits, message history). Files: `harness/{deps,loop,harness_prompts}.py`, `tests/harness/test_loop.py`. |
| 2026-04-10 | v1.3 | HARNESS-03 implemented (GitHub Issue #53). `HarnessEventLog` model in `models_db.py`. `session_log.py` with `emit_event()`/`get_session_events()` (async via `run_in_executor`). Agent loop emits events at each phase (session_start, tool_call, tool_result, diagnosis_done, error). `DiagnosisHistory.provider` CHECK extended to accept `"agent"`. `EventType` Literal extended with `session_start`, `hypothesis`, `context_compact`, `diagnosis_done`. Alembic migration `p9q0`. 9 unit tests. Updated `alembic/env.py` imports. Files: `harness/session_log.py`, `models_db.py`, `harness/deps.py`, `harness/loop.py`, `alembic/versions/p9q0_add_harness_event_log.py`, `tests/harness/test_session_log.py`. |
| 2026-04-10 | v1.4 | HARNESS-04 implemented (GitHub Issue #54). 2-tier context management: `context.py` with `estimate_tokens()` (char/4 approximation), `truncate_tool_result()` (Tier 1 per-result truncation), `maybe_compact()` (Tier 2 auto-compaction with iteration-boundary detection). `HarnessConfig.max_tool_result_tokens` added (default 2000). `compact_threshold` docstring updated to "estimated token count". Agent loop integrates truncation after each tool execution and compaction between iterations. Emits `context_compact` event on compaction. 28 unit tests (token estimation, truncation, iteration identification, summarization, compaction preservation). Files: `harness/context.py`, `harness/deps.py`, `harness/loop.py`, `tests/harness/test_context.py`. |
| 2026-04-10 | v1.5 | HARNESS-05 implemented (GitHub Issue #55). `harness/router.py` with `POST /v2/obd/{session_id}/diagnose/agent`. Wires `run_diagnosis_loop()` to `StreamingResponse` with `text/event-stream`. Auth via `get_current_user`, session ownership check, cached diagnosis (force=false), 2KB padding prefix. Stores result in `DiagnosisHistory` with `provider="agent"` and updates `OBDAnalysisSession.diagnosis_text`. SSE event mapping: `session_start`→`status`, `tool_call`/`tool_result` pass-through, `context_compact`→`status`, `done` enriched with `diagnosis_history_id`/`iterations`/`tools_called`/`autonomy_tier`. Query params: `force`, `locale`, `max_iterations`, `force_agent`, `force_oneshot` (last two reserved for HARNESS-06). Registered in `main.py`. 12 unit tests (auth, cache, SSE format, done event, tool events, error handling, V1 regression). Files: `harness/router.py`, `main.py`, `tests/harness/test_router.py`. |
| 2026-04-10 | v1.6 | HARNESS-06 implemented (GitHub Issue #56). Graduated autonomy router: `autonomy.py` with `classify_complexity()` (Tier 0–3 deterministic classification), `apply_overrides()` (`force_agent`/`force_oneshot`), `AutonomyDecision` dataclass. Helpers: `_count_dtcs()` (regex DTC extraction + dedup), `_max_severity()` (keyword-based severity from anomaly text), `_count_clues()` (STAT/RULE tags or separator counting). Integrated into `router.py`: queries `DiagnosisHistory` for prior diagnosis (Tier 3 follow-up), `suggested_max_iterations` drives agent budget, `done` SSE event now emits real `autonomy_tier` + `autonomy_strategy`. `force_oneshot` takes precedence over `force_agent` (safety-first). Router test suite updated with autonomy mocks. 44 unit tests (8 DTC counting, 8 severity, 8 clues, 12 classification, 8 overrides). Files: `harness/autonomy.py`, `tests/harness/test_autonomy.py`, updated `harness/router.py` and `tests/harness/test_router.py`. |
| 2026-04-12 | v1.8 | HARNESS-08 implemented (GitHub Issue #58). Integration and E2E tests: `test_integration.py` (7 tests: golden-path loop with mocked LLM, event log completeness, iteration monotonicity, Tier 0→oneshot routing, Tier 1→agent routing, agent-to-V1 fallback, double-failure resilience), `test_e2e_agent.py` (6 tests: full HTTP golden-path stream, diagnosis history storage, cache behavior, force bypass, fallback E2E, optional real-LLM test). JSON fixtures: `golden_path_responses.json` (4 LLM responses: get_session_context→detect_anomalies+search_manual→generate_clues→diagnosis), `fallback_responses.json` (agent error + V1 tokens). Fixture loader: `fixtures/__init__.py` with `load_llm_responses()` and `load_fallback_fixture()`. New feature: agent-to-V1 fallback in `router.py` — when agent loop raises, emits error SSE event then falls back to `_oneshot_stream()` with `skip_padding=True`. Added `e2e_real_llm` pytest marker in `conftest.py`. Also marked HARNESS-06 as DONE. All 182 harness tests pass (12 new + 1 skipped real-LLM). |
| 2026-04-10 | v1.7 | HARNESS-07 implemented (GitHub Issue #57). Frontend agent visualization: `AgentDiagnosisView.tsx` (main agent streaming view with state machine), `ToolCallCard.tsx` (collapsible card per tool invocation with name/input/output/duration), `IterationProgress.tsx` (iteration counter + autonomy tier badge). Extended `api.ts` with `streamAgentSSE()` and `streamAgentDiagnosis()` supporting V2 event types (`tool_call`, `tool_result`, `session_start`). Agent SSE callbacks: `onToolCall`, `onToolResult`, `onDone`, `onSessionStart`, etc. Tool invocations paired by name+iteration in UI state. Tier 0 fallback: token-by-token text (same as V1). "Agent AI" sub-tab added to `AnalysisLayout.tsx` (visible when premium enabled). i18n: ~25 new strings in `agent.*` namespace across EN, zh-CN, zh-TW. Types: `AgentToolCallEvent`, `AgentToolResultEvent`, `AgentDoneEvent`, `ToolInvocation` in `types.ts`. V1 `AIDiagnosisView.tsx` untouched. Build passes. |
| 2026-04-12 | v2.0 | HARNESS-10 in progress (GitHub Issue #70). Manual ingestion pipeline: `Manual` DB model + Alembic `q1r2` migration, `manual_pipeline.py` background service (marker-pdf conversion + RAG ingestion with GPU semaphore), 5 API endpoints under `/v2/manuals` (upload, list, get, delete, status), refactored `marker_convert.py` (ConversionResult + vehicle_model_subdir), per-vehicle-model directory structure. Frontend: `/manuals` page with ManualUploadForm (drag-drop PDF), ManualList (status badges, auto-polling), ManualViewer. Nav link in HeaderAuth. i18n (EN, zh-CN, zh-TW). Config: `manual_storage_path`, `manual_max_file_size_bytes`, `manual_use_llm`. Startup recovery for interrupted conversions. 16 unit tests passing. |
| 2026-04-12 | v1.9 | HARNESS-09: Toolset redesign (GitHub Issue #69). Replaced 7 V1-wrapper tools with 2 agent-native tools: `read_obd_data` (parameterized OBD log reader with overview + signal query modes) and `search_manual` (redesigned with vehicle_model filter + exclude_chunk_ids). Removed: `get_pid_statistics`, `detect_anomalies`, `generate_clues`, `get_session_context`, `refine_search`, `search_case_history`. New: `obd_data_tools.py` reads raw TSV files via `log_parser.parse_log_file()`. `retrieve.py` now accepts `vehicle_model` and `exclude_chunk_ids` filters. Agent loop auto-injects `_session_id` so LLM never passes UUIDs. System prompt rewritten as flexible investigation guide (no rigid 7-step script). User message simplified to vehicle + time range + DTCs only. 172 tests pass (1 pre-existing DB-env failure). Files: created `harness_tools/obd_data_tools.py`; rewrote `harness_tools/rag_tools.py`, `harness_tools/input_models.py`, `harness/harness_prompts.py`; modified `harness/loop.py`, `harness/tool_registry.py`, `app/rag/retrieve.py`; deleted `harness_tools/obd_tools.py`, `harness_tools/history_tools.py`. |
| 2026-04-13 | v2.1 | HARNESS-11: Multimodal manual navigation tools (GitHub Issue #71). 3 new filesystem tools: `list_manuals` (discover manuals, filter by vehicle model), `get_manual_toc` (heading tree with slugs + DTC quick index), `read_manual_section` (full section with base64 images). Multimodal infrastructure: `ToolOutput = str | List[ContentBlock]`, `ToolResult.output` accepts multimodal, `_make_tool_message()` passes list content to OpenAI format, `_extract_text_for_sse()` strips images from SSE. Context: `estimate_content_tokens()` for multimodal (images at 1000 tokens), `truncate_tool_result()` preserves images while truncating text, `_summarize_iteration()` drops images during compaction. Shared utils: `manual_fs.py` (`slugify`, `parse_frontmatter`, `parse_heading_tree`, `extract_section`, `find_closest_slug`, `resolve_image_refs`, `load_image_as_content_block`, `build_multimodal_section`). Security: path traversal protection, 5 MB image cap. System prompt updated with 5 tool descriptions. 70 new tests (22 infra + 31 utils + 17 handlers), 242 total harness tests pass. Files: created `harness_tools/manual_tools.py`, `harness_tools/manual_fs.py`; modified `harness/tool_registry.py`, `harness/loop.py`, `harness/context.py`, `harness/harness_prompts.py`, `harness_tools/input_models.py`. |
| 2026-05-16 | v2.17 | HARNESS-18: drafted 25 bilingual golden candidates to scale the eval set from 5 → 30 entries (GitHub Issue #84). **Why now**: the 5 existing live entries (one per bucket) demoed the dashboard at this week's team meeting and just finished internal review — `lookup-001`, `dtc-001`, and `adversarial-001` accepted 5/5/5/5; `cross-001` received `needs_revision` (realism=1, reviewer felt the maintenance-interval framing was "too textbook"); `image-001` received `needs_revision` and was already rewritten per #89. Five entries is too thin for an agent-vs-RAG comparison (#74) or for a workshop expert to grade meaningfully. **Output**: `tests/harness/evals/golden/v2/candidates/batch_harness18.jsonl` — 25 entries (5 per bucket: lookup-002..006, procedural-002..006, cross-002..006, image-002..006, adversarial-002..006). Every entry is bilingual (`question` + `question_zh` + `golden_summary` + `golden_summary_zh`) and authored by direct read against `source/MWS-150-A.md`. All 25 validate against `tests.harness.evals.schemas.GoldenEntry`; all 79 verbatim citation quotes hit the source (whitespace-exact substring match including Marker's stray inter-character whitespace at line wraps). **Bucket-specific authoring shifts incorporating expert review**: (a) `cross-002`..`cross-006` deliberately avoid the maintenance-interval style that drew the `cross-001` "too textbook" complaint — every cross-section question now starts with a realistic workshop scenario ("customer brings in bike with X symptom") and the two-slug retrieval is motivated by the diagnostic, not by a manual-vs-spec lookup; (b) `image-002`..`image-006` follow the post-#89 image-001 pattern — describe physical positions in prose, attach `figure_image_paths` per citation, never rely solely on figure-local letter callouts (a/b/c/d); (c) `adversarial-002`..`adversarial-006` test plausible-but-false premises (manual transmission, turbocharger, carburetor, two-stroke premix, fake DTC P9999) — each carries the top-level `manual_id` fallback for golden_sync, empty `golden_citations`, and a `golden_summary` that explicitly corrects the premise plus directs the technician to what the bike actually has. **Distribution rebalance**: original plan was 8/8/6/4/4 (lookup/procedural/cross/image/adversarial); HARNESS-18 flattens to **6/6/6/6/6** so each bucket has enough surface for inter-rater agreement work. `section_plan.md` Status header and distribution table updated to reflect the new totals and the candidate counts. **Process notes**: candidates land in `candidates/` (not promoted to `mws150a.jsonl`) so they pass through `scripts/review_golden_candidates.py` team triage before going live on the dashboard — consistent with the workflow in #84. The candidates directory is skipped by `golden_sync.py`, so the live dashboard / DB is unaffected until promotion. **Pending** (out-of-scope for this commit): team triage TUI pass, append accepted entries to `mws150a.jsonl`, redeploy diagnostic-api, internal team grading via dashboard, hand-off to workshop expert. Files created: `tests/harness/evals/golden/v2/candidates/batch_harness18.jsonl`; modified: `tests/harness/evals/golden/v2/section_plan.md`. |
| 2026-05-16 | v2.16 | HARNESS-17: removed delete capability from golden reviews — strict append-only / last-write-wins semantics. **Motivation**: the dashboard is a shared team review log; if reviewer A's "accept" badge can be deleted by reviewer A (or anyone) before reviewer B sees it, the audit trail of how the entry's grade evolved disappears. The `last-write-wins` rule already gives reviewers a clean revision path (post a new review → it becomes the new headline) without needing destructive delete. **Backend** (`app/api/v2/endpoints/goldens.py`): `DELETE /v2/goldens/reviews/{review_id}` endpoint removed entirely; the slot left in place as a comment block documenting the policy and the SQL-level override for true administrative erasure. **Frontend** (`obd-ui/src/components/goldens/TeamFeedbackList.tsx`): `FeedbackCard` no longer renders the per-row Delete button or its confirmation dialog; `handleDelete`, `deleting`/`deleteError` state, and `Trash2` icon import removed; `onDeleted` callback prop dropped; `localBump` refetch state in the parent `<TeamFeedbackList>` removed (parent `refreshKey` from new submits is the only refetch trigger now). API client `deleteReview()` deleted from `obd-ui/src/lib/api.ts`; `submitGoldenReview()` docstring updated to reflect the new "append-only AND immutable" semantics. **i18n**: `goldens.teamFeedback.delete` / `deleteTitle` / `deleteConfirm` removed from EN / zh-CN / zh-TW. **Out-of-scope** (deliberately not done): database constraint to prevent row deletion at the schema level — direct DB access remains the legitimate path for admin moderation / GDPR-style erasure, and leaving that path in place keeps an out-of-band audit trail. Files modified: `diagnostic_api/app/api/v2/endpoints/goldens.py`, `obd-ui/src/components/goldens/TeamFeedbackList.tsx`, `obd-ui/src/lib/api.ts`, `obd-ui/src/locales/{en, zh-CN, zh-TW}.json`. |
| 2026-05-16 | v2.15 | HARNESS-17: image-001 figure embedding + citation accuracy from Towngas technician feedback (GitHub Issue #89). **Problem**: Towngas reviewer reported that the image-required golden's citation was effectively a hyperlink hop — the textual quote referenced figure-local labels (`"I" mark "c"` aligns with `mark "d"`) that are meaningless without the diagram, forcing the reviewer to click through to the manual to verify the answer. Investigation also surfaced a citation accuracy bug: the AC-generator-rotor / right-crankcase alignment quote was attributed to slug `汽門間隙的調整` but actually lives in the cylinder-head chapter (`汽缸頭的拆卸` + `汽缸頭的安裝`). **Fix (4 parts)**: (1) `image-001` rewritten in `tests/harness/evals/golden/v2/mws150a.jsonl` — `golden_summary` (EN + zh) now describes the two alignments by physical position (camshaft-sprocket / stopper-plate; AC-generator-rotor / right-crankcase) and explicitly notes that figure-local letters (a/b/c/d) are NOT universal identifiers, so the prose is self-contained without the diagram; (2) `golden_citations` corrected — camshaft-side alignment stays under `汽門間隙的調整` (one quote, one figure), crankshaft-side alignment moves to `汽缸頭的拆卸` + `汽缸頭的安裝` where the quotes actually live; (3) Each citation gained a new optional `figure_image_paths: List[str]` field listing manual-relative image paths; (4) `pitfall_directives` updated — drop the swapped-letter-labels directive (irrelevant now that the answer doesn't depend on letters), add a directive against attributing the crankshaft-side alignment to `汽門間隙的調整`. **Backend**: `GoldenCitationOut` Pydantic schema in `app/api/v2/endpoints/goldens.py` gained `figure_image_paths: List[str] = []`; `_coerce_citations()` passes it through from JSONB. `GoldenEntryDetail` gained `md_file_path: Optional[str]` (looked up by joining `Manual` on `entry.manual_id`; tolerates non-UUID sentinel manual_ids and missing Manual rows by returning None) so the frontend can compute the same `imageBaseUrl` the `ManualViewer` uses. **Frontend**: `<QuestionCard>` now renders embedded figures inline beneath each citation's quote (2-col grid on `sm:`, lazy-loaded, click-to-open in new tab) using a `resolveImageUrl()` helper that mirrors the ManualViewer's rewrite logic — figures load from the nginx `/manuals/data/` alias the same way they do in the manual viewer. Citation row layout changed from `<a>`-wraps-everything to `<a>` (text only) + sibling `<div>` (figures), avoiding nested anchors. `figuresEmbeddedNote` footer added to clarify that the figures are inline. **i18n**: `goldens.questionCard.figure`, `openFigure`, `figuresEmbeddedNote` added to EN / zh-CN / zh-TW. **Types**: `GoldenCitation.figure_image_paths?: string[]` and `GoldenEntryDetail.md_file_path: string \| null` added in `obd-ui/src/lib/types.ts`. **cross-001 deferred**: the Towngas claim that "maintenance interval is not fixed" conflicts with the manual's explicit `每 12,000 km` for valve clearance. Posted clarification request on issue #89 ([comment](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/89#issuecomment-4466706874)) asking the expert to confirm whether they meant (a) the manual is wrong, (b) variation with service conditions, or (c) a time-based requirement to add alongside the mileage. JSONL not changed yet. Files modified: `tests/harness/evals/golden/v2/mws150a.jsonl`, `app/api/v2/endpoints/goldens.py`, `obd-ui/src/components/goldens/QuestionCard.tsx`, `obd-ui/src/lib/types.ts`, `obd-ui/src/locales/{en, zh-CN, zh-TW}.json`. |
| 2026-05-10 | v2.14 | HARNESS-17 Phase 2.1: append-only team-shared reviews (GitHub Issue #82). **Problem**: Phase 2 stored reviews under a `(golden_entry_id, reviewer_id)` unique constraint, so re-submitting silently overwrote the prior grade and the listing dashboard's headline status was per-caller (logging in as user B hid user A's "accept" badge). **Fix**: alembic migration `y9z0a1b2c3d4` drops `uq_golden_review_entry_reviewer`; `submit_review` now always INSERTs a new row; `list_goldens` surfaces the team-wide most-recent review (`latest_review_status`/`latest_review_star`/`latest_reviewer_username`/`latest_review_at`/`review_count`) so every team member sees the same headline grade. `get_golden` drops the per-caller `my_review` field — the submit form always starts blank, and per-reviewer history lives in the team-feedback panel (`GET /v2/goldens/{id}/reviews`). Dead per-entry audio endpoint (`GET /v2/goldens/{id}/review/audio`) removed; cross-user `/reviews/{review_id}/audio` covers all playback. Frontend: `ReviewSubmitForm` always starts blank, resets after submit (`resetForm()`); listing card shows the latest reviewer's username + total review count badge alongside the latest status. i18n: `goldens.listing.byReviewer` added, `goldens.review.existingRecording` / `submitUpdate` removed, `goldens.review.submitNew` → "Post feedback" / "發布回饋" / "发布反馈", `goldens.detail.yourReview` → "Add feedback" / "新增回饋" / "添加反馈". `has_my_review` query param renamed `has_reviews` (semantics: "any reviewer", not "this caller"). TypeScript clean. Files: created `alembic/versions/y9z0_drop_review_unique_constraint.py`; modified `app/api/v2/endpoints/goldens.py`, `app/models_db.py`, `obd-ui/src/app/goldens/{page.tsx, [id]/page.tsx}`, `obd-ui/src/components/goldens/ReviewSubmitForm.tsx`, `obd-ui/src/lib/{api,types}.ts`, three locale files. |
| 2026-05-05 | v2.13 | HARNESS-17 Phase 1: golden-set review dashboard (GitHub Issue #82). Workshop-expert validation track for the golden Q&A set authored under HARNESS-15.  **Backend** (commit `5b84079`): Alembic migration `w7x8y9z0a1b2` creates `golden_entries` (mirror of `tests/harness/evals/golden/v2/*.jsonl` for queryability — JSONL stays canonical) and `golden_reviews` (one row per `(entry, reviewer)` pair, with overall + 3 per-dimension star ratings, status `draft|accept|needs_revision|reject`, free-text notes, and audio attachment columns mirroring the existing `_OBDFeedbackMixin` pattern so the same `<AudioRecorder>` two-step token-upload flow works without changes).  New `app/services/golden_sync.py` runs idempotent JSONL→DB upsert on app startup (lifespan hook in `main.py`); tolerant of malformed lines, skips `candidates/` drafts, logs counts.  Five endpoints under `/v2/goldens/*`: `GET /` (list with bucket / difficulty / has_my_review filters), `GET /{id}` (detail + caller's review), `POST /audio/upload` (stage audio, return token), `POST /{id}/review` (upsert caller's review, links audio_token if present), `GET /{id}/review/audio` (auth-gated stream).  Eval-only fields (`must_contain`, `pitfall_directives`, `expected_recall_slugs`, `expected_tool_trace`) are intentionally NOT exposed via the dashboard API — those are scaffolding for the automated benchmark, not for human review.  dtc-001 bilingualised in `v2/mws150a.jsonl` (added `question_zh` + `golden_summary_zh`).  **Frontend** (this commit): three new components under `obd-ui/src/components/goldens/` — `<StarRating>` (1-5 stars with hover preview + click-to-clear), `<QuestionCard>` (renders question + proposed answer + source quotes with bilingual toggle, falls back to English with hint when Chinese is missing), `<ReviewSubmitForm>` (composes 4 stars + status radio + notes textarea + reused `<AudioRecorder>`, fetches existing audio via auth-gated blob URL).  Two new routes: `/goldens` (listing grouped by bucket with review-progress badges + filter dropdown) and `/goldens/[id]` (detail with bilingual toggle button + manual side-link + review form).  Nav link added to `<HeaderAuth>`.  TypeScript clean.  **Smoke-tested locally**: backend modules import cleanly, sync field-extractor handles valid + missing-field inputs, JSONL parses with new bilingual fields, frontend `tsc --noEmit` passes.  **Pending**: deploy to PolyU server, run migration, end-to-end demo.  **Phase 2-4 (deferred)**: aggregated stats endpoint, admin edit endpoint, "next un-reviewed" navigation, CSV/JSON review export.  Files: created `alembic/versions/w7x8_add_golden_review_tables.py`, `app/services/golden_sync.py`, `app/api/v2/endpoints/goldens.py`, `obd-ui/src/components/goldens/{StarRating,QuestionCard,ReviewSubmitForm}.tsx`, `obd-ui/src/app/goldens/page.tsx`, `obd-ui/src/app/goldens/[id]/page.tsx`.  Modified: `app/models_db.py`, `app/main.py`, `obd-ui/src/components/HeaderAuth.tsx`, `obd-ui/src/lib/{api,types}.ts`, `tests/harness/evals/golden/v2/mws150a.jsonl`. |
| 2026-05-04 | v2.12 | HARNESS-15 (in progress): `hallucination_penalty` redesign — LLM-judged `pitfall_directives` replaces substring-based `must_not_contain` (GitHub Issue #74). **Problem with the old design**: substring scan over `must_not_contain` was context-blind ("this is NOT an oxygen sensor issue" got penalised the same as "this IS an oxygen sensor issue"), near-saturated on non-adversarial entries (both honest agents and useless RAG outputs scored 1.0 on most entries because the wrong-domain content RAG returned didn't happen to overlap with the specific trap strings), and only caught `LLM-fabrication-of-trapped-strings` — missed wrong-domain dumps entirely (RAG returned brake/tire content for a P0117 question and still scored 1.0 because none of the must_not_contain terms appeared). **New design**: `GoldenEntry.must_not_contain: List[str]` renamed to `pitfall_directives: List[str]` — natural-language "don't" instructions evaluated by the LLM judge in the same call that produces `answer_quality` (no extra API cost, just slightly bigger prompt). Each directive is a sentence describing a specific failure mode (e.g., *"The output must not assert that DTC P0117 involves the oxygen sensor"* or *"The output must not present brake-system content as the primary answer"*). The judge decides per directive whether the output ASSERTS / IMPLIES the forbidden statement (semantic, context-aware — handles negation, disambiguation, cross-references correctly). **Soft penalty curve**: `hallucination_penalty = max(0.1, 1.0 - 0.3 * violation_count)` — 0 violations = 1.0, 1 = 0.7, 2 = 0.4, 3+ = 0.1.  Replaces the older steep curve (1 hit = 0.5, 2+ = 0.0) which was binary-shaped — partial credit better matches reality where one passing bad assertion isn't fatal. Floor of 0.1 prevents one metric from zeroing the overall score. **Architecturally**: `hallucination_penalty` moves OUT of `compute_deterministic_metrics` (it's no longer deterministic — depends on judge output). `judge.rate_answer_quality` renamed to `rate_quality_and_pitfalls` and now returns `(answer_quality, reasoning, violation_count, violation_details)`. `compute_overall` signature gained an explicit `hallucination_penalty: float` param. `_build_enriched_reasoning` now surfaces violated directives in the reasoning block. **Symmetry across systems**: same judge prompt + directives applied to both agent and RAG. RAG with brake content for a coolant question now correctly violates the *"don't present brake content as the answer"* directive — captures the wrong-domain failure mode the old metric missed. **Author guidance updated** in `scripts/generate_golden_candidates.py` (LLM-generated entries now produce `pitfall_directives` directly) and `tests/harness/evals/golden/v2/section_plan.md` step 6 (human-author guidance). **Migrated**: dtc-001 (4 directives) and lookup-001 (4 directives) from `must_not_contain: List[str]` to `pitfall_directives: List[str]`. **Validation**: smoke-tested schema parsing of migrated dtc-001, hallucination_penalty curve at violation_count ∈ {0,1,2,3,4}, weights still sum to 1.00, judge module imports cleanly. End-to-end re-run pending. Files modified: `tests/harness/evals/{schemas.py, metrics.py, judge.py, judge_prompts.py, golden/v2/mws150a.jsonl, golden/v2/candidates/dtc-001.json, golden/v2/candidates/lookup-001.json, golden/v2/section_plan.md}`, `tests/harness/evals/test_judge.py` (field rename only — these tests have been broken since v2.7 and need a separate rewrite), `tests/scripts/test_generate_golden_candidates.py`, `tests/scripts/test_review_golden_candidates.py`, `scripts/{generate_golden_candidates.py, review_golden_candidates.py, eval_one_golden.py}`. |
| 2026-05-04 | v2.11 | HARNESS-15 (in progress): agent deliverable = summary + CITED sections only — fix for double-counting exploration overhead (GitHub Issue #74). **Problem surfaced during the v2.10 fact_density rework**: `_agent_result_to_system_run` was concatenating EVERY read section into `output_text`, including navigation overhead (TOC entries, ruled-out hypotheses, neighbouring sections used for context but not cited as answer sources). On dtc-001 with the 3-tool agent, that meant 8 read sections × ~3,700 tokens = ~30,000-token deliverable, of which only 1 section (~3,700 tokens) was actually cited. The 7 non-cited sections were getting served downstream as if they were answer sources, AND penalising the agent in `fact_density` even though `exploration_cost` already captured the same wasted work. **Double-counting**: the same 7 wasted reads contributed to both `exploration_cost` (correct — agent paid navigation cost) and `fact_density` (wrong — those reads aren't part of the deliverable). **Fix**: `_agent_result_to_system_run` in `tests/harness/evals/runner.py` now filters `raw_sections` by `claim_slugs` before concatenating into `output_text`. Only sections the agent explicitly cited in its final JSON flow into the deliverable; sections merely browsed during navigation are still recorded in `result.raw_sections` (full record preserved for diagnostics) but excluded from the metric input. Header label changed `--- Retrieved sections (N) ---` → `--- Cited sections (N) ---` to match the semantic. **Properties of the fix**: (a) Cross-language `fact_recall` still symmetric — Chinese `must_contain` terms come from cited sections by golden-authoring convention, so they remain in the filtered `output_text`. (b) Clean metric separation — `exploration_cost` measures navigation overhead, `fact_density` measures conciseness of the deliverable, no shared work. (c) Aligned with production reality — a downstream diagnose LLM consuming this output should see synthesis + cited sources, NOT a dump of every section the agent looked at. (d) The 4-tool agent run (3 reads, all cited) is unchanged because `cited_slugs == read_slugs` for that run. **Validation on dtc-001 (3-tool agent)**: pre-fix `output_text ≈ 30,000 tokens` → post-fix `≈ 5,000 tokens` (summary + 1 cited section). `fact_density` 0.43 (estimated) → **1.000** (cited content fits the 13,000-token budget for 5 facts). `OVERALL × 100` 88.9 (estimated) → **94.6**. RAG `output_text` unchanged (no synthesis step, claim_slugs == read_slugs). Files modified: `tests/harness/evals/runner.py` (filter logic + updated docstring), `tests/harness/evals/metrics.py` (updated `_compute_fact_density` docstring to reflect "cited" semantic). |
| 2026-05-04 | v2.10 | HARNESS-15 (in progress): `fact_density` rework — token-based budget that scales with `must_contain` count (GitHub Issue #74). **Problem**: the previous formula was `recall × min(1, 100/words)`, calibrated for human chat replies (~100 words). When the agent's `output_text` was switched to `summary + raw_sections concat` (v2.8) to fix cross-language `fact_recall`, the word count ballooned to ~860 words, dropping conciseness to ~0.116 — punishing the agent for behaviour we explicitly wanted. Two compounding flaws: (a) the 100-word cap doesn't match the actual consumer (downstream LLM with 100K-token context, not a human reading chat), (b) `.split()` word-counting under-counts Chinese (no inter-word whitespace), creating a language-asymmetric bias. **Fix**: replaced the conciseness factor with a token-based budget: `budget = BASE + PER_FACT × len(must_contain)`, `conciseness = min(1, budget / tokens)`. Tokens counted via `tiktoken` `cl100k_base` (GPT-4 / DeepSeek-family BPE) — language-aware, aligned with downstream LLM consumer cost. New `_count_tokens()` helper with lazy tiktoken init + `len(text)//4` fallback. **Calibration**: `BASE_TOKEN_BUDGET = 500`, `PER_FACT_TOKEN_BUDGET = 2500`. Calibrated against actual dtc-001 agent output (28,704 chars / 11,821 cl100k tokens for 5 facts) so honest deliverables land at conciseness = 1.0. 50,000-token bloat still drops to 0.26 — metric still catches genuine over-inclusion. **Weight rebalance**: `fact_density: 0.05 → 0.10` (restored — was demoted in v2.9 when it was broken); `hallucination_penalty: 0.15 → 0.10` to fund the restoration (rationale: hallucination_penalty is near-saturated on non-adversarial entries — most systems score 1.0 — so an extra 0.05 of weight here mostly inflates everyone uniformly without improving discrimination; the judge's `answer_quality` already catches subtler hallucinations). Sums to 1.00 exactly. **Validation on dtc-001**: pre-rework `fact_density = 0.116` (broken); post-rework `fact_density = 1.000` (5/5 recall × 1.0 conciseness because 11,821 tokens fits the 13,000-token budget). RAG `fact_density = 0.000` unchanged (recall = 0 → density = 0 regardless of conciseness). New dependency: `tiktoken==0.7.0` added to `diagnostic_api/requirements.txt` (1 MB wheel, Rust-core BPE — fast and pure-pip). Files modified: `diagnostic_api/requirements.txt`, `tests/harness/evals/metrics.py`. |
| 2026-05-03 | v2.9 | HARNESS-15 (in progress): split `section_precision` into `claim_precision` + `exploration_cost` (GitHub Issue #74). **Problem**: the previous `section_precision` was computed against the union of citation slugs and read slugs (every section the agent accessed). This conflated two distinct agent behaviours: **navigation** (reading an index/TOC to find the answer's location) and **grounding** (reading the actual answer section to extract content). A technician who flips through the manual's index then reads the right page isn't being imprecise; the agent doing the same thing was unfairly penalised. Concretely on `dtc-001`: agent read `故障代碼表` (DTC index) then `故障代碼編號-p0117、p0118` (the answer), cited only the answer → old `section_precision = 0.5` even though the claim was perfect. **Fix**: split into two metrics. `claim_precision` (computed over `claim_slugs` only — slugs the system explicitly cited) measures the precision of the claim. `exploration_cost` (`1 - |claim ∩ read| / max(|read|, 1)`, computed over `read_slugs`) measures navigation overhead. RAG: `claim_slugs == read_slugs` (no synthesis), so `exploration_cost` is always 0.0 — intentionally; the navigation/grounding distinction only exists for the agent. **SystemRunResult schema**: replaced `retrieved_slugs: List[str]` with `claim_slugs: List[str]` + `read_slugs: List[str]`. **Grade schema**: replaced `section_precision: float` with `claim_precision: float` + `exploration_cost: float`. **DEFAULT_OVERALL_WEIGHTS** rebalanced: `claim_precision: 0.15` (kept previous section_precision weight), `exploration_cost: 0.05` applied as `(1 - cost)` so all terms contribute positively to overall, `fact_density: 0.10 → 0.05` (de-weighted the broken metric we previously flagged for retuning, freeing the +0.05 budget for exploration_cost). Sums to 1.0 exactly. **Updated**: `_compute_section_recall` now takes the `claim ∪ read` union (asks "did the section appear anywhere?"). `_compute_citation_quality` operates on `claim_slugs` (citation quality reflects the claim, not navigation history). Judge prompt shows both `claim_slugs` and `read_slugs` separately so the judge sees what the system claimed vs what it merely browsed. **Validation on dtc-001 against local Ollama agent**: pre-split overall × 100 was 73.3 (`section_precision=0.5`); post-split overall × 100 is **82.0** (`claim_precision=1.0`, `exploration_cost=0.667`). RAG: 16.5 → 21.5 (gains 5 points from the `(1 - exploration_cost) = 1.0` freebie which is correct — RAG pays no exploration cost because its retrieval IS its claim). Delta widened from +56.8 to **+60.5**. All 33 pre-existing `tests/harness_agents/` unit tests still pass. Files modified: `tests/harness/evals/{schemas.py, metrics.py, runner.py, rag_runner.py, judge.py, judge_prompts.py}`, `scripts/eval_one_golden.py`. |
| 2026-05-03 | v2.9 | HARNESS-15: removed `search_manual` from the manual sub-agent's tool registry (GitHub Issue #74). **Why**: `search_manual` is a thin wrapper around `app.rag.retrieve.retrieve_context` — the same call the comparative-eval RAG track uses. Keeping it in the agent's toolkit (a) muddied the agent-vs-RAG comparison ("agent + RAG-inside vs RAG-alone" instead of clean orthogonality), and (b) was actively harmful in observed runs — the LLM repeatedly called it on identifier queries (DTC codes), got noise back due to cross-language embedding mismatch, and pivoted to TOC navigation anyway, costing ~150ms per wasted call and ~5% of agent wall-clock time on dead-weight retrieval. **Change**: `create_manual_agent_registry()` in `app/harness_agents/manual_agent.py` now registers exactly 3 tools (`list_manuals`, `get_manual_toc`, `read_manual_section`); the import of `SEARCH_MANUAL_DEF` is dropped. The agent's system prompt (`manual_agent_prompts.py`) updated to remove the `search_manual` description and the "or search_manual" alternative in the process steps; replaced with explicit guidance to scan the TOC's DTC quick-reference index for code lookups. **Architectural framing for the paper**: post-change, the agent navigates *structurally* (heading tree + section reads) and RAG retrieves *semantically* (pgvector top-k) — the two systems are now architecturally orthogonal, no shared retrieval mechanism. Test: new `test_search_manual_is_not_registered` (34/34 manual_agent tests pass). Production note: `SEARCH_MANUAL_DEF` itself remains in `harness_tools/rag_tools.py` (other harness configurations may register it); only the manual sub-agent's restricted registry stops including it. Files modified: `app/harness_agents/manual_agent.py`, `app/harness_agents/manual_agent_prompts.py`, `tests/harness_agents/test_manual_agent.py`. |
| 2026-05-03 | v2.8 | HARNESS-15 (in progress): cross-language fact_recall fix + agent deliverable = summary + raw_sections (GitHub Issue #74). **Problem**: deterministic `fact_recall` metric compared `must_contain` (Chinese terms from the source) against `SystemRunResult.output_text`. For RAG, `output_text` IS the concatenated retrieved chunks (Chinese preserved). For the agent, `output_text` was just the synthesised summary (typically translated to English). Cross-language asymmetry: an agent that correctly retrieved the right Chinese-language section but wrote an English summary scored ~0.2 on `fact_recall` even though the information was demonstrably available in its `raw_sections`. Was masked in earlier runs by `_parse_final_json` failing and falling back to the raw JSON dump (which happened to include Chinese citation quotes inflating the score artificially to 0.8). **Fix in `tests/harness/evals/runner.py`**: `_agent_result_to_system_run` now composes `output_text` as `summary + "\n\n--- Retrieved sections (N) ---\n\n" + concat(raw_sections)`. Mirrors RAG's "output_text == concatenated content" shape; gives both systems equal footing on the `must_contain` substring scan. Architecturally aligned with the agent's actual production deliverable — the `ManualAgentResult` already exposes both `summary` and `raw_sections` as separate fields; consumers (diagnose endpoint, future pipelines) get both. **Validation**: re-ran v2 candidate `dtc-001` end-to-end against the production agent (`qwen3.5:27b-q8_0` on local Ollama with `/no_think` directive). Pre-fix `fact_recall=0.200`; post-fix `fact_recall=1.000` (all 5 Chinese must_contain strings now found in the agent's deliverable). Deterministic-only `overall × 100`: agent **73.3** vs RAG **16.5**, **+56.8 delta**. RAG returned brake/battery/tire-label chunks for the English P0117 query — confirms the cross-language semantic-retrieval failure mode the paper centres on. **Eval-driver additions** (`scripts/eval_one_golden.py`): `--no-think` flag injects the Qwen3 `/no_think` directive into the agent's system prompt (drops first-token latency from ~91s to ~2.5s on local Ollama qwen3.5:27b-q8_0); `--max-tokens` flag bounds per-call tokens (workaround for OpenRouter 402 when credits tight). New `_NoThinkOpenAILLMClient` wrapper subclass — lives in eval driver, doesn't touch production. **Open issue surfaced**: `fact_density` (= `recall × min(1, 100/words)`) drops sharply when `output_text` includes raw_sections (now ~28KB vs ~500 char summary). Currently 0.077 for the agent — almost certainly too punishing. Worth retuning to measure conciseness-of-summary rather than conciseness-of-deliverable; flagged for follow-up. Files modified: `tests/harness/evals/runner.py`, `scripts/eval_one_golden.py`, `tests/harness/evals/golden/v2/source/.gitignore`. |
| 2026-05-03 | v2.7 | HARNESS-15 (in progress): comparative-eval schema + RAG runner + continuous-metric rubric (GitHub Issue #74). **Pivot:** issue scope expanded from "rebuild golden v2" to "agent-vs-RAG benchmark suitable for a publishable comparison study." See [#74 comment](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/74#issuecomment-4367141575). **Schema additions** (`tests/harness/evals/schemas.py`): `GoldenQuestionType` literal (`lookup` / `procedural` / `cross-section` / `image-required` / `adversarial`) — primary axis for slicing comparative results, independent of `GoldenCategory`. `GoldenEntry` gained `question_type` (required) and `expected_recall_slugs` (was implicit in `golden_citations`). New `SystemRunResult` model — unified output shape both the manual sub-agent and the RAG retriever produce, so the judge grades them on the same rubric without caring which produced them. `RetrievedChunkMetadata` for RAG chunk-level diagnostics. `Grade` rewritten with **continuous metrics** in `[0.0, 1.0]` (was binary 0/1); new dimensions: `section_recall`, `section_precision`, `fact_density`, `hallucination_penalty`, `citation_quality`, `answer_quality`, `trajectory_efficiency`. **Deterministic-metric module** (new `tests/harness/evals/metrics.py`): computes seven of eight rubric dimensions from `(GoldenEntry, SystemRunResult)` without LLM involvement — reproducible across runs. `_compute_fact_density` is `recall × conciseness_factor` (rewards short complete answers; penalises verbose ones). Whitespace-normalised CJK-aware substring matching. Default formula weights exposed as `DEFAULT_OVERALL_WEIGHTS` for future tuning. **RAG runner** (new `tests/harness/evals/rag_runner.py`): wraps `app.rag.retrieve.retrieve_context`, normalises to `SystemRunResult` with chunk-to-slug bridging via `slugify(chunk.section_title)`. Captures wall-clock latency. Embedding cost = $0 (local Ollama). **Agent unified adapter** (`tests/harness/evals/runner.py`): new `run_manual_agent_unified` returns `SystemRunResult`; legacy `run_manual_agent` kept for callers needing raw shape. **Judge rewrite** (`tests/harness/evals/judge.py` + `judge_prompts.py`): scope reduced to `answer_quality` rating only; deterministic metrics handled separately. New top-level `grade_run(entry, run) -> Grade` orchestrates determinitstic metrics + judge call + weighted combination + enriched-reasoning composition. Judge `max_tokens` reduced 2048→512. **Eval driver rewrite** (`scripts/eval_one_golden.py`): `--system manual_agent | rag | both` (default `both`), `--top-k` for RAG, side-by-side score table with delta column, trade-off block for latency/cost. All 33 pre-existing `tests/harness_agents/` unit tests pass unchanged — production code (`app/harness_agents/manual_agent.py`) untouched. **Phase 1 finding for a separate ticket:** `vehicle_model` is `"MWS-150-A"` in `manuals` but `"MWS150-A"` in `rag_chunks` — filtering RAG by canonical name returns 0 chunks. Files created: `tests/harness/evals/{metrics.py, rag_runner.py}`. Files modified: `tests/harness/evals/{schemas.py, judge.py, judge_prompts.py, runner.py}`, `scripts/eval_one_golden.py`. Stale `v2/mws150a.jsonl` entries (old `manual_id`, drifted punctuation) discarded in a follow-up commit before Phase 4 re-authoring. |
| 2026-04-27 | v2.6 | HARNESS-15 (in progress): citation-slug canonicalisation in the manual sub-agent (GitHub Issue #74). **Bug**: during eval, `result.citations[].slug` and `result.raw_sections[].slug` were populated with whatever free-form string the LLM passed as `read_manual_section`'s `section` argument — typically the section's display title (e.g. `故障代碼編號 P0117、P0118`) because that is what `get_manual_toc` shows the model. The judge's `section_match` rubric does literal string equality against the golden's parser-canonical slug (`p0117-p0118`), so correct answers were systematically scored 0 on `section_match`, contaminating every eval result that depended on it. **Fix**: `_extract_section_ref` and `_parse_final_json` now resolve LLM-emitted slugs to the parser-canonical form via a new `_canonicalise_slug()` helper that applies the same matching strategies (exact → slugify → substring) `read_manual_section` already uses internally. New `_slugs_for_manual()` helper loads the manual via the existing `_read_manual_file()` and walks the heading tree to produce the canonical-slug list. `_parse_final_json` gained an optional `raw_sections` parameter (back-compat: existing callers passing only `content` still work — the slug just passes through unchanged). The agent loop's terminal call site now forwards the accumulated `raw_sections` so citation slugs can be canonicalised. **Validation**: re-ran v2 candidate `dtc-001` (P0117 coolant-temp DTC) end-to-end — pre-fix `overall=0.6` (`section_match=0` due to title-as-slug); post-fix `overall=1.0` (`section_match=1`, `fact_recall=1.0`, judge reasoning explicitly cites the canonical slug). All 33 existing `tests/harness_agents/test_manual_agent.py` tests still pass. Files modified: `app/harness_agents/manual_agent.py` only — no schema, tool I/O, or system-prompt changes. |
| 2026-04-23 | v2.5 | HARNESS-14 phase 3: golden-candidate generator + reviewer scripts (GitHub Issue #73). **Generator** (`scripts/generate_golden_candidates.py`): reads real manuals from `settings.manual_storage_path`, samples sections via category-aware heuristics (regex matches on title/body for `dtc`/`symptom`/`component`, image-ref detection for `image`, metadata+TOC sampling for `adversarial`), prompts an OpenRouter LLM (default `deepseek/deepseek-v3.2` — chosen to differ from judge's `z-ai/glm-5.1` and reduce circularity) with a rubric-pinned system prompt, and validates every candidate via `_validate_and_ground()` before emitting. Grounding check: every citation's `manual_id` + `slug` must match the sampled section, and every `quote` must be a verbatim substring of the section text. Adversarial branch enforces empty `golden_citations` + "not found" in `must_contain` and takes a different system prompt (fake DTC / out-of-scope / nonexistent-component flavours). Duplicate questions (case-insensitive) are suppressed. Output lands in `tests/harness/evals/golden/candidates/` — **never directly in `v1/`**. 32 unit tests using scripted `AsyncOpenAI` with slug-aware reply builder that inspects the user prompt. **Reviewer** (`scripts/review_golden_candidates.py`): interactive TUI (accept/edit/reject/skip/quit) with `$EDITOR`-based edit flow, schema re-validation via `GoldenEntry.model_validate` before appending, and sidecar `.review-state.json` for resume across sessions. Auto-infers golden v1 target path from candidates filename (`candidates/mws150a-dtc.jsonl` -> `v1/mws150a.jsonl`); overridable with `--out`. 23 unit tests using scripted `input()` + fake editor runner: cover accept/reject/skip/quit, mixed decisions, unknown-input reprompt, edit success, edit abort, edit-with-invalid-schema reprompt, state persistence + resume, malformed-entry rejection, candidates loader malformed-line skipping. Full test sweep: 735 passed (+55 from phase 2), 7 skipped, 1 pre-existing DB-env failure (unrelated). Follow-up task (not in this commit): run generator against real MWS150A manual + human-review into `v1/mws150a.jsonl` (requires API access). Files created: `scripts/generate_golden_candidates.py`, `scripts/review_golden_candidates.py`, `tests/scripts/{__init__.py, test_generate_golden_candidates.py, test_review_golden_candidates.py}`. |
| 2026-04-23 | v2.4 | HARNESS-14 phase 2 (commit 3): manual-search sub-agent (GitHub Issue #73). New `app/harness_agents/` package hosts production sub-agents that reuse the core harness's `LLMClient` protocol + `ToolRegistry` but run their own minimal loops and return structured results (no DB event log, no SSE streaming). `types.py` defines production shapes (`Citation`, `SectionRef`, `ToolCallTrace`, `ManualAgentResult`, `StoppedReason`); `tests/harness/evals/schemas.py` re-exports them so there is one source of truth. `manual_agent.py` implements `run_manual_agent(question, obd_context, deps)` — a restricted 4-tool ReAct loop (`list_manuals`, `get_manual_toc`, `read_manual_section`, `search_manual`; `read_obd_data` explicitly excluded) with `asyncio.timeout` budget, max-iteration guard, and graceful error handling. Defaults: `qwen3.5:27b-q8_0`, max_iterations=8, max_tokens=12288, temperature=0.2, timeout=120s. Final-answer contract enforced via `_parse_final_json()` with three fallback strategies (direct JSON, markdown-fence strip, first-`{...}`-block regex) and a raw-content fallback when all fail. `read_manual_section` outputs are captured into `raw_sections` automatically with `had_images` flag detected from multimodal content blocks. Tool inputs are sanitised before being recorded in `tool_trace` (strips `_`-prefixed keys, truncates strings > 500 chars). `create_manual_agent_registry()` factory builds a fresh registry with exactly the 4 manual tools. New `app/harness_agents/manual_agent_prompts.py` pins the system prompt (citation-format rules, adversarial-entry handling, final-JSON schema). Eval `runner.py` replaced its phase-1 stub with a thin wrapper that builds process-cached default deps pointing at local Ollama (`settings.llm_endpoint + "/v1"`) and forwards to the agent loop. New `--mock-agent` CLI flag + `manual_agent_deps` fixture returns a canned-response `ManualAgentDeps` for plumbing runs without a running LLM. 33 new unit tests in `tests/harness_agents/test_manual_agent.py` (registry restriction 2, markdown fence 3, final JSON parser 7, tool args 4, input sanitiser 3, section extraction 4, last-assistant fallback 2, happy-path loop 5, budget/error 3) using a scripted `LLMClient` pattern. All 33 pass; `--run-eval --mock-agent --mock-judge` completes the pipeline without LLM calls; full suite 680 passed (1 pre-existing DB-env failure unrelated). Files created: `app/harness_agents/{__init__,types,manual_agent,manual_agent_prompts}.py`, `tests/harness_agents/{__init__,test_manual_agent}.py`. Modified: `tests/harness/evals/runner.py` (real wiring), `tests/harness/evals/conftest.py` (`_build_mock_agent_deps` + `manual_agent_deps` fixture), `tests/harness/evals/schemas.py` (re-export from types.py), `tests/harness/evals/test_manual_agent_eval.py` (consumes new fixture), `tests/conftest.py` (registered `--mock-agent` CLI option), `docs/v2_dev_plan.md`, `docs/v2_design_doc.md`. |
| 2026-04-23 | v2.3 | HARNESS-14 phase 2 (commit 2): GLM 5.1 judge wrapper (GitHub Issue #73). Replaced the phase-1 judge stub with a real `AsyncOpenAI` call to `z-ai/glm-5.1` at temperature 0 with `response_format={"type": "json_object"}`, pulling credentials from `settings.premium_llm_api_key` / `settings.premium_llm_base_url` (same env vars as the user-facing premium client). Single-retry policy: on first-try JSON parse failure, judge is re-prompted with a corrective user message appended to the history; on parse failure again or API errors in both attempts, returns a zero-score `Grade` tagged `[judge failure]` rather than raising, so one bad entry can't crash the whole eval run. Pinned constants: `_JUDGE_MODEL="z-ai/glm-5.1"`, `_JUDGE_TEMPERATURE=0.0`, `_JUDGE_MAX_TOKENS=2048`, `_MAX_SECTION_CHARS=3000` (per raw-section text cap in the judge prompt). Client is injectable — callers may pass a pre-built `AsyncOpenAI` instance (tests use a fake), otherwise `_get_default_client()` lazily constructs one from settings and caches per-process. New `--mock-judge` CLI flag and `judge_client` fixture let engineers exercise `--run-eval` plumbing without consuming OpenRouter credits: the fixture returns `None` (→ real client) by default, or a canned-response mock when `--mock-judge` is set. New `judge_prompts.py` module with `JUDGE_SYSTEM_PROMPT` pinning the 5-dimension rubric + adversarial-entry special case, and `build_user_prompt()` assembling golden + agent data with tool-trace order/counts summary and raw-section truncation. 21 new unit tests in `test_judge.py` (prompt construction 6, parse helpers 4, happy path 3, retry 6, edge cases 2) via `_FakeClient` pattern. All 21 pass; `--run-eval --mock-judge` plumbing green; `--run-eval` without `--mock-judge` fails loudly with clear `RuntimeError: Judge requires PREMIUM_LLM_API_KEY` message. 242 pre-existing harness tests unchanged. Files created: `tests/harness/evals/judge_prompts.py`, `tests/harness/evals/test_judge.py`. Modified: `tests/harness/evals/judge.py` (rewrote from stub), `tests/harness/evals/conftest.py` (added `_build_mock_judge_client` + `judge_client` fixture), `tests/harness/evals/test_manual_agent_eval.py` (consumes `judge_client` fixture), `tests/conftest.py` (registered `--mock-judge` CLI option). |
| 2026-04-23 | v2.2 | HARNESS-14 phase 1: scaffolding for the manual-agent evaluation suite (GitHub Issue #73). Locked model choices for HK constraint: judge = `z-ai/glm-5.1` via OpenRouter (Claude/OpenAI/Gemini geo-blocked, see #23); agent primary = local `qwen3.5:27b-q8_0`; ceiling comparison (phase 5) = `glm-5.1`/`kimi-k2`. Pydantic schemas (`GoldenEntry`, `GoldenCitation`, `Citation`, `SectionRef`, `ToolCallTrace`, `ManualAgentResult`, `Grade`) define contracts between golden set, agent, and judge. Phase-1 stubs for `run_manual_agent()` and `judge_result()` return deterministic dummy output so the end-to-end pytest pipeline (parametrization + session-scoped `eval_report` fixture → timestamped JSON artifact) can be verified without LLM cost. Goldens are immutable once frozen; corrections bump to `v2/` (rules in `golden/README.md`). 3 phase-1 dummy entries in `v1/mws150a.jsonl` (DTC easy, component medium, adversarial hard). Root conftest extended with `--run-eval` CLI flag + `pytest_collection_modifyitems` so eval-marked tests are skipped unless the flag is passed (keeps default `pytest` runs fast/free). Known limitation: one-manual corpus until a second manual is acquired — adversarial category swapped from cross-manual to intra-manual edge cases (fake DTC `P9999`, out-of-scope query, typo'd slug, multi-section answer). Files created: `tests/harness/evals/{__init__.py,schemas.py,runner.py,judge.py,conftest.py,test_manual_agent_eval.py}`, `tests/harness/evals/golden/{README.md,v1/mws150a.jsonl}`, `tests/harness/evals/reports/.gitignore`. Modified: `tests/conftest.py`. All 3 eval tests pass with `--run-eval`; 242 pre-existing harness tests unchanged (1 pre-existing DB-env failure). |
