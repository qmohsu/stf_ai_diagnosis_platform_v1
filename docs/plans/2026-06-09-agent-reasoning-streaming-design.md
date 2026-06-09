# Agent Reasoning Streaming (HARNESS-22)

**Date:** 2026-06-09
**Ticket:** HARNESS-22
**Origin:** Follow-up to GitHub Issue #119 — UX problem observed while
validating the first real Yamaha agent diagnosis.

## Problem

The Agent AI diagnosis showed a multi-minute frozen spinner with no
output, then everything appeared at once. Root cause: although the
endpoint (`harness/router.py::generate_agent_diagnosis`) already returns
a `StreamingResponse` and the frontend consumes SSE incrementally, the
agent loop (`run_diagnosis_loop`) makes a **blocking, non-streaming**
`llm_client.chat()` call per ReAct iteration and emits **nothing**
between the start of an iteration and the moment that call returns. With
`qwen3.5:27b` in thinking mode (thousands of hidden reasoning tokens per
turn) each iteration is tens of seconds; the first is the worst (largest
context). A long single turn also risks the Cloudflare-tunnel idle
timeout — the APP-41 keep-alive was only wired into the one-shot path,
never the agent loop.

## Decisions (from brainstorming)

1. **Reasoning display:** live, collapsible "Thinking…" panel that
   streams the raw thinking tokens and auto-collapses at the tool-call /
   answer boundary.
2. **Persistence:** **live-only (ephemeral)** — reasoning is streamed to
   the browser but not stored. History replay is unchanged (tool calls +
   final answer). No DB schema change.
3. **Final answer:** also streams token-by-token (types out live), then
   the `done` event finalizes + persists it as today.

## Approach (chosen: A — streaming method on the LLM client)

Add `chat_stream()` to the `LLMClient` protocol + `OpenAILLMClient`. It
calls `chat.completions.create(stream=True)` and yields normalized
`LLMStreamChunk` deltas (`reasoning` | `content`) as they arrive, while
accumulating OpenAI tool-call deltas by `.index`. It terminates by
yielding the **same `LLMResponse`** the loop already consumes, so tool
dispatch / done-detection downstream are unchanged. All vendor streaming
quirks (qwen3's `delta.reasoning` / `model_extra["reasoning"]` channel,
tool-call fragment reassembly) stay in the adapter; the mock client in
tests just yields a scripted chunk sequence.

Rejected: (B) an `on_token` callback on `chat()` — bridging a callback
into the loop's async generator needs a queue; awkward, harder to test.
(C) inline `stream=True` in the loop — makes the loop vendor-aware and
bypasses the `LLMClient` protocol the tests depend on.

### Robustness folded in

- **Graceful fallback:** `loop._stream_llm_turn` wraps the streaming
  consumption; if `chat_stream()` raises (e.g. an Ollama build that
  won't stream with tools), it falls back to a single blocking `chat()`
  so a streaming quirk degrades to prior behaviour instead of failing
  the diagnosis.
- **Keep-alive:** a `_with_keepalive` wrapper around the SSE generator
  injects a `: ping` comment during any >15s silent gap (covers the
  pre-first-token prompt-processing window), fully closing the tunnel
  timeout risk without adding events to the `HarnessEvent` stream.

## Data flow

```
chat_stream (stream=True)
  → LLMStreamChunk("reasoning"|"content") ...        # live deltas
  → LLMResponse(content, tool_calls, finish_reason)  # terminal
loop._stream_llm_turn
  → HarnessEvent("reasoning"|"token", {text, iteration})   # yielded live
  → returns the terminal LLMResponse to the loop (unchanged dispatch)
router._stream
  → reasoning → SSE "reasoning" {text, iteration}
  → token     → SSE "token" "<text>"
  (wrapped by _with_keepalive → ": ping" during gaps)
frontend
  → onReasoning → ReasoningPanel (per-iteration, cleared at tool_call)
  → onToken     → diagnosis text types out (buffer reset at tool_call)
  → onDone      → final text + persisted history id
```

## Components touched

- **Backend:** `harness/deps.py` (`LLMStreamChunk`, `chat_stream`,
  `_extract_reasoning`, `EventType += reasoning|token`),
  `harness/loop.py` (`_stream_llm_turn` + streaming consumption with
  fallback), `harness/router.py` (`reasoning`/`token` SSE mapping +
  `_with_keepalive`).
- **Frontend:** `lib/types.ts` (`AgentReasoningEvent`), `lib/api.ts`
  (`onReasoning` + dispatch case), `components/ReasoningPanel.tsx`
  (new), `components/AgentDiagnosisView.tsx` (state + handlers +
  iteration-boundary reset), `components/ToolCallCard.tsx`
  (`&check;`→`✓`), `locales/{en,zh-CN,zh-TW}.json` (`agent.thinking`).

## Error handling

- Streaming error → log `harness_stream_fallback`, fall back to blocking
  `chat()`; if that also raises, the loop's existing handler emits
  `error` + a partial-diagnosis `done`.
- Frontend resets reasoning + answer buffers at each `tool_call` so
  intermediate narration never pollutes the final answer area.

## Testing

`tests/harness/test_loop_streaming.py`: `chat_stream` reasoning/content
split, tool-call delta accumulation (single + multi-index),
reasoning-via-`model_extra`, loop emits `reasoning`/`token` with no
fallback, fallback-to-`chat()` on stream error. (`_with_keepalive` and
`chat_stream` reassembly were additionally validated standalone offline,
since the full harness suite needs tiktoken; the suite runs in-container
on deploy.)

## Non-goals

Persisting reasoning to the event log; summarizing reasoning into a
status line; reasoning in History replay; a per-iteration progress event
in the `HarnessEvent` stream (the keep-alive wrapper covers liveness
without it).
