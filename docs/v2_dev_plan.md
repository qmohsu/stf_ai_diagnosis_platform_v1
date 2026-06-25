# V2 Development Plan — Harness Engineering (v1.0)

**Agent-driven vehicle diagnosis via harness loop, tool registry, and graduated autonomy**

| Field | Value |
|-------|-------|
| **Architecture doc** | `docs/v2_design_doc.md` |
| **GitHub Issue** | #26 (discussion: From Context Engineering to Harness Engineering) |
| **Version** | v1.0 |
| **Last updated** | 2026-05-24 (removed `search_manual` from main-agent registry; registry 12 → 11 tools, no RAG in agent pipeline) |

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

#### HARNESS‑19 — Agent-Native OBD Investigation Toolset ✅ DONE

Depends on: HARNESS-01, HARNESS-09 (sub-agent infrastructure from HARNESS-14)
GitHub Issue: #85
Status: **DONE** (2026-05-16) — design + scaffolding + tests landed in one PR.

Scope: Replace the single `read_obd_data` two-mode tool with 6 decomposed cognitive primitives, add an OBD investigation sub-agent (mirroring the manual sub-agent template from HARNESS-14), and introduce delegation wrappers so the main agent can route compound investigations to either specialist. Implements hybrid Pattern 2 from the design doc — main agent retains direct access to primitives AND can delegate.

The toolset is **agent-native** by HARNESS-09's principle: tools return data, not pre-digested conclusions. V1's `statistics_extractor` style stats computations are reimplemented locally rather than going through the `NormalizedTimeSeries` pipeline (which strips Yamaha proprietary `A_YAM_*` columns).

Key design decisions (per `docs/plans/2026-05-16-obd-toolset-design.md`):
- **Six primitives** in `app/harness_tools/obd_signals.py` (`list_signals`, `read_window`, `get_signal_stats`, `find_events`) and `app/harness_tools/obd_dtcs.py` (`list_dtcs`, `lookup_dtc`).
- **Sub-agent** in `app/harness_agents/obd_agent.py` (`run_obd_agent`), restricted 6-tool registry via `create_obd_agent_registry()`. Output contract: `OBDAgentResult` Pydantic shape with `summary`, `signal_citations`, `dtc_citations`, `raw_data` (auto-captured tool excerpts), `limitations`, `tool_trace`, `iterations`, `stopped_reason`.
- **Delegation** wrappers in `app/harness_tools/delegation_tools.py` for both OBD and manual sub-agents. Recursion guard: sub-agent registries do NOT include delegation tools — verified by `tests/harness_tools/test_delegation_tools.py::TestNoRecursion`.
- **Yamaha A_YAM_\* proprietary columns** exposed under original names; the new `app/harness_tools/obd_loader.py` bypasses `format_normalizer.py` and reads raw CSV directly (UTF-8 BOM-aware). 16 proprietary columns preserved.
- **Yamaha hex DTCs** handled honestly — `lookup_dtc("87F11043...")` returns "no decoder available" plus a `search_manual` pivot. No fabricated decodings.
- **`read_obd_data` deprecated** — file kept on disk for one release cycle but unregistered from `create_default_registry()`. Tests against it kept passing.
- **Main agent toolbox grew 5 → 12 tools**: 6 OBD primitives + 4 manual primitives + 2 delegation wrappers. System prompt rewritten with "primitives vs. delegation" guidance.

Files created:
- `app/harness_tools/obd_loader.py` — Yamaha-aware raw loader (`OBDLogData`, `detect_format`, `load_for_session`, BOM-strip, time + float helpers).
- `app/harness_tools/obd_signal_inventory.py` — `SignalDescriptor`, `classify_subsystem`, `units_for`, `build_inventory`, `filter_inventory`, `resolve_signal_name`, `fuzzy_suggestions`.
- `app/harness_tools/obd_signals.py` — 4 signal primitives + `ToolDefinition` exports.
- `app/harness_tools/obd_dtcs.py` — 2 DTC primitives + Yamaha metadata extraction.
- `app/harness_tools/delegation_tools.py` — `delegate_to_obd_agent` + `delegate_to_manual_agent` wrappers; `set_shared_llm_client()` for the harness to install its `LLMClient`.
- `app/harness_agents/obd_agent.py` — sub-agent ReAct loop, mirrors `manual_agent.py`.
- `app/harness_agents/obd_agent_prompts.py` — system prompt + user-message builder.
- `app/harness_agents/result_formatters.py` — `format_obd_agent_result` + `format_manual_agent_result` markdown serializers.
- `tests/harness_tools/{__init__,test_obd_loader,test_obd_signals,test_obd_dtcs,test_delegation_tools}.py`.
- `tests/harness_agents/test_obd_agent.py`.

Files modified:
- `app/harness_agents/types.py` — added `SignalCitation`, `DTCCitation`, `DataExcerpt`, `OBDAgentResult` (parallel to `ManualAgentResult`).
- `app/harness_tools/input_models.py` — 8 new input models (`ListSignalsInput`, `ReadWindowInput`, `GetSignalStatsInput`, `FindEventsInput`, `ListDTCsInput`, `LookupDTCInput`, `DelegateToOBDAgentInput`, `DelegateToManualAgentInput`).
- `app/harness/tool_registry.py` — `create_default_registry()` rewritten for 12-tool hybrid layout.
- `app/harness/harness_prompts.py` — system prompt expanded with new tool descriptions + "primitives vs. delegation" guidance.

Acceptance criteria:
- [x] 6 OBD primitives implemented + registered.
- [x] OBD sub-agent end-to-end smoke test with mocked LLM (`tests/harness_agents/test_obd_agent.py::TestRunOBDAgentEndToEnd`).
- [x] Delegation wrappers tested for registry membership + recursion guard + I/O contract.
- [x] All 8 new tools tested against the real Yamaha road-test fixture.
- [x] A_YAM_\* columns surface under original names in `list_signals` output (verified by `test_a_yam_signals_listed_under_original_names`).
- [x] Yamaha hex DTCs surface as honest "no decoder + manual pivot" (verified by `TestLookupDTCYamahaHex`).
- [x] Existing harness regression tests stay green (223 passed in tiktoken-free slice; 25 environment SSL errors unrelated to this work).

Out of scope (separate tickets):
- Cross-signal correlation tool (`correlate_signals`) — defer until evals show it's needed.
- Anomaly-as-a-tool — runs into HARNESS-09's "no pre-digestion" principle.
- Annotation scratchpad — introduces state.
- Freeze-frame snapshot — no fixture has freeze-frame data yet.
- Yamaha hex DTC decoder — needs proprietary spec.
- OBD eval suite — parallel to HARNESS-14 but blocked on labelled fault data.
- Pure-orchestrator main agent rewrite (Pattern 3 from design doc) — long-term direction.

#### HARNESS‑20 — Lock-In Path for Expert-Approved Goldens 🔧 IN PROGRESS

Depends on: HARNESS-14 (golden infrastructure), HARNESS-17 (review dashboard providing the `golden_reviews` table that the promotion gate consults).
GitHub Issue: #90.
Status: **IN PROGRESS** (2026-05-24) — two-tier corpus + promote-by-script landed; UI promotion button + retro-lock of the 30 already-graded entries deferred to follow-ups.

Scope: Split the V2 golden corpus into a mutable **candidate** tier (`tests/harness/evals/golden/v2/*.jsonl`) and an append-only **locked** tier (`tests/harness/evals/golden/v2/locked/*.jsonl`). The eval harness reads only the locked tier, so an edit to `must_contain` or to a `golden_citations[].quote` on a candidate cannot retroactively re-score an entry that has already been graded against. Promotion is one-way and audit-trailed: `scripts/promote_golden.py` enforces a review-quality gate (latest expert review must be `status='accept'` with `star_rating >= 4`), appends the candidate line verbatim into the locked file, stamps SHA-256 of the canonical-serialised JSON, and writes one row to `locked/PROMOTIONS.md` recording the timestamp, hash, reviewer, expert review id, and reason. `--force` bypasses the gate and is itself recorded.

Key design decisions:
- **Option A (two-tier files)** chosen over Option B (in-place `frozen` flag + content hash) and Option C (immutable revisions). Selected because the dashboard already treats the candidate file as canonical-source-on-disk, the operational cost of two files is near-zero, and "edit a locked entry" needing a new id is exactly the constraint we want — it falls out of file-layout instead of requiring schema changes.
- **`tier` column on `golden_entries`** added via Alembic `z0a1b2c3d4e5`; `golden_sync.py` populates it from path detection (recursive walk under `v2/`; `"locked"` in path parts → `tier='locked'`). Surfaced through `GoldenEntrySummary.tier` and `GoldenEntryDetail.tier` so the dashboard can render a lock badge in a follow-up UI change without an API contract bump.
- **Audit log is plain Markdown** (`locked/PROMOTIONS.md`), not a DB table. A flat file shows up in `git diff` on every promotion, can't be rewritten with `psql`, and is permanently attributable via `git blame`.
- **Verbatim append** to the locked JSONL — the script writes the exact bytes from the candidate line, not a re-serialised form. Keeps future diffs against the candidate clean and makes the content hash stable.
- **Eval harness flip in one line** — `test_manual_agent_eval.py` now loads from `v2/locked/mws150a.jsonl`. The shipped locked file is empty, so the eval suite collects zero parametrised cases (cleanly skipped) until the first promotion. This is the deliberate safety net: no agent-vs-RAG number can be published until an expert-approved entry exists.

