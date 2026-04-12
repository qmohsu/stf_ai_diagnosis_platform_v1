# V2 Development Plan — Harness Engineering (v1.0)

**Agent-driven vehicle diagnosis via harness loop, tool registry, and graduated autonomy**

| Field | Value |
|-------|-------|
| **Architecture doc** | `docs/v2_design_doc.md` |
| **GitHub Issue** | #26 (discussion: From Context Engineering to Harness Engineering) |
| **Version** | v1.0 |
| **Last updated** | 2026-04-10 (HARNESS-06 done) |

## 1. Scope Boundary

### 1.1 In Scope

- Core harness loop (agent loop pattern, async generator)
- Tool registry with dispatch map + 7 tool wrappers (4 existing + 3 new)
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
- Skill loading system (domain-specific MD files — future HARNESS-10)
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

#### HARNESS‑10 — Skill Loading (Domain-Specific Knowledge)

Depends on: HARNESS-02
Scope: On-demand domain knowledge injection. Skill descriptions in system prompt; full skill content loaded via `load_skill` tool when the LLM requests it. Example: `engine-diagnosis.md`, `transmission-diagnosis.md`.
Reference: Learning notes S05.

#### HARNESS‑11 — Background Agent Tasks

Depends on: HARNESS-08
Scope: Long-running agent sessions that execute in the background. Notification when complete. For multi-vehicle fleet analysis or overnight batch diagnosis.
Reference: Learning notes S08.

#### HARNESS‑12 — Case Library Tool (Feedback-Driven Learning)

Depends on: HARNESS-08
Scope: Use stored expert feedback to build a case library. Tool retrieves past cases where feedback was positive (helpful=true, rating≥4) and includes the expert-validated root cause.

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
