# STF AI Diagnosis Platform — V2 Harness Architecture

**From Context Engineering to Harness Engineering: Agent-Driven Vehicle Diagnosis**

## Document control

| Field | Value |
|-------|-------|
| **Doc title** | V2 Harness Architecture for AI-Assisted Vehicle Diagnosis |
| **Project** | STF AI Diagnosis Platform — Phase 1 Pilot |
| **Status** | Draft v0.8 |
| **Owner** | Li-Ta Hsu |
| **Contributors** | ML engineers; backend engineers; frontend engineers |
| **Last updated** | 2026-04-23 (v1.1) |
| **Primary pilot stack** | FastAPI + AsyncOpenAI (OpenRouter) + Ollama + pgvector (PostgreSQL) + Next.js |
| **New in this revision** | HARNESS-14 phase 1: scaffolding for the manual-agent evaluation suite (GitHub Issue #73). Adds §12.5 describing a restricted manual-search sub-agent graded by `z-ai/glm-5.1` (HK-accessible, Claude/OpenAI/Gemini geo-blocked per #23) against a human-reviewed frozen golden set. Phase-1 deliverables: Pydantic schemas (`GoldenEntry`, `ManualAgentResult`, `Grade`), stub runner + judge, parametrized pytest with `--run-eval` CLI gate, session-scoped `eval_report` fixture, 3 dummy golden entries. Immutable-version strategy (`v1/` → `v2/`) prevents silent eval-set drift. Known limitation: one-manual corpus (MWS150A only) until a second manual is acquired. |

### Revision history

| Version | Date | Summary |
|---------|------|---------|
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
| **Input** | `{ "vehicle_model": "MWS-150-A" }` (optional filter) |
| **Output** | Text listing each manual's ID, vehicle model, page count, and section count |
| **Implementation** | Scans `manual_storage_path` for `.md` files, parses YAML frontmatter |
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
| `OBDAnalysisSession` | Unchanged. V1 pipeline populates `parsed_summary_payload` before agent loop starts. |
| `DiagnosisHistory` | Extended: `provider` CHECK constraint adds `"agent"` alongside `"local"` and `"premium"`. |
| `HarnessEventLog` | **New table**. FK to `OBDAnalysisSession`. |

**Alembic migration**: Adds `HarnessEventLog` table and updates `DiagnosisHistory.provider` CHECK constraint.

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

Separate from the unit / integration / E2E layers above, this suite measures how well a **restricted manual-search sub-agent** uses the 4 manual-navigation tools (`list_manuals`, `get_manual_toc`, `read_manual_section`, `search_manual`) to answer diagnostic inquiries. It isolates tool-use quality from OBD analysis quality and catches behaviours that deterministic unit tests miss — hallucinations, parameter misunderstanding, inefficient tool-call sequences, and omitted information.

**Architecture.** A thin restricted ReAct loop (max ~8 iterations, ~12K output tokens) calls only the 4 manual tools — `read_obd_data` is explicitly excluded. The agent returns a structured `ManualAgentResult` (summary, citations, raw_sections, tool_trace, iterations, total_tokens). The output is graded by `z-ai/glm-5.1` via OpenRouter (temperature 0, JSON mode) against a human-reviewed golden entry (`GoldenEntry` — question, golden_summary, golden_citations, must_contain, must_not_contain, expected_tool_trace).

**Judge rubric.** The judge returns a `Grade` with five dimensions: `section_match` (did the agent cite the golden slug?), `fact_recall` (fraction of `must_contain` items present), `hallucination` (any `must_not_contain` found?), `citation_present`, and `trajectory_ok` (≤1.5× expected tool count, no brute-force read-all). Overall = 0.4·section_match + 0.3·fact_recall + 0.2·(1−hallucination) + 0.1·citation_present. Trajectory is reported but not enforced in the pass threshold so cost regressions surface without failing tests.

**Model selection under HK constraint.** The PolyU server is in Hong Kong; Claude, OpenAI, and Gemini are geo-blocked (see §10 of `docs/v2_dev_plan.md` and Issue #23). Locked choices: judge = `z-ai/glm-5.1`; agent primary = local `qwen3.5:27b-q8_0` (what ships); phase-5 ceiling comparison = `z-ai/glm-5.1` or `moonshotai/kimi-k2`.

**Golden set immutability.** Goldens live under `tests/harness/evals/golden/v{N}/`. Once `v1/` is frozen, entries are immutable — corrections bump to `v2/`. This prevents silent eval-set drift and keeps historical eval reports comparable. Generation is grounded: Claude reads a specific manual section and emits one `(question, summary, citations)` tuple, then a human accepts / edits / rejects before promotion from `candidates/` to `v{N}/`.

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
| RAG retrieval | `retrieve_context()` | `diagnostic_api/app/rag/retrieve.py:115` | `search_manual` | `harness_tools/rag_tools.py` |
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
