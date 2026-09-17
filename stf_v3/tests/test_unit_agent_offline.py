"""PROD-08 T-8 / T-9 / T-10 / T-11 / T-13: the whole diagnosis loop offline.

TestModel drives the full tool set; a scripted FunctionModel checks
delegation, event nesting, shared usage, text-plus-tool-call semantics,
citation extraction and thinking isolation; the four budget gates, a
model failure, a cancel and empty responses all end in a partial report.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import structlog
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from stf_v3.diagnosis.agent import events as ev
from stf_v3.diagnosis.agent.deps import Budgets
from stf_v3.diagnosis.agent.main_agent import MAIN_TOOL_NAMES, run_diagnosis, stream_diagnosis
from stf_v3.diagnosis.agent.report import extract_citations
from stf_v3.diagnosis.agent.runner import redact_error
from stf_v3.diagnosis.tools._common import REPEAT_PREFIX
from tests.agent_helpers import FAKE_VIN, TEXT, THINK, Script, make_deps, n_responses, response, small_manual, tool_call

FINAL_MD = (
    "**Fault identification** — P00AF stored (turbo boost control).\n"
    "**Root cause analysis** — see m1#2-1-fuel-pump-troubleshooting and m1#never-read.\n"
    "**Supporting evidence** — P0117 is not present; P00AF is.\n"
    "**Recommended actions** — inspect the actuator.\n**Limitations** — none.\n"
)
MANUAL_FINAL = json.dumps({"summary": "Fuel pressure 320 kPa.", "citations": [
    {"manual_id": "m1", "slug": "2-1-fuel-pump-troubleshooting", "quote": "320 kPa"}]})
OBD_FINAL = json.dumps({"summary": "P00AF stored; RPM steady at idle.", "signal_citations": [
    {"signal": "RPM", "value": 850.0, "stat": "mean", "units": "rpm"}],
    "dtc_citations": [{"code": "P00AF", "status": "stored", "ecu": "engine"}], "raw_data": [], "limitations": []})


# ── T-8: TestModel drives everything ─────────────────────────────


@pytest.mark.parametrize("log", ["tsv", "yamaha", "maxlog"])
async def test_testmodel_full_round_calls_every_tool(log: str) -> None:
    """All 12 tools are called and every result is text; the run opens with
    session_start, ends with done, and produces a report."""
    deps = make_deps(log)
    out = await run_diagnosis(deps, TestModel(custom_output_text="**Fault identification** — nothing found."))
    assert out.stopped_reason == "complete"
    called = {e.payload["tool"] for e in out.events if e.event_type == ev.TOOL_CALL}
    assert called == set(MAIN_TOOL_NAMES)
    results = [e for e in out.events if e.event_type == ev.TOOL_RESULT]
    assert len(results) == 12 and all(isinstance(e.payload["chars"], int) for e in results)
    assert out.events[0].event_type == ev.SESSION_START and out.events[-1].event_type == ev.DONE
    assert ev.DIAGNOSIS_DONE in [e.event_type for e in out.events]
    assert out.report.content_md.startswith("**Fault identification**") and not out.report.partial
    assert [e.seq for e in out.events] == list(range(1, len(out.events) + 1))


async def test_stream_is_single_use_and_matches_run() -> None:
    """FM-10: the stream yields the same sequence a run records and cannot be
    iterated twice; a used deps object is refused."""
    deps = make_deps("tsv")
    stream = stream_diagnosis(deps, TestModel(custom_output_text="done"))
    seen = [e.event_type async for e in stream]
    assert seen[0] == ev.SESSION_START and seen[-1] == ev.DONE and stream.outcome is not None
    assert seen == [e.event_type for e in stream.outcome.events]
    with pytest.raises(RuntimeError):
        async for _ in stream:
            pass
    with pytest.raises(ValueError):
        stream_diagnosis(deps, TestModel())


# ── T-9: scripted delegation round ───────────────────────────────


def _script() -> Script:
    return Script(
        main=[
            response(tool_call("list_signals")),
            response(tool_call("delegate_to_manual_agent", inquiry="What is the fuel pressure specification?")),
            response(tool_call("delegate_to_obd_agent", inquiry="Investigate the stored DTCs and idle RPM.")),
            response(TEXT(content="Let me also decode the code."), tool_call("lookup_dtc", code="P00AF")),
            response(THINK(content="secret reasoning about P0300"), TEXT(content=FINAL_MD)),
        ],
        manual=[
            response(tool_call("list_manuals")),
            response(tool_call("read_manual_section", manual_id="m1", section="2-1-fuel-pump-troubleshooting")),
            response(TEXT(content=MANUAL_FINAL)),
        ],
        obd=[
            response(tool_call("list_dtcs")),
            response(TEXT(content=OBD_FINAL)),
        ],
    )


async def test_scripted_round_events_nesting_usage_and_citations() -> None:
    """Event order follows the script; sub-agent events carry the delegation
    call id; usage sums main + both sub-agents; a text-plus-tool-call turn
    continues; citations: read section = trace, unread section = NO_SOURCE,
    P0117 = NO_SOURCE, P00AF = trace; thinking never becomes a token."""
    script = _script()
    deps = make_deps("maxlog", manuals=[small_manual("m1", manufacturer="Toyota", vehicle_model="Hiace", factory_code=None)])
    out = await run_diagnosis(deps, script.model())
    assert out.stopped_reason == "complete", out.error
    types = [e.event_type for e in out.events]
    assert types[0] == ev.SESSION_START and types[-1] == ev.DONE and ev.DIAGNOSIS_DONE in types
    top = [e for e in out.events if e.parent_tool_call_id is None and e.event_type == ev.TOOL_CALL]
    assert [e.payload["tool"] for e in top] == ["list_signals", "delegate_to_manual_agent",
                                                 "delegate_to_obd_agent", "lookup_dtc"]
    manual_call = next(e for e in top if e.payload["tool"] == "delegate_to_manual_agent")
    nested = [e for e in out.events if e.parent_tool_call_id == manual_call.payload["tool_call_id"]]
    assert [e.payload["tool"] for e in nested if e.event_type == ev.TOOL_CALL] == ["list_manuals", "read_manual_section"]
    obd_call = next(e for e in top if e.payload["tool"] == "delegate_to_obd_agent")
    nested_obd = [e for e in out.events if e.parent_tool_call_id == obd_call.payload["tool_call_id"]]
    assert [e.payload["tool"] for e in nested_obd if e.event_type == ev.TOOL_CALL] == ["list_dtcs"]
    # the delegation results reached the main agent, formatted
    lookup_idx = types.index(ev.TOOL_RESULT, types.index(ev.TOOL_CALL, out.events.index(top[-1])))
    assert out.events[lookup_idx].payload["tool"] == "lookup_dtc"
    # usage: 5 main requests + 3 manual + 2 obd
    assert out.usage.requests == 10 and script.calls == {"main": 5, "manual": 3, "obd": 2}
    assert n_responses(out.messages) == 5
    # text + tool call continued (a token event precedes the lookup_dtc call, then the run went on)
    assert any(e.event_type == ev.TOKEN and e.payload["text"].startswith("Let me also") for e in out.events)
    # thinking is a reasoning event, never a token
    assert any(e.event_type == ev.REASONING and "secret reasoning" in e.payload["text"] for e in out.events)
    assert not any(e.event_type == ev.TOKEN and "secret reasoning" in e.payload["text"] for e in out.events)
    cits = {(c.kind, c.ref): c.source for c in out.report.citations}
    assert cits[("manual", "m1#2-1-fuel-pump-troubleshooting")] == "trace"
    assert cits[("manual", "m1#never-read")] == "NO_SOURCE"
    assert cits[("dtc", "P00AF")] == "trace" and cits[("dtc", "P0117")] == "NO_SOURCE"
    assert "P0300" not in {c.ref for c in out.report.citations}   # thinking text is not report text
    assert out.report.content_md == FINAL_MD and out.report.tool_calls == 7


def test_extract_citations_with_empty_trace() -> None:
    """FM-34: a zero-trace run yields an empty list (or only NO_SOURCE marks)."""
    assert extract_citations("nothing", [], ["m1"]) == []
    only_text = extract_citations("see P0117", [], ["m1"])
    assert [(c.ref, c.source) for c in only_text] == [("P0117", "NO_SOURCE")]


# ── T-10: the four gates + failures ──────────────────────────────


def _looper(tokens: int = 0) -> Script:
    return Script(main=[response(tool_call("list_signals", pattern="*"), tokens=tokens)])


async def _partial(deps, model):  # type: ignore[no-untyped-def]
    out = await run_diagnosis(deps, model)
    types = [e.event_type for e in out.events]
    assert types[-1] == ev.DONE and ev.ERROR in types
    assert out.report.partial and out.report.content_md.startswith("> **Partial report**")
    return out


async def test_request_limit_ends_in_partial_report() -> None:
    """A model that never stops calling tools hits the request gate."""
    deps = make_deps(budgets=Budgets(request_limit=3, wall_clock_s=30))
    out = await _partial(deps, _looper().model())
    assert out.stopped_reason == "budget" and out.usage.requests <= 3


async def test_tool_calls_limit_ends_in_partial_report() -> None:
    deps = make_deps(budgets=Budgets(request_limit=50, tool_calls_limit=2, wall_clock_s=30))
    out = await _partial(deps, _looper().model())
    assert out.stopped_reason == "budget"


async def test_total_tokens_limit_ends_in_partial_report() -> None:
    deps = make_deps(budgets=Budgets(request_limit=50, total_tokens_limit=500, wall_clock_s=30))
    out = await _partial(deps, _looper(tokens=300).model())
    assert out.stopped_reason == "budget"


async def test_wall_clock_ends_in_partial_report() -> None:
    """A slow model plus a 1-second wall clock → timeout, still a clean end."""

    async def slow(messages, info):  # type: ignore[no-untyped-def]
        await asyncio.sleep(3)
        return response(TEXT(content="too late"))

    deps = make_deps(budgets=Budgets(wall_clock_s=1.0))
    out = await _partial(deps, FunctionModel(slow, model_name="slow"))
    assert out.stopped_reason == "timeout" and out.elapsed_s < 3


async def test_model_connection_error_ends_in_error_event() -> None:
    """FM-14: a transport failure produces error + done and a partial report."""

    def boom(messages, info):  # type: ignore[no-untyped-def]
        raise httpx.ConnectError("Bearer sk-secret connection refused")

    out = await _partial(make_deps(), FunctionModel(boom, model_name="boom"))
    assert out.stopped_reason == "error" and "sk-secret" not in (out.error or "")
    err = next(e for e in out.events if e.event_type == ev.ERROR)
    assert "sk-secret" not in json.dumps(err.payload)


async def test_subagent_over_budget_returns_prefixed_partial_finding() -> None:
    """FM-8 / FM-46: the delegation tool returns a prefixed partial finding
    and the main run completes normally."""
    script = Script(
        main=[response(tool_call("delegate_to_manual_agent", inquiry="What is the fuel pressure spec?")),
              response(TEXT(content="done"))],
        manual=[response(tool_call("search_manual_text", manual_id="m1", query="pressure"))],
    )
    deps = make_deps(budgets=Budgets(wall_clock_s=30, subagent_request_limit=1, subagent_wall_clock_s=30))
    out = await run_diagnosis(deps, script.model())
    assert out.stopped_reason == "complete"
    result = next(m for m in out.messages if any(getattr(p, "tool_name", "") == "delegate_to_manual_agent"
                                                 and hasattr(p, "content") for p in m.parts))
    part = next(p for p in result.parts if getattr(p, "tool_name", "") == "delegate_to_manual_agent")
    assert str(part.content).startswith("[delegation exceeded its budget — partial finding]")


def test_budgets_come_from_settings() -> None:
    """FM-28: every gate is a setting."""
    from stf_v3.settings import Settings

    b = Budgets.from_settings(Settings(agent_wall_clock_s=5, agent_request_limit=7, subagent_request_limit=2,
                                       tool_result_max_tokens=9, compact_threshold_tokens=11))
    assert (b.wall_clock_s, b.request_limit, b.subagent_request_limit, b.tool_result_max_tokens,
            b.compact_threshold_tokens) == (5, 7, 2, 9, 11)


# ── T-11: vehicle identity from the record ───────────────────────


def _first_user_prompt(messages) -> str:  # type: ignore[no-untyped-def]
    first = next(m for m in messages if isinstance(m, ModelRequest))
    return "\n".join(p.content for p in first.parts if isinstance(p, UserPromptPart))


async def test_vehicle_identity_comes_from_the_record_not_the_log() -> None:
    """A log with no VIN and a different '# Vehicle:' line still yields a
    prompt naming the record's make/model/VIN; a non-local model gets the
    pseudonym instead of the VIN (FM-51)."""
    deps = make_deps("maxlog_no_vin", manufacturer="Toyota", model="Hiace", vin=FAKE_VIN)
    out = await run_diagnosis(deps, TestModel(custom_output_text="ok"))
    prompt = _first_user_prompt(out.messages)
    assert "Toyota Hiace" in prompt and FAKE_VIN in prompt and "Corolla" not in prompt
    cloud = make_deps("maxlog_no_vin", manufacturer="Toyota", model="Hiace", vin=FAKE_VIN, model_is_local=False)
    out2 = await run_diagnosis(cloud, TestModel(custom_output_text="ok"))
    prompt2 = _first_user_prompt(out2.messages)
    assert FAKE_VIN not in prompt2 and "VIN V-" in prompt2 and "Toyota Hiace" in prompt2
    assert FAKE_VIN not in json.dumps([e.payload for e in out2.events], default=str)


async def test_locale_directive_in_prompt() -> None:
    """D2: the default locale (zh-TW) adds a Traditional Chinese directive."""
    deps = make_deps()
    assert deps.locale == "zh-TW"
    out = await run_diagnosis(deps, TestModel(custom_output_text="ok"))
    assert "Chinese (Traditional)" in _first_user_prompt(out.messages)


# ── T-13: observability, memo, cancel, empty replies ─────────────


async def test_logs_carry_no_content_and_no_vin() -> None:
    """FM-21 / FM-52: one structured line per event and per tool, with sizes
    only — no tool output text, no VIN."""
    deps = make_deps("maxlog")
    with structlog.testing.capture_logs() as logs:
        out = await run_diagnosis(deps, TestModel(custom_output_text="ok"))
    events = [l for l in logs if l.get("event") == "agent.event"]
    tools = [l for l in logs if l.get("event") == "agent.tool"]
    assert len(events) == len(out.events) and len(tools) == 12
    blob = json.dumps(logs, default=str)
    assert FAKE_VIN not in blob and "Signals (" not in blob and "text" not in {k for l in logs for k in l}


def test_redact_error_hides_secrets() -> None:
    assert "abc123" not in redact_error(Exception("Authorization: Bearer abc123 failed"))
    assert redact_error(ValueError("plain")) == "ValueError: plain"


async def test_repeated_tool_call_is_served_from_memo() -> None:
    """FM-12: an identical second call returns the same result with the
    repeat prefix and is flagged in the event."""
    script = Script(main=[response(tool_call("list_signals"), tool_call("list_signals")),
                          response(TEXT(content="done"))])
    deps = make_deps()
    out = await run_diagnosis(deps, script.model())
    results = [e for e in out.events if e.event_type == ev.TOOL_RESULT]
    assert [e.payload["repeated"] for e in results] == [False, True]
    returns = [p for m in out.messages for p in m.parts if getattr(p, "tool_name", "") == "list_signals" and hasattr(p, "content")]
    assert returns[1].content == REPEAT_PREFIX + returns[0].content


async def test_cancel_check_stops_the_run() -> None:
    """FM-55: a cancel callback that turns true ends the run as cancelled."""
    flags = {"n": 0}

    def cancel() -> bool:
        flags["n"] += 1
        return flags["n"] >= 2

    deps = make_deps(cancel_check=cancel)
    out = await run_diagnosis(deps, _looper().model())
    assert out.stopped_reason == "cancelled"
    assert [e.event_type for e in out.events][-1] == ev.DONE and ev.ERROR in [e.event_type for e in out.events]


async def test_empty_replies_are_retried_then_partial() -> None:
    """FM-43: a model that answers with nothing is retried a bounded number
    of times and the run ends with a partial report, never a hang."""
    calls = {"n": 0}

    def empty(messages, info):  # type: ignore[no-untyped-def]
        calls["n"] += 1
        return response()

    deps = make_deps(budgets=Budgets(wall_clock_s=30, request_limit=20))
    out = await run_diagnosis(deps, FunctionModel(empty, model_name="empty"))
    assert out.stopped_reason in ("error", "budget") and out.report.partial
    assert 2 <= calls["n"] <= 6
