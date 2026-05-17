# Design: Agent-Native OBD Investigation Toolset (HARNESS-19)

**Date**: 2026-05-16
**Status**: Approved
**Author**: Xiangzhu Yan
**Ticket**: [HARNESS-19 (#85)](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/85)
**Doc track**: V2 (harness / agent loop)

## Context

The V2 harness has the manual toolset working (`list_manuals`, `get_manual_toc`,
`read_manual_section`, `search_manual`) and the eval framework is maturing in
parallel (#74). On the OBD side only one tool exists — `read_obd_data`
(overview + signal-query modes) — produced by the #69 toolset redesign.

As of 2026-05-09 we also have **one real Yamaha road-test recording** committed
as a regression fixture: `obd_agent/fixtures/yamaha_dual_road_test_20260508.csv`
— 4 min 17 s, 257 samples at 1 Hz, dual-channel (K-Line engine ECU +
CAN ABS ECU), healthy bike, 27 columns including 11 K-Line canonical PIDs and
16 Yamaha-proprietary `A_YAM_*` fields, plus 2 stored Yamaha-hex DTCs.

With real signal data in hand, it is time to design and scaffold the rest of
the OBD-side toolset — clean-slate, not a port of the V1 deterministic
pipeline.

## Problem

`read_obd_data` covers only the discovery + raw-window-read pair of cognitive
primitives. An agent investigating real OBD data also needs:

1. **Aggregation primitives** — without them, the agent burns tokens pulling
   raw rows and doing arithmetic LLMs are unreliable at.
2. **Event-finding primitives** — Grep-style "when does this signal meet this
   condition?" — collapses 257 raw samples into a handful of useful windows.
3. **DTC-specific tools** — the fixture has 2 stored DTCs in Yamaha hex format
   (`87F11043…`), but no list/lookup/decode tools exist. Today the agent sees
   raw hex strings with no path forward.
4. **A_YAM_\* proprietary signals exposure** — `format_normalizer.py` strips
   all `A_YAM_*` fields, losing ~60% of the Yamaha fixture's signal richness
   (battery voltage, injection pulse, cylinder head temp, etc.).
5. **A specialist sub-agent** — the manual side has `manual_agent.py` doing
   focused work for the eval suite; the OBD side has nothing analogous, so the
   main agent must do all OBD investigation in its own context.

The toolset must be **agent-native**: tools provide *data*, not pre-digested
conclusions. The V1 pipeline's `generate_clues` /`detect_anomalies` /
`statistics_extractor` chain runs before the LLM in V1; for V2 the agent
drives the investigation itself.

## Requirements

1. Six OBD investigation primitives mapped to distinct cognitive steps
   (discover, read, aggregate, find-events, list-DTCs, lookup-DTC).
2. A new OBD sub-agent (`run_obd_agent`) mirroring the established
   `manual_agent.py` template — restricted toolset, structured-output
   contract, no SSE / event-log persistence.
3. Two delegation wrappers (`delegate_to_obd_agent`,
   `delegate_to_manual_agent`) so the main agent can route compound
   inquiries to specialists (hybrid Pattern 2, see §Approach).
4. Tools must expose `A_YAM_*` proprietary signals under their original
   column names.
5. DTC tools must handle Yamaha-proprietary hex codes honestly: no
   fabricated decodings; manual-search pivot for unknown formats.
6. All tools return text (no multimodal — OBD data is tabular).
7. Hermetic unit tests against the real Yamaha fixture; no network or LLM
   calls. Smoke-tested sub-agent loop with mocked `LLMClient`.

## Out of scope

The following are deliberately deferred to follow-up tickets:

- **Diagnostic accuracy benchmarks** — no labelled fault data exists yet.
- **OBD golden set + eval harness** (parallel to #73 for manuals) — needs a
  separate HARNESS-XX ticket once goldens can be generated.
- **Cross-signal correlation tool** (`correlate_signals`) — agent can
  approximate by reading two windows and reasoning; add only if evals show
  it is needed.
- **Anomaly-detection-as-a-tool** — would re-introduce the "pre-digested
  analysis" pattern that #69 walked away from. May revisit later as a
  derived-feature primitive (changepoint timestamps as *data*, not
  conclusions).
- **Annotation scratchpad** — useful for long sessions but introduces state
  (where it lives, when it clears).
- **Freeze-frame snapshot tool** — current fixture has no freeze-frame data.
  Defer until a fixture exists.
- **Real-time / streaming OBD ingestion**.
- **Multi-vehicle generalisation** — Yamaha-first; Honda / others later.
- **Yamaha-hex DTC decoder** — no public spec; reverse-engineering is its
  own workstream.

## Approach

### Architectural pattern: hybrid (Pattern 2)

Three patterns were considered:

| Pattern | Main agent toolbox | Sub-agents in production? |
|---|---|---|
| 1 — Flat | All primitives directly | No (eval-only) |
| **2 — Hybrid (chosen)** | **Primitives + delegation wrappers** | **Yes, via `delegate_to_*`** |
| 3 — Pure delegation | Only delegation wrappers | Yes (primitives hidden from main) |

Pattern 2 matches Claude Code's own design (it has `Read` / `Grep` / `Glob`
*and* the `Task` sub-agent tool). The main agent does small things directly
("what's the RPM range?") and delegates compound investigations
("investigate the stored DTCs end-to-end").

Pattern 3 is cleaner architecturally but rewrites the main agent's prompt in
the same PR — too much risk in one change. Pattern 1 leaves sub-agents
aspirational. Pattern 2 lands meaningful delegation without forcing a
whole-architecture rewrite.

### Tool decomposition philosophy

Per Anthropic's *Writing tools for agents* guide and the manual-toolset
precedent: each tool maps to one cognitive step. We split `read_obd_data`'s
current two-mode shape (overview vs. signal-query) into separate tools
(`list_signals` + `read_window`) so the agent never juggles modes within a
single call.

Tool-name analogs from Claude Code's own primitives:

| Claude Code | OBD analog | Why |
|---|---|---|
| `Glob` | `list_signals(pattern, subsystem)` | discovery — what exists |
| `Read` | `read_window(signals, t1, t2)` | targeted sample read |
| `Grep` | `find_events(signal, predicate)` | where does condition hold |
| (aggregate) | `get_signal_stats(signals, range)` | summary without raw rows |
| (lookup) | `list_dtcs(status, ecu)` + `lookup_dtc(code)` | DTC drilldown |

### Yamaha-specific decisions

| Quirk | Decision | Rationale |
|---|---|---|
| `A_YAM_*` proprietary fields stripped by normalizer | Expose under **original names** (no friendly aliases yet) | Zero new mapping work; agent calls `search_manual` to learn what each means; revisit if evals show stumbling |
| Yamaha-hex DTCs (`87F11043…`) | **Honest no-decoder + manual pivot** in `lookup_dtc` | No public Yamaha DTC spec; building a decoder is out of scope; manual chart is the realistic recovery path |
| Channel B (ABS ECU) not materialized in fixture | List as `not present` in `list_signals`; `list_dtcs(ecu="abs")` returns empty cleanly | Future fixtures may include Channel B; interface stable |
| Freeze-frame data absent | Skip `get_freeze_frame` tool entirely | Defer interface until data exists; lying about an unavailable tool is worse than omitting it |

## Design

### 1. Architecture

```
                   ┌───────────────────────────────┐
                   │   Main diagnosis agent        │
                   │   (12-tool registry)          │
                   │   primitives + delegation     │
                   └──────────────┬────────────────┘
                                  │ delegate_to_obd_agent(inquiry)
                                  ▼
┌───────────────────────────────────────────────────────────────┐
│  OBD sub-agent  (new — app/harness_agents/obd_agent.py)       │
│  • run_obd_agent(inquiry, session_id, deps) -> OBDAgentResult │
│  • Restricted ReAct loop, max_iter=8, timeout=120s            │
│  • Reuses LLMClient + ToolRegistry from app.harness           │
└──────────────┬────────────────────────────────────────────────┘
               │  tool calls (only the 6 OBD primitives)
               ▼
┌───────────────────────────────────────────────────────────────┐
│  OBD investigation toolset  (new — app/harness_tools/obd_*.py)│
│  ├─ list_signals       (Glob analog)                          │
│  ├─ read_window        (Read analog)                          │
│  ├─ get_signal_stats   (aggregate primitive)                  │
│  ├─ find_events        (Grep analog)                          │
│  ├─ list_dtcs          (DTC enumeration)                      │
│  └─ lookup_dtc         (DTC decode + manual pivot)            │
└──────────────┬────────────────────────────────────────────────┘
               │  reads raw CSV via _resolve_log_path() pattern
               ▼
       OBDAnalysisSession.raw_input_file_path on disk
```

#### Registries (three independent factories)

```python
create_default_registry()    # Main agent: 12 tools
                              #   6 OBD primitives + 4 manual primitives
                              #   + delegate_to_obd_agent
                              #   + delegate_to_manual_agent

create_obd_agent_registry()  # OBD sub-agent: 6 primitives only
                              #   (no delegation — prevents recursion)

create_manual_agent_registry() # Manual sub-agent: 3 primitives only
                                #   (already exists in manual_agent.py)
```

#### `OBDAgentResult` contract

Mirrors `ManualAgentResult` (`harness_agents/types.py`) where structurally
identical, diverges only where OBD data differs from manual content.

```python
class SignalCitation(BaseModel):
    signal: str
    time_range: Optional[Tuple[str, str]] = None  # ISO start / end
    value: Optional[float] = None
    stat: Optional[str] = None                    # "p95"|"mean"|"max"|...
    units: Optional[str] = None

class DTCCitation(BaseModel):
    code: str
    status: Literal["stored", "pending"]
    ecu: Optional[str] = None

class DataExcerpt(BaseModel):
    kind: Literal["stats", "events", "window", "dtcs"]
    payload: Dict[str, Any]                        # tool-output shape

class OBDAgentResult(BaseModel):
    summary: str
    signal_citations: List[SignalCitation]
    dtc_citations: List[DTCCitation]
    raw_data: List[DataExcerpt]
    limitations: List[str]
    tool_trace: List[ToolCallTrace]                # reused from types.py
    iterations: int
    total_tokens: int
    stopped_reason: StoppedReason                  # reused from types.py
```

### 2. Tool catalog

All 8 tools return plain text. Inputs are Pydantic-validated; `_session_id` is
auto-injected by the loop and not declared in the input model.

#### 2.1 `list_signals` (Glob analog, cheap)

**Question:** *"What signals does this OBD log contain?"*

```python
class ListSignalsInput(BaseModel):
    pattern: Optional[str] = None
    subsystem: Optional[Literal["engine", "abs", "all"]] = "all"
```

Returns ~150-token text inventory: time range, sampling rate, channel
presence, signal list with units + density (dense / sparse / missing).
Filters by glob-ish pattern and subsystem.

#### 2.2 `read_window` (Read analog, medium)

**Question:** *"Give me raw samples for these signals in this time range."*

```python
class ReadWindowInput(BaseModel):
    signals: List[str] = Field(..., min_length=1, max_length=8)
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    max_rows: int = Field(default=50, ge=1, le=500)
```

Tab-separated table with timestamp + requested signals. Auto-downsamples
when window samples exceed `max_rows`. Always shows units + missing-data
notes.

#### 2.3 `get_signal_stats` (aggregate, cheap)

**Question:** *"Summarize these signals — give me mean/std/percentiles, no
raw rows."*

```python
class GetSignalStatsInput(BaseModel):
    signals: List[str] = Field(..., min_length=1, max_length=10)
    time_range: Optional[Tuple[str, str]] = None
    include: Optional[List[Literal[
        "basic", "percentiles", "trend", "extrema",
    ]]] = None  # default ["basic", "percentiles"]
```

Internal implementation **reuses `obd_agent/statistics_extractor.py`** — the
kind of "primitive helper" #69 wanted preserved (data-producing, not
conclusion-emitting). With `include=["extrema"]` also returns timestamps of
min/max — useful for the agent to drill in with `read_window`.

#### 2.4 `find_events` (Grep analog, cheap)

**Question:** *"When does this signal meet this condition?"*

```python
class FindEventsInput(BaseModel):
    signal: str
    predicate: Literal[
        "above_threshold", "below_threshold",
        "rising_above", "falling_below",
        "rate_of_change_above", "rate_of_change_below",
        "missing",
    ]
    threshold: Optional[float] = None
    min_duration_seconds: float = 1.0
    merge_gap_seconds: float = 2.0
    time_range: Optional[Tuple[str, str]] = None
    max_events: int = Field(default=20, ge=1, le=100)
```

Returns list of `(start, end, duration, peak, sample_count)` per event.
Predicates are a fixed enum (safer, more predictable, easier to test) — no
free-form expressions.

#### 2.5 `list_dtcs` (DTC enumeration, cheap)

**Question:** *"What fault codes are in this session?"*

```python
class ListDTCsInput(BaseModel):
    status: Optional[Literal["stored", "pending", "all"]] = "all"
    ecu: Optional[Literal["engine", "abs", "all"]] = "all"
```

Extracts standard P-codes from `GET_DTC` / `GET_CURRENT_DTC` columns (existing
`_parse_dtc_list` path) **and** Yamaha-hex DTCs from the leading metadata
comment block (new helper). Groups by classification, separates stored vs.
pending.

#### 2.6 `lookup_dtc` (DTC decode, cheap)

**Question:** *"What does this fault code mean, what subsystem is it about,
what should I look at next?"*

```python
class LookupDTCInput(BaseModel):
    code: str
```

Standard P/C/B/U codes → python-OBD lookup (existing
`log_parser.py:159` path). Yamaha hex → honest "no decoder" output with a
`search_manual(query=<code>, vehicle_model=…)` pivot. Unknown formats → clear
error string suggesting verification.

#### 2.7 `delegate_to_obd_agent` (delegation wrapper)

**Question (from main agent):** *"Investigate this OBD-related question
end-to-end; come back with a structured finding."*

```python
class DelegateToOBDAgentInput(BaseModel):
    inquiry: str = Field(..., min_length=10, max_length=500)
```

Handler:
1. Build `OBDAgentDeps(llm_client, create_obd_agent_registry(), config)`.
2. Await `run_obd_agent(inquiry, _session_id, deps)` → `OBDAgentResult`.
3. Serialize via `format_obd_agent_result(result)` → structured markdown
   (summary + signal_citations + dtc_citations + raw_data + limitations).

`max_result_chars` raised to ~80 KB to accommodate sub-agent outputs.

#### 2.8 `delegate_to_manual_agent` (delegation wrapper)

```python
class DelegateToManualAgentInput(BaseModel):
    inquiry: str = Field(..., min_length=10, max_length=500)
    obd_context: Optional[str] = Field(default=None, max_length=2000)
```

Pure wrapper over the existing `run_manual_agent` infrastructure. Same
serialization pattern with `format_manual_agent_result`.

### 3. Data flow + infrastructure

#### 3.1 Raw-data access (no new infrastructure)

Reuse `_resolve_log_path(session_id, db)` + `parse_log_file(path)` already in
the codebase. Each tool re-parses the CSV on call. Justified for v1 because
the Yamaha fixture is 257 rows / ~50 KB and parses in < 50 ms. If profiling
shows it matters, add a per-sub-agent-run `lru_cache` later — single PR,
no architectural debt.

#### 3.2 `A_YAM_*` field exposure

The new tools **bypass `format_normalizer.py`** (which strips these). They
read column names directly from `parse_log_file()` output. A new helper
`obd_signal_inventory.py` produces `SignalDescriptor(name, units, subsystem,
density)` records. Units for known `A_YAM_*` fields come from a small
hand-curated dict (`BATT_V` → V, `INJ_MS` → ms, etc.); unknown fields just
say "raw".

#### 3.3 DTC parsing

```
list_dtcs / lookup_dtc
  └─> _extract_dtcs_from_metadata(file_path)  [new helper]
        ├─ Regex over leading comment block for "Stored DTC:" / "Pending DTC:"
        │  (Yamaha hex lives there in the fixture)
        └─ Existing _parse_dtc_list() for GET_DTC / GET_CURRENT_DTC columns
           (standard P-code path)
  └─> _classify_dtc(code) → "standard" | "yamaha_hex" | "unknown"
        ├─ standard → python-OBD table
        ├─ yamaha_hex → honest no-decoder output
        └─ unknown → actionable error string
```

#### 3.4 Sub-agent execution context

Sub-agent **shares the LLMClient** with the main agent (same OpenRouter /
Ollama backend) but uses an **independent message history** and **restricted
tool registry**. Failure isolation: a sub-agent timeout returns a graceful
`OBDAgentResult(stopped_reason="timeout", …)` rather than bubbling up.

#### 3.5 Result formatters

New file `app/harness_agents/result_formatters.py`:
- `format_obd_agent_result(result) -> str` — markdown serialization for
  delegation tool output.
- `format_manual_agent_result(result) -> str` — same for manual.

Lives next to the agent files because it is semantically owned by the
sub-agent contract, not the tool.

#### 3.6 What does NOT change

- `app/harness/loop.py` — main loop logic unchanged; just sees more tools.
- `app/harness/context.py` — truncation / compaction logic unchanged.
- `app/harness/autonomy.py` — tier routing unchanged.
- `app/harness_agents/manual_agent.py` — manual sub-agent unchanged.
- DB schema — unchanged.
- API endpoints — unchanged. (No new standalone OBD sub-agent endpoint in
  this PR; can be added later when an OBD eval suite exists.)

### 4. Error handling

Anthropic's tool-writing guide is emphatic that errors must be **actionable**.
Three tiers:

| Tier | Cause | Handler | Example output |
|---|---|---|---|
| **T1 — Input validation** | Pydantic rejects the call | Registry catches `ValidationError`, formats into actionable string | "predicate 'above_threshold' requires `threshold` parameter. Example: …" |
| **T2 — Domain mismatch** | Valid input, signal/code doesn't exist | Tool handler returns descriptive text (NOT raises); `is_error=False` | "Signal 'EGT' not in this session. Did you mean: COOLANT_TEMP, IAT? Use `list_signals` to see all 15 available signals." |
| **T3 — System / IO failure** | DB unreachable, file missing, sub-agent timeout | Tool handler raises → registry catches → `ToolResult(is_error=True, …)` | "Error: could not read OBD log file for session 7f3b… (file not found). This may be a stale session ID; verify with the user." |

#### Per-tool edge cases (summary)

- **`list_signals`**: corrupt file → informational message, not error.
- **`read_window`**: unknown signal → fuzzy-match suggestion (Levenshtein over
  inventory); inverted window → clear message; window outside session
  bounds → 0-sample notice with session range.
- **`get_signal_stats`**: <3 valid samples → omit `autocorr` / `linreg` with
  notes; all N/A in range → `count=0` row (not dropped).
- **`find_events`**: zero events → include max value in range to help agent
  adjust threshold; missing threshold for value predicate → T1.
- **`list_dtcs`**: no DTCs → informational, not error.
- **`lookup_dtc`**: Yamaha hex → no-decoder output with manual pivot;
  unrecognized format → "Code 'X1234' is not a recognized OBD-II standard
  code and does not match Yamaha hex format. Verify the code…"
- **`delegate_to_*`**: timeout / max_iterations → structured `*AgentResult`
  with `stopped_reason` set; LLM error → `stopped_reason="error"`.

#### Sub-agent loop error handling

Mirror `manual_agent.py`'s pattern verbatim:

| Failure | Handling |
|---|---|
| `asyncio.timeout` | Catch → `stopped_reason="timeout"`, preserve partial state |
| Max iterations | While-else → `stopped_reason="max_iterations"` |
| LLM JSON parse failure | Fallback: `_extract_last_assistant_content`, empty citations |
| Tool exec error | Surface error string via registry; sub-agent keeps iterating |
| Tool argument parse error | Append clean error as tool result so LLM self-corrects |

#### Privacy boundary

Per CLAUDE.md "Privacy & Data Boundaries (Non-Negotiable)":

- `read_window` enforces `max_rows ≤ 500` via Pydantic — prevents log dump.
- `get_signal_stats` only returns aggregates.
- `find_events` returns event metadata (start / end / peak), never the
  underlying sample sequences.
- `OBDAgentResult.raw_data` excerpts are bounded by the same caps because
  they are built from these tool outputs.

VINs: existing `pseudonymise_vin()` policy still applies but is dormant on
this hot path per APP-54. No new handling required.

### 5. Testing strategy

Per issue #85: "Smoke-test all tools against the real road-test fixture.
Tool registration in the harness, basic unit tests." Diagnostic-accuracy
evals are out of scope.

#### 5.1 Three test layers

| Layer | What it tests | Where | Network / LLM? |
|---|---|---|---|
| Unit | Each tool against the Yamaha fixture | `tests/harness_tools/test_obd_*.py` | No |
| Sub-agent smoke | `run_obd_agent` end-to-end with mocked LLM | `tests/harness_agents/test_obd_agent.py` | No |
| Delegation smoke | `delegate_to_*` handlers wired into the registry | `tests/harness_tools/test_delegation_tools.py` | No |

#### 5.2 Coverage targets (~35-40 new tests)

- `test_obd_signals.py` — 5 classes (List/Read/Stats/Events/Inventory)
- `test_obd_dtcs.py` — 2 classes (List / Lookup)
- `test_obd_agent.py` — sub-agent ReAct flow, timeout, max_iter, JSON
  failure, tool-error recovery
- `test_delegation_tools.py` — registry dispatch, session_id injection,
  formatted output, recursion guard (sub-agent registry MUST NOT contain
  delegation tools)

Specific must-have unit tests:

- `test_list_signals_returns_a_yam_proprietary_signals_under_original_names`
  — validates locked decision.
- `test_list_dtcs_extracts_two_yamaha_hex_dtcs_from_fixture_metadata` —
  uses real fixture data.
- `test_lookup_dtc_yamaha_hex_returns_no_decoder_with_manual_pivot` —
  validates locked decision.
- `test_read_window_unknown_signal_returns_fuzzy_match_suggestion` —
  T2 validation.
- `test_get_signal_stats_basic_stats_match_statistics_extractor_output` —
  reuse verification.
- `test_no_recursion_obd_agent_cannot_call_delegate_tool` — registry
  isolation.

#### 5.3 What is NOT tested in this PR

- **Diagnostic accuracy / correctness of agent conclusions** — out of scope.
- **Production main-agent integration regression** — covered by existing
  `test_integration.py` and `test_e2e_agent.py`. Run as-is; fix any fallout
  from the main registry growing from 5 to 12 tools.
- **Performance** — Yamaha fixture is small; defer caching decisions.
- **Multi-vehicle generalization** — Yamaha-only.

## Files to create / modify

| File | Action | LOC est. |
|---|---|---|
| `app/harness_agents/obd_agent.py` | Create — sub-agent ReAct loop | ~600 |
| `app/harness_agents/obd_agent_prompts.py` | Create — system prompt + user message | ~120 |
| `app/harness_agents/types.py` | Modify — add `SignalCitation`, `DTCCitation`, `DataExcerpt`, `OBDAgentResult` | +60 |
| `app/harness_agents/result_formatters.py` | Create — `format_obd_agent_result`, `format_manual_agent_result` | ~150 |
| `app/harness_tools/obd_signals.py` | Create — 4 signal tools | ~500 |
| `app/harness_tools/obd_dtcs.py` | Create — 2 DTC tools | ~250 |
| `app/harness_tools/obd_signal_inventory.py` | Create — `SignalDescriptor` + classifier helpers | ~120 |
| `app/harness_tools/delegation_tools.py` | Create — `delegate_to_obd_agent` + `delegate_to_manual_agent` | ~180 |
| `app/harness_tools/input_models.py` | Modify — 6 OBD input models + 2 delegation input models | +120 |
| `app/harness/tool_registry.py` | Modify — `create_default_registry` swap, new `create_obd_agent_registry` | +50 |
| `app/harness/harness_prompts.py` | Modify — tool descriptions + delegation guidance | +60 |
| `app/harness_tools/obd_data_tools.py` | Modify — unregister `read_obd_data` from default registry; keep file for one cycle | -10 |
| `tests/harness_tools/test_obd_signals.py` | Create | ~400 |
| `tests/harness_tools/test_obd_dtcs.py` | Create | ~200 |
| `tests/harness_tools/test_delegation_tools.py` | Create | ~200 |
| `tests/harness_agents/test_obd_agent.py` | Create | ~350 |

Net addition: ~3,200 LOC including tests.

## Open questions / future work

These are deliberately deferred to follow-up tickets but worth recording:

1. **`delegate_to_obd_agent` vs. direct OBD tools — when does main agent
   pick which?** Initial system-prompt guidance: "Use primitive tools for
   focused questions; delegate for compound investigations." If
   in-production traces show the main agent over- or under-delegating, tune
   the prompt or surface confidence-based routing.

2. **Per-run DataFrame cache.** Single PR refactor when profiling shows
   per-call CSV parsing is the bottleneck.

3. **Yamaha-hex DTC decoder.** Needs Yamaha spec or careful reverse-engineering.
   Until then, `lookup_dtc` honestly says "no decoder; check the manual."

4. **OBD eval suite.** Parallel to #73 for manuals. Blocked on having
   labelled fault data — pending real-world cases from the PolyU pilot.

5. **A_YAM_\* friendly aliases.** If evals show the agent stumbles on
   proprietary field names (e.g. confusing `A_YAM_VVA` with valve angle vs.
   variable valve actuator), build a friendly-name map. Otherwise the
   agent calls `search_manual` to learn what each means.

6. **Main agent → pure-orchestrator rewrite (Pattern 3).** Long-term
   direction. Triggered when delegation traces show the main agent rarely
   uses primitives directly. Separate ticket.

7. **Freeze-frame tool.** Once a fixture with freeze-frame data exists,
   add `get_freeze_frame(dtc_code?)`.

8. **Channel B (ABS ECU) data.** Currently `list_signals` reports "not
   present" for the Yamaha fixture. When CAN-side data appears in future
   uploads, the existing tools should handle it transparently.

## References

- Issue [#85 — HARNESS-19 (this ticket)](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/85)
- Issue [#69 — V2 toolset redesign (`read_obd_data` lineage)](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/69)
- Issue [#71 — Manual toolset (reference pattern for 3-tool decomposition)](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/71)
- Issue [#73 — Manual sub-agent eval suite (`ManualAgentResult` contract)](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/73)
- Issue [#80 — Yamaha real-road-test regression fixture](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/80)
- Issue [#26 — Harness engineering architecture](https://github.com/qmohsu/stf_ai_diagnosis_platform_v1/issues/26)
- [Anthropic — Writing tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
- [Anthropic — Develop your tests](https://platform.claude.com/docs/en/test-and-evaluate/develop-tests)
- `app/harness_agents/manual_agent.py` — template for the OBD sub-agent
- `docs/v2_design_doc.md` — V2 architecture (will be updated when this PR lands)
- `docs/v2_dev_plan.md` — V2 dev plan (HARNESS-19 ticket entry will be added)