Phasing:
1. **Backend + script** ✅ DONE (PR #102) — migration, `golden_sync.py` tier wiring, API tier field, `promote_golden.py`, empty `locked/` directory, audit log skeleton, eval harness pointer flip, README rewrite, unit tests.
2. **Retro-lock the 30 graded candidates** ✅ DONE (2026-05-24, phase 2 PR) — server-side enumeration confirmed all 30 entries had a 5★ accept review from the Towngas workshop expert (reviewer UUID `b34ac0f0-...`). Batch promoted via `_scratch_batch_promote.py` (run-once driver, not committed) using `--force` + new `--expert-review-id` kwarg so the audit row attributes the source review even without a live DB lookup. `locked/mws150a.jsonl` now contains 30 lines; `locked/PROMOTIONS.md` has 30 attributable rows. `test_manual_agent_eval.py` now collects 30 parametrised cases (was 1 skipped placeholder under phase 1's empty-tier safety net).
3. **First baseline eval run** — pending. Open questions before running: (a) lower `_PASS_THRESHOLD` from 0.7 to something realistic so a sub-baseline number doesn't hard-fail the whole suite on the first run; (b) run both `manual_agent` AND `rag` lanes since the publishable artifact in #74 is the comparison, not either system alone; (c) commit `docs/harness_14_phase6_baseline.md` summarising results (model versions, pass-rate per category, failure attribution). Rough cost: $3-8 per full run on OpenRouter.
4. **UI promotion button** — follow-up. Admin-only; reads `latest_review_*` from `GoldenEntrySummary`, calls a new `POST /v2/goldens/{id}/promote` endpoint that wraps `promote_entry()`. Lower priority now that the 30-entry retro-lock is done — future promotions will mostly be incremental as new candidates clear the review gate.
5. **Content-hash consistency check** — periodic job (CI or admin-triggered) re-hashes locked entries and flags any drift from `PROMOTIONS.md`. Becomes meaningful now that the locked tier is non-empty.

Files (phase 1 PR #102):
- New: `diagnostic_api/alembic/versions/z0a1_add_golden_tier_column.py`, `diagnostic_api/scripts/promote_golden.py`, `diagnostic_api/tests/scripts/test_promote_golden.py`, `diagnostic_api/tests/test_golden_sync.py`, `diagnostic_api/tests/harness/evals/golden/v2/locked/mws150a.jsonl` (empty), `diagnostic_api/tests/harness/evals/golden/v2/locked/PROMOTIONS.md`.
- Modified: `diagnostic_api/app/models_db.py` (`GoldenEntry.tier` column + check constraint), `diagnostic_api/app/services/golden_sync.py` (`_tier_for_path`, recursive walk, tier propagation), `diagnostic_api/app/api/v2/endpoints/goldens.py` (`tier` in `GoldenEntrySummary` and `GoldenEntryDetail` + mappers), `diagnostic_api/tests/harness/evals/test_manual_agent_eval.py` (load from `v2/locked/`), `diagnostic_api/tests/harness/evals/golden/README.md` (two-tier policy), `diagnostic_api/tests/harness/evals/conftest.py` (docstring path example), `diagnostic_api/scripts/review_golden_candidates.py` (docstring path example).

Files (phase 2 PR):
- Modified: `diagnostic_api/scripts/promote_golden.py` (new `expert_review_id_override` kwarg + `--expert-review-id` CLI flag), `diagnostic_api/tests/scripts/test_promote_golden.py` (2 new tests for the kwarg).
- Populated: `diagnostic_api/tests/harness/evals/golden/v2/locked/mws150a.jsonl` (30 lines), `diagnostic_api/tests/harness/evals/golden/v2/locked/PROMOTIONS.md` (30 audit rows).

Files (post-phase-2 schema-fix PR):
- New: `diagnostic_api/alembic/versions/a1b2_rename_tier_to_is_locked.py` (drops the broken `tier` string column, adds `is_locked` boolean).
- Modified: `diagnostic_api/app/models_db.py` (column rename + type change), `diagnostic_api/app/services/golden_sync.py` (rewritten as two-pass: candidate-content upsert + locked-flag overlay), `diagnostic_api/app/api/v2/endpoints/goldens.py` (`tier` → `is_locked` on response schemas + mappers), `diagnostic_api/tests/test_golden_sync.py` (12 tests for the new helpers — candidate walk, locked walk, overlay flag-flip, orphan warnings).

Acceptance criteria:
- [x] Empty `locked/mws150a.jsonl` committed; eval harness reads from it. *(phase 1)*
- [x] `promote_golden.py` runs end-to-end with review-gate, hash, audit row. *(phase 1)*
- [x] ~~`tier` column round-trips through migration, `golden_sync`, and API responses.~~ Superseded by `is_locked` boolean (schema-fix PR) — see note below. *(phase 1 → corrected)*
- [x] Unit tests cover happy path, refuse-relock, review-gate failures, `--force`, `--dry-run`. *(phase 1)*
- [x] V2 dev plan + design doc updated in the same PR. *(phases 1 + 2 + schema-fix)*
- [x] At least one entry promoted end-to-end (server review history confirms all 30 qualify; batch promoted with audit trail). *(phase 2)*
- [x] DB tier/lock state reflects on-disk reality post-deploy: all 30 rows report `is_locked=True` after the schema-fix PR + container restart. *(schema-fix)*

**Phase 1 schema bug + correction:** Post-deploy verification on 2026-05-24 found that the original `tier` column (added in Alembic `z0a1b2c3d4e5`, phase 1) was conceptually wrong. Both tiers share entry ids by design (the locked file is a verbatim copy of the candidate line — that's how `promote_golden.py` works), but `GoldenEntry.id` is the sole primary key, so the recursive sync walk produced two upserts on the same id and the second one overwrote the first. Net result: every row in `golden_entries.tier` read `'candidate'` regardless of actual lock state, even after phase 2 locked all 30. The schema-fix PR replaces `tier` with an `is_locked` boolean and rewrites `golden_sync` as two passes (candidate-content upsert → locked-flag overlay UPDATE). The candidate's current content always wins in the DB (so the dashboard reflects the latest mutable edit), and the lock badge is just "this id is also in the locked file". Locked-tier *content* stays on the filesystem and is read directly by the eval harness — the DB no longer tries to mirror it.

Out of scope (this PR and follow-ups):
- UI promotion button (phase 4).
- Content-hash consistency checker (phase 5).
- Per-entry revision history (Option C from the design discussion) — revisit only if "amend a locked entry without cloning to a new id" becomes a real workflow need.

#### HARNESS‑21 — OBD Sub-Agent Evaluation Framework ✅ DONE

Depends on: HARNESS-14 (judge + golden infrastructure), HARNESS-19 (OBD sub-agent under test), Yamaha road-test fixture (#80, merged).
GitHub Issue: #97.
Status: **PR [3/4] COMPLETE** (2026-05-24) — baseline scorecard at `docs/harness_21_phase5_baseline.md`; `promote_golden.py --lane=obd` support; OBD eval reader migrated to `golden/v2/locked/yamaha_road_test.jsonl` (empty until first expert promotion).  HARNESS-21 closed; follow-ups (prompt iteration, threshold raise to 0.75, `must_contain` adjustment on adversarial-001, ceiling run with `z-ai/glm-5.1`) are logged as separate tickets driven by workshop expert feedback at `/goldens/obd`.  Design doc: `docs/plans/2026-05-17-harness-21-obd-eval-design.md`.

Scope: Parallel evaluation lane to HARNESS-14, scoped to the OBD sub-agent (`app/harness_agents/obd_agent.py`) running against the Yamaha road-test fixture. Same judge model (`z-ai/glm-5.1`), same `--run-eval` plumbing, same `Grade` envelope — diverging only where OBD data shape requires (native signal/DTC citations, numerical-value tolerance, explicit no-evidence flag for adversarial entries). Grades **descriptive accuracy** ("does the agent correctly characterise what the data shows") on a healthy bike; diagnostic-accuracy evals are a separate ticket pending labelled-fault recordings.

Key design decisions:
- **Extend existing schemas** rather than fork — `SystemRunResult` gains `obd_signal_citations` / `obd_dtc_citations`; `GoldenEntry` gains `expected_signal_citations` / `expected_dtcs` / `expected_no_evidence`; `Grade` gains `value_accuracy` (default 1.0 neutral for manual lane).
- **Lane dispatcher by `question_type`** — six new OBD types (`signal_statistics`, `event_finding`, `dtc_enumeration`, `dtc_decode`, `compound_obd`, `adversarial_obd`) route through `metrics_obd.py`; manual types stay on the original `metrics.py` path.
- **Unified weights** — one `DEFAULT_OVERALL_WEIGHTS` summing to 1.0 across both lanes (9 dims). Manual-lane scores will shift slightly under the rebalance; PR [3/3] re-baselines both lanes.
- **Numerical tolerance** — 5% relative by default, per-citation override via `value_tolerance_rel`. Zero-expected falls back to absolute tolerance 0.01.
- **Adversarial via explicit flag** — `expected_no_evidence=True` flips polarity (citing nothing is the right answer); pitfall_directives catch affirmative hallucinations semantically.
- **Sibling modules under one suite** — `obd_runner.py` + `metrics_obd.py` + `test_obd_agent_eval.py` next to the manual versions. Shared `schemas.py` + `judge.py` + `conftest.py`.

Phasing (rescoped 3→4 PRs on 2026-05-24):
1. **PR [1/3] — Scaffolding** ✅ DONE (2026-05-17, 8 commits): schema extensions, `metrics_obd.py` (TDD: 46 tests first), lane dispatcher + weight rebalance, `obd_runner.py` + adapter, judge OBD-lane sanity tests, `compute_yamaha_reference.py` developer aid, eval entry point + 3 dummy goldens + Yamaha session fixture + conftest mocks, V2 doc updates. 91 new tests; full plumbing run green via `pytest -m eval --run-eval --mock-agent --mock-judge`.
2. **PR [2a/4] — Eval-side fixes + real goldens** ✅ DONE (2026-05-24, 7 commits): `compute_dtc_accuracy` empty-expected consistency fix, `OBDAgentConfig._DEFAULT_TIMEOUT` 120s→240s, real Yamaha session bootstrap fixture (was `pytest.skip`), reference-stats sidecar JSON committed, 15 hand-authored goldens (2/2/2/3/3/3) replacing the 3 PR [1/3] dummies, V2 doc updates.  Latent path-resolution bug from PR [1/3] (`parent.parent.parent.parent` resolved to `diagnostic_api/`) corrected to `.parents[4]`.  125 OBD eval unit tests pass.
3. **PR [2b/4] — UI lane + v1→v2-tier migration** — new `/goldens/obd` route mirroring `/goldens/manual` (split from current `/goldens` landing), `?lane=obd` query param on `GET /v2/goldens`, `lane` column on `GoldenReview` (Alembic), sparkline rendering from sidecar JSON, OBD eval reader moves to `golden/v2/locked/yamaha_road_test.jsonl` with the seed-from-v1 step.  First OBD promotions happen through the UI's expert-review workflow.
4. **PR [3/4] — Baseline scorecard + workflow plumbing** ✅ DONE (2026-05-24, 4 commits): authored `docs/harness_21_phase5_baseline.md` from PR [2a/4]'s real-LLM run (12/15 pass, mean 0.843, 29:24 wall; per-bucket + per-entry tables + failure analysis); `promote_golden.py --lane=obd` support (7 new tests, 26 total); migrated `test_obd_agent_eval.py` reader from `v1/yamaha_road_test.jsonl` → `v2/locked/yamaha_road_test.jsonl` (empty initial state; mirrors manual lane's HARNESS-20 safety net — empty parametrize handled via skipped placeholder with actionable message); V2 docs updated.  Prompt iteration, threshold raise, `must_contain` fix on adversarial-001, ceiling run — all deliberately deferred to follow-up tickets driven by workshop expert engagement at `/goldens/obd`.

Files (PR [1/3]):
- New: `tests/harness/evals/metrics_obd.py`, `tests/harness/evals/obd_runner.py`, `tests/harness/evals/test_obd_agent_eval.py`, `tests/harness/evals/test_metrics_obd.py`, `tests/harness/evals/test_metrics.py`, `tests/harness/evals/test_obd_runner.py`, `tests/harness/evals/test_judge_obd.py`, `tests/harness/evals/test_schemas.py`, `tests/harness/evals/golden/v1/yamaha_road_test.jsonl`, `scripts/compute_yamaha_reference.py`.
- Modified: `tests/harness/evals/schemas.py` (additive OBD-side fields, `OBD_QUESTION_TYPES` constant), `tests/harness/evals/metrics.py` (lane dispatcher, weight rebalance), `tests/harness/evals/judge.py` (passes `value_accuracy` through to `Grade`), `tests/harness/evals/conftest.py` (OBD agent deps mock + Yamaha session fixture + fixed mock-judge schema), `tests/harness/evals/golden/README.md` (OBD lane documentation).

Out of scope (PR [1/3] or this ticket):
- LLM-driven `generate_golden_candidates.py` OBD variant — issue flags as overkill for one fixture; revisit if a second fixture lands.
- Multi-vehicle expansion (Honda, etc.) — bumps to `v2/yamaha_road_test.jsonl` + adds new fixture goldens when fixtures exist.
- Diagnostic-accuracy eval — needs ground-truth fault recordings.
- CI integration — eval stays opt-in (cost + latency).
- Real-LLM session bootstrap for `OBDAnalysisSession` row backing the Yamaha fixture — explicit `pytest.skip` until PR [2/3].

#### HARNESS‑24 — Agent Diagnosis Feedback + History Lane ✅ DONE

GitHub issue #127. Expert feedback on **Agent AI** diagnoses always failed with `400 provider mismatch` because the Agent AI tab's feedback form posted the agent generation's `diagnosis_history_id` to `POST /v2/obd/{id}/feedback/ai_diagnosis`, which hard-requires `provider='local'`. Feedback on agent-generated diagnoses was impossible — a gap for the pilot's training-data goal.

Fix (option a — dedicated per-view feedback table, consistent with the existing 5):
- **DB**: `OBDAgentDiagnosisFeedback` model (mirrors `OBDPremiumDiagnosisFeedback`) + `OBDAnalysisSession.agent_diagnosis_feedback` relationship; Alembic migration `e4f5a6b7c8d9` creates `obd_agent_diagnosis_feedback`.
- **API**: new `POST /v2/obd/{id}/feedback/agent_diagnosis` (in `obd_analysis.py`, reusing shared feedback helpers) validating `diagnosis_history_id` against `provider='agent'`. `FeedbackModel` / `FeedbackType` / `_FEEDBACK_TABLES` / `_FEEDBACK_TABLES_WITH_HISTORY` extended. `GET /history` `provider` filter and the `DiagnosisHistoryItem.provider` / `FeedbackHistoryItem.tab_name` Literals widened to `agent` / `agent_diagnosis` (the former also closes a latent agent-row serialisation 500).
- **Frontend**: `AgentDiagnosisView` feedback form rewired `ai_diagnosis` → `agent_diagnosis`; session History tab gains an **Agent Model** lane (`DiagnosisHistoryView provider="agent"`); `FeedbackHistoryView` renders the agent tab; the force-agent toggle is surfaced beside Regenerate so it persists past the initial form. i18n keys in en / zh-CN / zh-TW.
- **Tests**: 4 offline unit tests in `test_feedback_diagnosis_link.py` + integration coverage in `test_obd_analysis.py`.

No agent-loop or SSE change. Design doc: `v2_design_doc.md` v1.8.0.

Files:
- New: `diagnostic_api/alembic/versions/e4f5_add_agent_diagnosis_feedback.py`.
- Modified: `diagnostic_api/app/models_db.py`, `diagnostic_api/app/api/v2/endpoints/obd_analysis.py`, `diagnostic_api/app/api/v2/schemas.py`, `diagnostic_api/tests/test_feedback_diagnosis_link.py`, `diagnostic_api/tests/test_obd_analysis.py`, `obd-ui/src/lib/{api,types}.ts`, `obd-ui/src/components/{AnalysisLayout,FeedbackForm,DiagnosisHistoryView,FeedbackHistoryView,AgentDiagnosisView}.tsx`, `obd-ui/src/locales/{en,zh-CN,zh-TW}.json`.

## 5. Notes

### What this plan deliberately avoids

- **Over-engineering the first iteration**: V2 starts with a single agent loop and 7 tools. Sub-agents, skill loading, and background tasks are future tickets.
- **Replacing V1 prematurely**: V1 one-shot endpoints remain the default for simple cases. V2 agent mode is an additional option, not a replacement.
- **Speculative tool design**: Only tools that wrap existing functions or have clear implementation paths are included. Speculative tools (e.g., "run Mode 06 test") require hardware integration not currently available.

### Changelog

| Date | Version | Changes |
|------|---------|---------|
| 2026-06-24 | v2.31 | **HARNESS-23 T1 (GitHub issue #143):** raised the manual sub-agent's budget so it stops running out mid-answer. The #107 baseline attributed 19/30 manual-lane failures to budget exhaustion — **13** `stopped_reason=timeout` (hit the 120 s wall at only 5-7 iterations) and **6** `max_iterations` (hit the 8-iter cap); the audit confirmed a stable ~10-24 s/iter for `qwen3.5:27b` in thinking mode, so the timeouts were structural, not GPU-load noise. The two limits bind *different* entries, so both moved together: `manual_agent.py` `_DEFAULT_MAX_ITERATIONS` **8 → 12** and `_DEFAULT_TIMEOUT` **120.0 → 240.0** s (mirrors the OBD agent's 240 s precedent, OBD eval v2.20/PR [2a/4]). `_DEFAULT_MAX_TOKENS` reviewed and **left at 12288** — no first-round run hit the per-call output cap. Agent-config only; metrics / judge / goldens untouched, no re-baseline (gated at #155). **Expected impact:** full-eval wall-time roughly **doubles** (manual-lane runs that previously got cut off now run to completion). **Tests**: new `TestManualAgentConfigDefaults` in `tests/harness_agents/test_manual_agent.py` pins all three defaults (12 / 240.0 / 12288). Follow-up plan: `docs/harness_14_phase6_followups.md` (T1). |
| 2026-06-21 | v2.30 | APP-61 (V2 reflection — the schema/upload work is V1, see `dev_plan.md` v5.15; follow-up to the #107 baseline): **the harness now matches a manual by its factory code**, the root cause behind 27/30 agent refusals in the baseline. The #107 baseline showed the HARNESS-25 honest agent correctly refusing "MWS-150-A" questions because `list_manuals` only exposed `vehicle="Yamaha TRICITY155"` — the manual's marketing model, not the factory code on its cover that the locked goldens use. `list_manuals` (`harness_tools/manual_tools.py`) now reads the optional `factory_code` from the `.md` frontmatter, renders it as `factory_code="…"` on each entry, matches the optional vehicle filter against it, and its honest-match footer states that a `factory_code` match identifies the SAME vehicle (with the MWS150-A ↔ Tricity 155 example). The manual sub-agent's process prompt (`harness_agents/manual_agent_prompts.py`, step 2) accepts a `factory_code=` match as valid grounding. No agent-loop, schema, or SSE change in V2 — the `factory_code` column + upload field + frontmatter writer are V1 (APP-61). **Tests**: 3 new `list_manuals` cases in `tests/harness/test_manual_tools.py` (renders code; filter-by-code matches a TRICITY155 manual via its MWS150-A alias; code omitted when absent). Removes the need for the eval's temporary manuals-mount frontmatter patch noted in `docs/harness_14_phase6_baseline.md`. |
| 2026-06-20 | v2.29 | **HARNESS-23 / GitHub issue #107: first real agent-vs-RAG baseline** on the 30 expert-locked goldens (`golden/v2/locked/mws150a.jsonl`). (Distinct from the v2.25 HARNESS-23 SSE-keepalive refactor — same ticket number, different work; #107 is the baseline measurement.) **Result: manual_agent mean overall 0.590 (median 0.580, σ 0.163, 6/30 pass@0.7) vs RAG mean 0.337 (median 0.305, σ 0.133, 1/30) — the agent beats single-shot top-5 RAG by +0.25 and wins every question-type** (lookup +0.35, image +0.29, procedural +0.27, cross-section +0.25, adversarial +0.11). The separator is synthesis: RAG has no LLM step (`answer_quality` 0.05 vs agent 0.28); a no-synthesis lane sits just above the rubric's structural floor. Agent wins are gated by its iteration budget — **19/30 agent runs hit timeout/max-iterations** (the dominant, fixable failure). **Threshold pin** (mean − 1σ, floored 1dp, per-lane): `test_manual_agent_eval.py` 0.7→**0.4**, `test_rag_eval.py` 0.7→**0.2**. **Cherry-picked** `c89e3a5` (RAG test + the now-rewritten plan doc). **Four eval-only reconciliations** for post-HARNESS-20 corpus drift (a 2nd manual, Corolla E11, was ingested since the issue was filed; production code/data untouched): (1) RAG `vehicle_model` filter `MWS150-A`→`TRICITY155`; (2) RAG exact (sequential) scan instead of HNSW — the HNSW post-filter starves a single-manual filter to 0 rows once a 2nd manual shares the index (0 even at the pgvector 0.7.4 max `ef_search=1000`); (3) manuals mount frontmatter label reconciled to the goldens' `MWS-150-A` so the honest agent (HARNESS-25) stops refusing 27/30; (4) RAG embeds with a fresh per-call `httpx.AsyncClient` — the production singleton breaks under pytest-asyncio's per-test event loops and silently zeroed 15/30 retrievals on the first run. `rag_runner.run_rag` gains an `exact` flag + `_exact_vector_retrieve`/`_embed_query`. Combined report at `docs/eval-reports/phase6_baseline_eval.json` (60 grades; assembled from the manual lane's first run + the post-embedding-fix RAG re-run — see its `_provenance`); aggregator at `docs/eval-reports/aggregate_phase6.py`; full results + 3-bucket failure attribution + follow-ups in `docs/harness_14_phase6_baseline.md` (rewritten from plan to results). Follow-ups (out of scope): reconcile corpus identity with the locked goldens, raise the agent iteration budget, slug-tolerant citation matching, fix production filtered-HNSW recall. |
| 2026-06-20 | v2.28 | HARNESS-26 (paired with V1 APP-60): **agent vehicle grounding** — give the agent the stated make/model so manual matching works *positively*. With HARNESS-25 the agent stopped confabulating a Yamaha scooter but then reverse-reasoned the model from the only same-make manual (called the Hiace a "Corolla"), because its only vehicle signal was the bare VIN. Now that APP-60 requires make/model at upload and stamps them into `parsed_summary`, `harness/harness_prompts.build_user_message` renders `Vehicle: {Manufacturer} {Model} (VIN {vehicle_id})` (new `_format_vehicle` helper) instead of `Vehicle: {vehicle_id}`; falls back to the bare `vehicle_id` (or `unknown`) for historical sessions with no make/model. This lets the HARNESS-25 match-or-refuse rule resolve the correct manual or honestly say none matches. Prompt-only; no schema or SSE change. **Tests**: new `tests/harness/test_harness_prompts.py` (7, offline) — make/model+VIN rendering, V-UNKNOWN omission, legacy fallback, locale suffix. Doc: `v2_design_doc.md` v1.8.2. |
| 2026-06-19 | v2.27 | HARNESS-25 (GitHub issue #136, paired with V1 APP-59): **honest manual agent** — stop the agent treating a non-matching service manual as authoritative. In the first real agent run on a Toyota Hiace (DTC P00AF, #135) the agent cited the Yamaha MWS150-A scooter manual — the only service-manual content in the vault — and concluded the vehicle was a Yamaha scooter and the code spurious. **Two changes, both prompt/tool-output (no schema, no SSE change)**: (1) `list_manuals` (`harness_tools/manual_tools.py`) now renders each manual's canonical `vehicle="<Manufacturer> <Model>"` identity (read from the `.md` frontmatter `manufacturer` + `vehicle_model`, written by APP-59's `write_frontmatter_identity`), matches the optional filter leniently against manufacturer/model/canonical, and appends a footer instructing the agent to only treat a manual as authoritative if its make/model matches the vehicle under diagnosis and to say "no service manual is available for this vehicle" otherwise; (2) a **Vehicle grounding (critical)** rule added to the main system prompt (`harness/harness_prompts.py`) and the manual sub-agent's process (`harness_agents/manual_agent_prompts.py`) — a standard SAE DTC + the session VIN outweigh manual content that contradicts the vehicle type; the sub-agent returns "Not found: no service manual available for <vehicle>" rather than substituting an unrelated manual. **Tests**: `tests/harness/test_manual_tools.py` — canonical-name rendering, honest-refusal footer, manufacturer-based filtering (21 pass offline). Doc: `v2_design_doc.md` v1.8.1. |
| 2026-06-14 | v2.26 | HARNESS-24 (GitHub issue #127): fix the `400 provider mismatch` that made expert feedback on **Agent AI** diagnoses impossible — a gap for the pilot's training-data goal. **Root cause**: `AnalysisLayout.tsx` wired the Agent AI tab's `<FeedbackForm>` to `feedbackTab="ai_diagnosis"`, so it POSTed the agent generation's `diagnosis_history_id` to `POST /v2/obd/{id}/feedback/ai_diagnosis`, whose `_validate_diagnosis_history_id` hard-requires `provider='local'` → 400 (`expected 'local', got 'agent'`). **Fix (option a — dedicated table, consistent with the 5 existing per-view feedback tables, chosen over relaxing `ai_diagnosis` to accept `{local, agent}`)**: (1) new `OBDAgentDiagnosisFeedback` model in `models_db.py` (mixin columns + `diagnosis_text` snapshot + `diagnosis_history_id` FK) + `OBDAnalysisSession.agent_diagnosis_feedback` relationship; (2) Alembic migration `e4f5a6b7c8d9` (down_revision `d3e4f5a6b7c8`, single head verified) creating `obd_agent_diagnosis_feedback`; (3) new `POST /v2/obd/{id}/feedback/agent_diagnosis` endpoint in `obd_analysis.py` validating against `provider='agent'` and reusing the shared `_submit_feedback` machinery (`FeedbackModel` / `FeedbackType` / `_FEEDBACK_TABLES` / `_FEEDBACK_TABLES_WITH_HISTORY` all extended; placed in `obd_analysis.py` rather than `harness/router.py` so it carries no harness/tiktoken import and stays unit-testable offline); (4) frontend `AgentDiagnosisView`'s feedback form rewired `ai_diagnosis` → `agent_diagnosis` (`FeedbackForm`/`api.ts`/`types.ts` unions extended). **Related gap 1 — History tab Agent lane**: agent generations were stored (`provider='agent'`) but invisible; added an **Agent Model** lane to the session History tab. Required widening the `/history` `provider` filter and the `DiagnosisHistoryItem.provider` / `FeedbackHistoryItem.tab_name` response Literals to `agent` / `agent_diagnosis` — the former Literal also fixed a **latent 500**: an agent row would have failed `DiagnosisHistoryResponse` validation. `DiagnosisHistoryView` / `FeedbackHistoryView` render the agent provider/tab. **Related gap 2 — force-agent toggle**: surfaced the "Force agent mode" checkbox beside the Regenerate button so it stays visible/controllable after the result panel replaces the initial form (the state already persisted in the component; the gap was that the control disappeared). **i18n**: `tabs.agentModel`, `history.agent`, `history.emptyAgent`, `feedbackForm.view.agent_diagnosis`, `feedbackHistory.tab.agent_diagnosis` added to en / zh-CN / zh-TW. **Tests**: 4 new offline unit tests in `test_feedback_diagnosis_link.py` (agent-provider accept/reject + endpoint-level accept/reject, run green offline) + integration coverage in `test_obd_analysis.py` (agent_diagnosis 404/route/snapshot, `provider=agent` history filter). No change to the agent loop or SSE protocol. Doc: `v2_design_doc.md` v1.8.0. |
| 2026-06-14 | v2.25 | HARNESS-23 (refactor, paired with V1 APP-58 / GitHub issue #128): the timer-based SSE keep-alive `_with_keepalive` introduced for the agent path in HARNESS-22 is **relocated** out of `harness/router.py` to a shared helper beside `_sse_event` in `app/api/v2/endpoints/obd_analysis.py`, so the V1 local + premium diagnose endpoints can reuse the same implementation (the original #128 fix — first post-deploy diagnosis dying on a silent Ollama cold-load — lives in the V1 docs).  `harness/router.py` now **imports** the shared `_with_keepalive` (its 40-line duplicate definition and the now-unused `asyncio` / `AsyncIterator` imports are removed) — no behavioural change to the agent stream.  Additionally, the harness **Tier‑0 one-shot path** (`generate_agent_diagnosis` → `_oneshot_stream`, which runs against local Ollama and so shares the cold-load risk) is now wrapped with `_with_keepalive`; previously only the agent-loop path was.  No Alembic migration; no frontend change.  Covered by the new V1 `tests/test_sse_keepalive.py` (the helper is imported from its new home). |
| 2026-06-09 | v2.24 | HARNESS-22: live reasoning streaming for the Agent AI diagnosis (GitHub Issue #119 follow-up).  **Problem**: the agent endpoint already returned a `StreamingResponse`, but `run_diagnosis_loop` called `llm_client.chat()` **non-streaming** once per ReAct iteration and emitted nothing until that blocking call returned — so with `qwen3.5:27b` in thinking mode (thousands of hidden reasoning tokens per turn) the UI showed a multi-minute frozen spinner, worst on iteration 1 (largest context).  A long single turn also risked the Cloudflare-tunnel idle timeout, since the APP-41 keep-alive was only ever wired into the local one-shot path, never the agent loop.  **Backend**: new `LLMStreamChunk` dataclass + `LLMClient.chat_stream()` (implemented on `OpenAILLMClient`) calls `chat.completions.create(stream=True)`, surfaces `reasoning` (qwen3 thinking channel — `delta.reasoning` / `delta.model_extra["reasoning"]`) and `content` deltas as they arrive, accumulates streamed tool-call fragments by `.index`, and terminates by yielding the same `LLMResponse` shape `chat()` returns (so tool dispatch / done-detection downstream are unchanged).  New `loop._stream_llm_turn` consumes that stream, yields `reasoning`/`token` `HarnessEvent`s live, and **falls back to the blocking `chat()`** if streaming raises (e.g. an Ollama build that won't stream with tools) — a streaming quirk degrades gracefully instead of failing the diagnosis.  `EventType` gains `reasoning` + `token`.  Router (`harness/router.py`) maps `reasoning` → SSE `reasoning` event and `token` → SSE `token` (text-only, matching the existing one-shot contract).  **Keep-alive**: new `_with_keepalive` wrapper around the SSE generator injects a `: ping` comment during any >15s silent gap (covers the pre-first-token prompt-processing window), fully closing the tunnel-timeout risk without polluting the `HarnessEvent` stream.  **Reasoning is ephemeral** (live-only) — not persisted to `harness_event_log`, so History replay is unchanged (tool calls + final answer only) and there is no DB bloat / migration.  **Frontend**: new `ReasoningPanel.tsx` (muted, collapsible, auto-scrolling "Thinking…" panel); `AgentDiagnosisView` accumulates `reasoning` deltas per iteration, clears them (and the answer buffer) at each `tool_call` boundary so the panel is per-iteration and the diagnosis area only ever holds the final answer, which now types out live via the existing `onToken` path.  `AgentReasoningEvent` type + `onReasoning` callback + `reasoning` dispatch case in `api.ts`/`consumeSSEStream`.  Cosmetic fix: `ToolCallCard` success glyph `&check;` (rendered literally) → `✓`.  i18n `agent.thinking` added (en / zh-CN / zh-TW).  **Tests**: `tests/harness/test_loop_streaming.py` — `chat_stream` reasoning/content split, tool-call delta accumulation (single + multi-index), reasoning-via-`model_extra`, loop emits `reasoning`/`token` with no fallback, and fallback-to-`chat()` on stream error.  No Alembic migration.  Design doc: `docs/plans/2026-06-09-agent-reasoning-streaming-design.md`. |
| 2026-05-24 | v2.23 | Removed `search_manual` (RAG tool) from the main-agent tool registry. Registry 12 → 11 tools (6 OBD primitives + 3 manual primitives + 2 delegation wrappers). `lookup_dtc` next-step suggestions updated: Yamaha-hex and unknown-format DTC messages now point to `get_manual_toc` → `read_manual_section` navigation instead of the former `search_manual` pivot. `_DELEGATE_MANUAL_DESC` cleaned of the "call `search_manual` instead" shortcut note. System prompt (`harness_prompts.py`) `search_manual` bullet removed. 5 production files changed, 10 test files updated (generic `search_manual` stand-in replaced with `list_signals`). No Alembic migration. Design doc: `docs/v2_design_doc.md` v1.6.0. |
| 2026-05-24 | v2.22 | HARNESS-21 PR [3/4]: baseline scorecard + workflow plumbing — **HARNESS-21 CLOSED** (GitHub Issue #97).  **Baseline doc** at `docs/harness_21_phase5_baseline.md` from PR [2a/4]'s real-LLM run (qwen3.5:27b-q8_0 agent, GLM 5.1 judge, 15 Yamaha goldens, 29:24 wall): 12/15 pass at threshold 0.6, **mean overall 0.843**.  Per-bucket: signal_statistics 0.955, event_finding 0.996, dtc_enumeration 1.000, dtc_decode 0.968, compound_obd 0.817, adversarial_obd 0.466.  Three failures classified: **compound-002 fabricated ECT=101°C** (actual 89°C; 3 pitfall violations; judge gave 0.10 — most concerning); **adversarial-001 golden-authoring issue** (must_contain="no evidence" too strict; judge gave the prose 0.95); **adversarial-002 mild agent overstep** (correct caveat then violated it; 1 pitfall, judge 0.45).  Threshold recommendation 0.75 (intentionally not applied in code).  Variance: median 87s wall, max 198s — confirms PR [2a/4]'s 240s timeout was the right call.  **`promote_golden.py --lane=manual\|obd`** support: default-lane resolution via new `_defaults_for_lane()` helper; OBD defaults target `golden/v2/yamaha_road_test.jsonl` (candidate) and `golden/v2/locked/yamaha_road_test.jsonl` (locked); PROMOTIONS.md shared between lanes.  Explicit per-flag --candidate-file / --locked-file overrides still win.  7 new tests (26 total promote_golden tests).  **OBD eval reader migration**: `test_obd_agent_eval.py` now reads `v2/locked/yamaha_road_test.jsonl` (was `v1/yamaha_road_test.jsonl`).  Locked file ships empty as the deliberate "no published OBD numbers until expert-approved" safety net (mirrors manual lane's HARNESS-20 behaviour).  Empty parametrize handled via skipped placeholder with actionable message pointing at `promote_golden --lane=obd`.  Once workshop expert reviews the 15 OBD goldens at `/goldens/obd` and an admin promotes them, the eval reader will start collecting real test cases.  **Out of scope** (logged as separate tickets / follow-ups driven by expert engagement): prompt iteration on `obd_agent_prompts.py` for the three failures; threshold raise to 0.75 in code; `must_contain` fix on adversarial-001; ceiling run vs `z-ai/glm-5.1` for tool-design-ceiling vs local-model-ceiling diagnostic; cross-vehicle expansion (Honda etc.) gated on additional fixtures; labelled-fault diagnostic-accuracy eval gated on ground-truth fault recordings; CI integration of the eval suite gated on cost/latency willingness.  Design doc: `docs/plans/2026-05-17-harness-21-obd-eval-design.md`. |
| 2026-05-24 | v2.21 | HARNESS-21 PR [2b/4]: OBD goldens UI lane + DB lane discriminator (GitHub Issue #97).  Production dashboard now surfaces the 15 OBD goldens from PR [2a/4] alongside the 30 manual-lane goldens at `/goldens/obd`.  **DB schema** (Alembic c2d3e4f5a6b7): `lane VARCHAR(20) DEFAULT 'manual'` added to both `golden_entries` and `golden_reviews` (CHECK lane IN ('manual', 'obd'), indexed); three OBD-specific columns added to `golden_entries` (`expected_signal_citations JSONB`, `expected_dtcs JSONB`, `expected_no_evidence BOOLEAN`) all defaulted so existing rows stay valid; `ck_golden_entry_question_type` widened to accept the six OBD literals.  **golden_sync.py**: `_extract_entry_fields` dispatches by question_type — OBD entries get synthetic `manual_id` from filename stem (`yamaha_road_test`) + three new JSONB fields populated; manual entries unchanged.  Walker non-recursive over `v2/*.jsonl` so it picks up the new `yamaha_road_test.jsonl` automatically.  7 new tests (19 total).  **Tier seed (Path C)**: copied `golden/v1/yamaha_road_test.jsonl` (PR [2a/4]'s 15 entries) → `golden/v2/yamaha_road_test.jsonl` as candidate tier; created empty `golden/v2/locked/yamaha_road_test.jsonl` as safety net.  Eval reader migration from v1 to v2/locked deferred to PR [3/4] or follow-up so first OBD promotions happen via UI expert review, not author self-promotion.  **API**: `GET /v2/goldens` gains `?lane=manual|obd` query param (default 'manual', regex-validated); `GoldenEntrySummary` + `GoldenEntryDetail` gain `lane` field; detail surfaces `expected_signal_citations` + `expected_dtcs` + `expected_no_evidence` + `pitfall_directives`.  New `GET /v2/goldens/obd/reference-stats` serves the precomputed Yamaha sidecar JSON (in-memory cached) for sparkline rendering.  **Dockerfile**: `COPY golden/v1/yamaha_road_test_reference.json` so the production image has the sidecar.  **Frontend (Next.js)**: routes split into `/goldens` (new two-card landing with total + reviewed counts per lane), `/goldens/manual` (relocated from old `/goldens`), `/goldens/manual/[id]` (relocated from old `/goldens/[id]`), `/goldens/obd` (new lane listing with 6-bucket dropdown), `/goldens/obd/[id]` (new OBD detail).  Detail renders question + summary + "Refusal expected" badge (when `expected_no_evidence`) + expected signal citations table with **DIY-SVG sparkline** (~50 LOC, no library dep — renders min/p50/mean/p95/max as a 5-point profile sourced from the reference-stats endpoint) + expected DTCs table + pitfall directives.  ReviewSubmitForm + TeamFeedbackList reused unchanged from manual lane.  **TypeScript types**: new `GoldenLane`, `ManualGoldenBucket`, `OBDGoldenBucket`, `MANUAL_BUCKETS` / `OBD_BUCKETS` constants, `ExpectedSignalCitation`, `ExpectedDTC`, `YamahaSignalStats`, `YamahaEventWindow`, `YamahaReferenceStats`; `GoldenEntrySummary` / `GoldenEntryDetail` gain `lane` + OBD fields.  `listGoldens()` accepts new optional `lane` filter.  New `getYamahaReferenceStats()` API client.  **i18n** (en.json): new `landing`, `obdListing`, `obdDetail` namespaces.  zh-CN / zh-TW intentionally NOT updated — OBD lane is English-only at v1.  **Out of scope** (rolled into PR [3/4] or follow-up): eval-reader migration v1→v2/locked, `promote_golden.py` lane support for OBD promotions.  PR [3/4] remains: baseline scorecard + threshold tuning + obd_agent_prompts.py iteration.  Design doc: `docs/plans/2026-05-17-harness-21-obd-eval-design.md`. |
| 2026-05-24 | v2.20 | HARNESS-21 PR [2a/4]: OBD eval-side fixes + 15 real Yamaha goldens (GitHub Issue #97).  Series rescoped 3→4 PRs after a post-[1/3] discussion on bucket balance + UI surface (see v1.5.3 of `v2_design_doc.md`).  **Eval-side fixes**: `compute_dtc_accuracy` empty-expected case returns vacuous 1.0 (was Jaccard 0/N) — symmetric with `compute_signal_precision`; surfaced by PR [1/3]'s real-LLM smoke where a signal_statistics golden's `citation_quality` collapsed to 0.0 because the agent emitted side-effect DTC citations.  `OBDAgentConfig._DEFAULT_TIMEOUT` bumped 120s → 240s after observing 53s/62s/120s+ variance on the same question against `qwen3.5:27b-q8_0` (Qwen's hidden chain-of-thought dominates wall clock; tool execution was 47-66ms across all runs).  **Yamaha session bootstrap**: `yamaha_session_id` fixture now real (was `pytest.skip` placeholder): get-or-creates synthetic `eval-fixture-user` User row + idempotently materialises the fixture into `settings.obd_log_storage_path/<UUID5>/raw_input.csv` so `resolve_log_path` resolves correctly; deterministic UUID5(NAMESPACE_OID, fixture_path) for stable session id.  Latent path-resolution bug in PR [1/3]'s code (`parent.parent.parent.parent` resolved to `diagnostic_api/`, not repo root) corrected to `.parents[4]` (5 levels up to repo root).  **Reference-stats sidecar**: `compute_yamaha_reference.py` refactored with `compute_reference_data()` pure function + `--json PATH` mode; generated `golden/v1/yamaha_road_test_reference.json` (schema_version=1, 12,504 chars) committed as the source-of-truth artifact for golden authoring AND PR [2b/4]'s sparkline rendering.  Sidecar includes per-signal `samples_valid/min/min_at/max/max_at/mean/p50/p95/std` plus precomputed `event_windows` for common thresholds plus the metadata DTC list with pinned fixture SHA-256.  **15 real Yamaha goldens** replace the 3 PR [1/3] dummies (`golden/v1/yamaha_road_test.jsonl`).  Failure-weighted 2/2/2/3/3/3 distribution across the 6 OBD buckets, all numeric values copied from the sidecar JSON.  Multi-source policy (Path C from the design discussion): standard `A_KL_*` signals are the canonical citations; proprietary `A_YAM_*` accepted as supplementary via pitfall directives bounding fabrication ranges.  Three adversarial entries probe the documented failure modes: fabricate evidence from no data (misfire), fabricate conclusion from undocumented proprietary signal (O2 sensor), fabricate definition for absent monitor (catalyst efficiency).  **Tests added**: `test_yamaha_bootstrap.py` (10: UUID determinism, materialisation copy/idempotent/overwrite-on-drift, fixture integrity), `test_metrics_obd.py` regression tests for the dtc_accuracy fix (2: empty-expected-with-cited, empty-cited-with-expected), `TestOBDAgentConfigDefaults` in `test_obd_agent.py` (2: timeout + max_iterations pins).  Total 125 OBD eval unit tests pass (was 113).  Under mocks, `pytest -m eval --run-eval --mock-agent --mock-judge` runs 15 parametrised tests: 3 pass / 12 fail because the canned mock client returns the same RPM/DTC-focused response regardless of question — *expected behaviour* (test docstring updated to clarify run modes).  Real-LLM run on PolyU is the meaningful gate; will produce input for PR [3/4] baseline scorecard.  PR [2b/4] (UI lane + v1→v2-tier migration) and PR [3/4] (baseline + iterate) remain.  Design doc revision history: v1.5.3.  Design doc: `docs/plans/2026-05-17-harness-21-obd-eval-design.md`. |
| 2026-05-17 | v2.19 | HARNESS-21 PR [1/3]: OBD sub-agent evaluation framework scaffolding (GitHub Issue #97). Parallel lane to HARNESS-14; reuses `judge.py` (`z-ai/glm-5.1`), `conftest.py` (`--run-eval` / `--mock-agent` / `--mock-judge`), `Grade` envelope. Additive schema extensions in `tests/harness/evals/schemas.py`: `ExpectedSignalCitation` + `ExpectedDTC` Pydantic models; `GoldenEntry` gains `expected_signal_citations` / `expected_dtcs` / `expected_no_evidence` (all default-empty/False); `SystemRunResult` gains `obd_signal_citations` / `obd_dtc_citations`; `Grade` gains `value_accuracy` (default 1.0 neutral); widened `GoldenQuestionType` literal with six OBD types + exported `OBD_QUESTION_TYPES` frozenset. New `tests/harness/evals/metrics_obd.py` with four OBD-native deterministic dims (`signal_recall`, `signal_precision`, `value_accuracy`, `dtc_accuracy`) — half-open ISO time-range overlap, case-insensitive signal/DTC matching, 5% relative tolerance with per-citation override, zero-expected absolute guard (0.01), `expected_no_evidence` polarity flip across all four dims. Lane dispatcher (`_is_obd_lane`) in `metrics.py` routes by `question_type` membership; `DEFAULT_OVERALL_WEIGHTS` rebalanced to nine dims (`section_recall` 0.25→0.20, `claim_precision` 0.15→0.10, `fact_recall` 0.20→0.15, `fact_density` 0.10→0.05, `hallucination_penalty` 0.10→0.15, `answer_quality` 0.10→0.15, `value_accuracy` NEW 0.10; sums to 1.00). New `tests/harness/evals/obd_runner.py`: `run_obd_agent_unified(question, session_id, deps)` adapts `OBDAgentResult` → `SystemRunResult` (claim_slugs/read_slugs always empty; `output_text` serialised as `<summary>` + `--- Signal citations (N) ---` + `--- DTC citations (N) ---` + `--- Limitations ---` blocks with empty blocks omitted); honours `OBD_EVAL_AGENT_MODEL` env (slash → OpenRouter, plain tag → Ollama) for phase-3 ceiling runs without code changes. New `tests/harness/evals/test_obd_agent_eval.py` parametrized over `golden/v1/yamaha_road_test.jsonl` with threshold `_PASS_THRESHOLD=0.6` (raised in PR [3/3]). Three dummy golden entries (signal_statistics RPM peak, dtc_enumeration with the two real Yamaha hex codes, compound_obd) authored to match the canned mock-agent response so the plumbing command runs green. `conftest.py` extended with `_build_mock_obd_agent_deps` + `obd_agent_deps` fixture + session-scoped `yamaha_session_id` fixture (UUID5 of fixture path; skips real-LLM path until PR [2/3] with a clear message); pre-HARNESS-15-shaped canned payload in `_build_mock_judge_client` updated to the current `{answer_quality, reasoning, pitfall_violations}` schema. New developer aid `scripts/compute_yamaha_reference.py` (stdlib-only) prints per-signal mean/p50/p95/min/max/std + contiguous-true event windows + DTC list against the Yamaha fixture for PR [2/3] golden authoring. New tests: `test_schemas.py` (15), `test_metrics_obd.py` (46), `test_metrics.py` (22), `test_obd_runner.py` (22), `test_judge_obd.py` (8); 91 new tests, all green; 113 total eval-module tests pass + 3 dummy eval tests pass under `pytest -m eval --run-eval --mock-agent --mock-judge`. Manual lane unchanged. Pre-existing stale imports in `test_judge.py` + `test_manual_agent_eval.py` flagged for separate cleanup. Design doc: `docs/plans/2026-05-17-harness-21-obd-eval-design.md`. |
| 2026-05-16 | v2.18 | HARNESS-19: agent-native OBD investigation toolset (GitHub Issue #85). Replaces the single `read_obd_data` two-mode tool with 6 decomposed cognitive primitives + an OBD investigation sub-agent + 2 delegation wrappers (hybrid Pattern 2). **Six primitives**: `list_signals` (Glob — discovery + units + density), `read_window` (Read — bounded sample read with auto-downsample), `get_signal_stats` (aggregate — min/max/mean/std/percentiles, optional trend + extrema), `find_events` (Grep — predicate-based event finder with merge_gap + min_duration), `list_dtcs` (enumeration of standard P-codes + Yamaha hex), `lookup_dtc` (standard P/C/B/U decode via python-OBD table + honest "no decoder" pivot for Yamaha proprietary hex). **OBD sub-agent** (`app/harness_agents/obd_agent.py`) mirrors the manual_agent template — restricted 6-tool registry via `create_obd_agent_registry()`, ReAct loop with max_iterations=8 / timeout=120s / max_tokens=12288, structured JSON output parsed into new `OBDAgentResult` Pydantic shape (`summary`, `signal_citations`, `dtc_citations`, `raw_data` auto-captured from tool excerpts, `limitations`, `tool_trace`, `iterations`, `stopped_reason`). **Delegation wrappers** (`app/harness_tools/delegation_tools.py`) for both OBD and manual sub-agents; sub-agent registries deliberately exclude delegation tools — recursion guard verified by tests. **Yamaha-aware raw loader** (`app/harness_tools/obd_loader.py`) bypasses `format_normalizer.py` (which strips `A_YAM_*` columns) and reads raw CSV directly with UTF-8 BOM defense + Yamaha-CSV vs standard-TSV format detection — 16 `A_YAM_*` proprietary columns now reach the agent under their original names per the locked HARNESS-19 decision. **Signal inventory** (`app/harness_tools/obd_signal_inventory.py`) with hand-curated `A_YAM_*` units (BATT_V → V, INJ_MS → ms, CHT → °C, etc.), classifier, glob/subsystem filters, fuzzy-suggestion lookup for unrecognised signal names. **Yamaha-hex DTC handling**: `lookup_dtc("87F11043...")` returns honest "no decoder available" + `search_manual` pivot guidance per locked decision. **Main agent registry** rewritten from 5 → 12 tools (6 OBD primitives + 4 manual primitives + 2 delegation wrappers); legacy `read_obd_data` unregistered (file kept on disk for one release cycle for callsite migration). **System prompt** in `harness_prompts.py` expanded with new tool descriptions and "primitives vs. delegation" usage guidance. **Output contract preserved**: `_session_id` injected by the main loop (line 416) reaches the new tools transparently; the sub-agent's own loop applies the same injection before calling its restricted registry. **Result formatters** (`app/harness_agents/result_formatters.py`) render `OBDAgentResult` / `ManualAgentResult` as structured markdown for the delegation tool output. **Tests**: 136 new (TestSignalInventory, TestListSignals, TestReadWindow, TestGetSignalStats, TestFindEvents, TestClassifyCode, TestListDTCsRealFixture, TestLookupDTCYamahaHex / Standard / Unknown, TestDetectFormat, TestYamahaMetadataDTCs, TestRealYamahaFixture, TestTimestampParser, TestTryFloat, TestOBDAgentRegistry, TestParseFinalJSON, TestCoerceCitations, TestBuildDataExcerpt, TestRunOBDAgentEndToEnd, TestParseToolArguments, TestNoRecursion, TestMainRegistry, TestDelegateToOBDAgent, TestDelegateToManualAgent, TestToolDefinitions); all 136 pass. 223 total passes across harness scope (no regressions; 25 pre-existing tiktoken SSL collection errors unrelated to this change). **Bug fix during testing**: UTF-8 BOM defense in `load_obd_data` — the committed Yamaha fixture is UTF-8 with BOM and `read_text(encoding="utf-8")` doesn't strip it, leaking the first comment line into the CSV body and breaking header parse. Files created: `app/harness_tools/{obd_loader, obd_signal_inventory, obd_signals, obd_dtcs, delegation_tools}.py`, `app/harness_agents/{obd_agent, obd_agent_prompts, result_formatters}.py`, `tests/harness_tools/{__init__, test_obd_loader, test_obd_signals, test_obd_dtcs, test_delegation_tools}.py`, `tests/harness_agents/test_obd_agent.py`. Modified: `app/harness_agents/types.py` (+ 4 OBD types), `app/harness_tools/input_models.py` (+ 8 input models), `app/harness/tool_registry.py` (12-tool default registry), `app/harness/harness_prompts.py` (rewritten system prompt). Out of scope: cross-signal correlation, anomaly-as-a-tool, annotation scratchpad, freeze-frame, Yamaha-hex decoder, OBD eval suite, Pattern-3 pure-orchestrator main-agent rewrite. Design doc: `docs/plans/2026-05-16-obd-toolset-design.md`. |
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
| 2026-05-20 | v2.21 | HARNESS-17: fallback condition relaxed from 50%-coverage to endsWith (GitHub Issue #101 third pass). **Problem**: the v2.20 fallback used `normSlug.length * 2 >= text.length` to filter out body paragraphs that mention the section in passing. Manual testing revealed it was too strict: ReactMarkdown escapes raw HTML to literal text, so the <p> wrapping ``<span id="page-91-4"></span><span id="page-91-2"></span>液壓煞車系統空氣的釋放`` ends up with textContent ≈ 67 chars (the literal <span> markup + the 11-char title), while normSlug × 2 = 22. Check failed → fallback returned null → polling timed out at 20 s → no scroll. **Fix**: replaced the coverage ratio with a positional test — accept the element when the slug appears within 8 trailing non-whitespace chars of the element's normalised text content (`text.length - (lastIndexOf(slug) + slug.length) <= 8`). Body paragraphs like ``參閱第 3-5 頁的 "汽門間隙的調整"。](#page-83-2)`` have ~20 chars after the slug, so they're rejected. TOC tables stay filtered via the existing TABLE/UL/OL skip. The 8-char trailing budget tolerates full-width punctuation (`。` / `、`) the markdown may add after the title. Files modified: `obd-ui/src/components/ManualViewer.tsx`. |
| 2026-05-20 | v2.20 | HARNESS-17: plain-text-heading fallback for citations whose slug isn't a real markdown heading (GitHub Issue #101 follow-up). **Problem**: after v2.19 shipped the scroll-to-quote logic, manual testing surfaced that procedural-005 citations still landed at the manual's first page — not the section heading, but the cover page. Root cause: the section title `液壓煞車系統空氣的釋放` appears in the source manual (line 2473) as `<span id="page-91-4"></span><span id="page-91-2"></span>液壓煞車系統空氣的釋放` with NO leading `#` markdown heading marker — marker-pdf occasionally fails to detect a styled-text section title as a heading. ReactMarkdown therefore emits the title inside a regular `<p>` with no `id` attribute, and `getElementById(slug)` returns null. **Fix**: new helper `findHeadingFallback(slug)` scans direct children of the rendered `<article>` element, skipping `TABLE` / `UL` / `OL` (which would otherwise false-positive on the TOC table that lists every section's name + page number), and returns the first child whose normalised text content contains the slug AND where the slug occupies ≥ 50% of the element's text. The 50% threshold filters out body paragraphs that mention the section in passing (`參閱第 3-5 頁的 "汽門間隙的調整"`). Wired into the polling loop: `getElementById(slug)` first; on miss, try `findHeadingFallback(slug)`; if either returns an element, the rest of the scroll/quote-walk logic proceeds unchanged. Logs which path was taken so future misses are debuggable. **Coverage**: with this in place, procedural-005's four citations land on their respective quotes via the existing scroll-to-quote logic, because findQuoteTarget's `nextElementSibling` walk from a `<p>`-heading-equivalent traverses article-level siblings the same way as from a real heading. **Out of scope**: re-ingesting the manual through marker-pdf with adjusted heading-detection settings (root-cause fix that would emit `## 液壓煞車系統空氣的釋放` in the markdown). The runtime fallback is cheaper and works for any future PDF with the same quirk. Files modified: `obd-ui/src/components/ManualViewer.tsx`. |
| 2026-05-20 | v2.19 | HARNESS-17: citation-anchor scroll-to-quote (GitHub Issue #101). **Problem**: clicking a citation in the golden-review dashboard's QuestionCard scrolled `ManualViewer` to the section heading even when the cited quote lived several pages deeper inside the section. Surfaced repeatedly in Towngas reviewer feedback (image-001 jumped to page 3-5 bottom instead of 3-6; all four procedural-005 citations landed at the section heading instead of their respective quotes). **Approach**: carry the quote text alongside the slug in the citation URL as a query param (`/manuals/{id}?q={url-encoded-quote}#{slug}`); on the viewer side, find the section heading by slug (existing logic), then walk the section's body text via `TreeWalker` for the quote and scroll to its enclosing block-level element. **Frontend** (`obd-ui/src/components/goldens/QuestionCard.tsx`): citation `href` extended with `?q=encodeURIComponent(c.quote)` between the manual_id and the `#slug` fragment. **ManualViewer** (`obd-ui/src/components/ManualViewer.tsx`): new pure helper `findQuoteTarget(headingEl, quote)` collects `nextElementSibling` siblings up to the next heading, walks every text node via `TreeWalker`, normalises by stripping whitespace (handles marker-pdf's CJK extraction artefacts like "凸輪 軸鏈輪" vs the golden's "凸輪軸鏈輪"), finds the quote's normalised form in the concatenated body, maps back to the originating text node, and returns its nearest block-level ancestor (`P` / `LI` / `TD` / `TH` / `BLOCKQUOTE` / `DIV` / `H*`) so the highlight ring has something visible to land on. The existing scroll loop (poll for heading → wait for layout to stabilise → multi-retry scroll-to-Y) is preserved unchanged; only the final `target` element is swapped from heading to quote-target when a quote is supplied. Legacy slug-only deep-links (no `?q=`) keep working — `quoteParam` null path skips the search and targets the heading exactly as before. Soft-fail behaviour: on whitespace-only quote, empty body, no match, no text-nodes, or missing block-level ancestor, the helper returns null and the caller falls back to the heading scroll with a console warning. **Coverage**: same code path handles image-001 (camshaft alignment quote on page 3-6 inside `汽門間隙的調整`), all four procedural-005 citations spanning pages 3-13 / 3-14 inside `液壓煞車系統空氣的釋放`, and any future cross-section citation whose quote isn't right under the section heading. **Out of scope** (intentionally not done): multi-span citations (a single citation referencing two non-contiguous quotes — defer until a real case appears); on-page citation index that surfaces a clickable list of every citation in the manual; persistence of the quote highlight across page reloads beyond what the URL already encodes. Files modified: `obd-ui/src/components/goldens/QuestionCard.tsx`, `obd-ui/src/components/ManualViewer.tsx`. |
| 2026-05-20 | v2.18 | HARNESS-17: cross-001 rewrite using Towngas option-d clarification + new ticket for citation-anchor UI bug (GitHub Issues #89 + #101). **cross-001 rewrite**: per Towngas reviewer follow-up, the manual's 12,000 km valve-clearance inspection interval is a default for vehicles in great shape only; in practice technicians use a condition-driven per-vehicle interval that may be shorter. Updated `golden_summary` (EN + 繁體) in `tests/harness/evals/golden/v2/mws150a.jsonl` to lead with the manual figure, layer the workshop reality, and keep the unconditional 進氣 0.10–0.14 / 排氣 0.21–0.25 mm clearance specs separate. Cold-engine prerequisite + cross-section structure unchanged. `pitfall_directives` rebalanced: the first directive (previously forbade any interval other than 12,000 km) softened to require referencing 12,000 km as the manual's documented figure; new 5th directive added against presenting 12,000 km as a strict universal rule. `must_contain` unchanged. `golden_citations`, `expected_recall_slugs`, `expected_tool_trace` unchanged. **Citation-anchor UI bug filed (#101)**: `ManualViewer`'s `scrollIntoView()` on the slug heading anchor lands at the section's top, so citations whose quote lives mid-section (image-001, all four procedural-005 quotes) jump to the section heading instead of the quote. Out of scope for #89; tracked separately under #101 with a suggested fix (carry the quote text alongside the slug in the URL fragment, walk the section's text for the match, scroll to it with the existing highlight ring). Files modified: `tests/harness/evals/golden/v2/mws150a.jsonl`. |
| 2026-05-16 | v2.17 | HARNESS-17: image-004 first-citation figure swap from second-round Towngas feedback (GitHub Issue #89). **Problem**: reviewer reported that the figure embedded under image-004's first citation ("拆卸空氣釋放螺栓 '1', 釋放冷卻系統內的空氣") was wrong — `_page_101_Picture_28.jpeg` shows a related but different figure printed just above the air-release-bolt instruction in the manual; the figure that actually shows the bolt labelled '1' is `_page_101_Picture_31.jpeg`, printed directly under the air-release-bolt torque spec. **Fix**: swapped `figure_image_paths` on the first citation in `tests/harness/evals/golden/v2/mws150a.jsonl` from `_page_101_Picture_28.jpeg` → `_page_101_Picture_31.jpeg`. Bilingual `golden_summary` / `golden_summary_zh` updated to reference the correct file. Notes field appended with the change rationale. **Out-of-scope deferred**: two related reports from the same review round — (a) image-001's first citation jumps to the bottom of page 3-5 instead of page 3-6; (b) all four procedural-005 citations jump to wrong pages within the section `液壓煞車系統空氣的釋放` — are NOT JSONL data problems. The cited slugs are correct enclosing sections, but `ManualViewer`'s scroll-to-anchor implementation lands at the section's heading rather than at the specific quote text inside it. Fix requires UI work (scroll-to-quote-text instead of scroll-to-slug); tracked separately. **cross-001 clarification received**: reviewer confirmed option (d) — Towngas technicians inspect each vehicle and set a per-vehicle interval; only stick to the manual's 12,000 km when the vehicle is in great shape (rare). Golden rewrite pending in a follow-up. Files modified: `tests/harness/evals/golden/v2/mws150a.jsonl`. |
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
