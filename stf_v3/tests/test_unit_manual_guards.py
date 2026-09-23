"""PROD-08 T-12: the manual sub-agent guardrails (FM-40) and JSON contract (FM-41).

Driven by a scripted FunctionModel through ``run_manual_agent``.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import json

from pydantic_ai.usage import RunUsage

from stf_v3.diagnosis.agent.events import EventSink
from stf_v3.diagnosis.agent.guards import FORCE_FINAL_INSTRUCTION, ManualGuardState, pin_manual_for_inquiry
from stf_v3.diagnosis.agent.manual_agent import FINAL_ANSWER_TOOL, parse_final_json, run_manual_agent
from stf_v3.diagnosis.agent.types import SectionRef
from tests.agent_helpers import TEXT, Script, make_deps, response, small_manual, tool_call

COROLLA = small_manual("m1", manufacturer="Toyota", vehicle_model="Corolla E11", factory_code=None)
JAZZ = small_manual("m2", manufacturer="Honda", vehicle_model="Jazz", factory_code="GK5")
FINAL = json.dumps({"summary": "Fuel pressure is 320 kPa.", "citations": [
    {"manual_id": "m2", "slug": "Fuel Pump Troubleshooting", "quote": "320 kPa"}]})


async def _run(script: Script, **deps_kw):  # type: ignore[no-untyped-def]
    deps = make_deps(manufacturer="Honda", model="Jazz", manuals=[COROLLA, JAZZ], **deps_kw)
    deps.events = EventSink()
    result = await run_manual_agent(deps, script.model(), "What is the fuel pressure spec for the Jazz GK5?",
                                    None, "delegate-1", RunUsage())
    return deps, result


async def test_pinned_manual_blocks_foreign_reads_then_forces_final() -> None:
    """The vehicle record pins the Jazz manual; reads of the Corolla manual
    return BLOCKED text (not executed); after two blocks the tools are
    withheld and the model must answer."""
    script = Script(main=[], manual=[
        response(tool_call("list_manuals")),
        response(tool_call("read_manual_section", manual_id="m1", section="1-1-specifications")),
        response(tool_call("get_manual_toc", manual_id="m1")),
        response(TEXT(content=FINAL)),
    ])
    deps, result = await _run(script)
    blocked = [t for t in deps.trace if t.is_error]
    assert len(blocked) == 2 and all(t.input["manual_id"] == "m1" for t in blocked)
    assert deps.events.count("tool_result") >= 3
    # the forced turn offered only final_answer (PROD-10) and carried the force-final instruction
    assert script.seen_tools["manual"][-1] == [FINAL_ANSWER_TOOL]
    assert result.stopped_reason == "complete" and result.summary == "Fuel pressure is 320 kPa."
    assert result.citations and result.citations[0].manual_id == "m2"


async def test_four_reads_trip_the_force_final_backstop() -> None:
    """After four section reads the next model request has no tools; the
    final JSON is parsed and cited slugs are canonicalised to what was read."""
    reads = [response(tool_call("read_manual_section", manual_id="m2", section=s))
             for s in ("1-1-specifications", "1-2-maintenance-schedule", "2-1-fuel-pump-troubleshooting", "3-1-battery")]
    script = Script(main=[], manual=[response(tool_call("list_manuals"))] + reads + [response(TEXT(content=FINAL))])
    deps, result = await _run(script)
    assert script.seen_tools["manual"][-1] == [FINAL_ANSWER_TOOL] and len(script.seen_tools["manual"][-2]) == 4
    assert len(result.raw_sections) == 4
    assert result.citations[0].slug == "2-1-fuel-pump-troubleshooting"


async def test_repeated_identical_call_trips_force_final() -> None:
    """A byte-identical repeated call is a loop: the repeat is served from
    the memo and the tools are withheld on the next request."""
    same = response(tool_call("read_manual_section", manual_id="m2", section="3-1-battery"))
    script = Script(main=[], manual=[same, same, response(TEXT(content=FINAL))])
    deps, result = await _run(script)
    assert script.seen_tools["manual"][-1] == [FINAL_ANSWER_TOOL]
    repeated = [e for e in deps.events.events if e.event_type == "tool_result" and e.payload.get("repeated")]
    assert repeated and result.stopped_reason == "complete"


async def test_non_json_final_falls_back_to_text_and_budget_gives_partial() -> None:
    """FM-41: prose instead of JSON becomes the summary; a sub-agent that
    never finishes within its request budget yields a partial with the
    last assistant text and stopped_reason=budget (FM-8)."""
    script = Script(main=[], manual=[response(TEXT(content="The spec is 320 kPa (no JSON here)."))])
    _, result = await _run(script)
    assert result.summary.startswith("The spec is 320 kPa") and result.citations == []
    from stf_v3.diagnosis.agent.deps import Budgets

    looping = Script(main=[], manual=[
        response(TEXT(content="still looking"), tool_call("search_manual_text", manual_id="m2", query="pressure")),
        response(TEXT(content="still looking"), tool_call("search_manual_text", manual_id="m2", query="pressure2")),
    ])
    _, partial = await _run(looping, budgets=Budgets(wall_clock_s=30, subagent_wall_clock_s=30, subagent_request_limit=1))
    assert partial.stopped_reason == "budget"
    assert partial.summary  # never empty


def test_parse_final_json_tolerates_fences_and_prose() -> None:
    """Fenced JSON, JSON inside prose, and garbage all parse to a usable pair."""
    fenced = "```json\n" + FINAL + "\n```"
    summary, cits = parse_final_json(fenced, [SectionRef(manual_id="m2", slug="2-1-fuel-pump-troubleshooting", text="x")])
    assert summary == "Fuel pressure is 320 kPa." and cits[0].slug == "2-1-fuel-pump-troubleshooting"
    summary, cits = parse_final_json("Here you go: " + FINAL + " done.")
    assert cits and cits[0].manual_id == "m2"
    summary, cits = parse_final_json("nothing structured")
    assert summary == "nothing structured" and cits == []
    assert parse_final_json(None)[1] == []


def test_pin_helpers() -> None:
    """Exactly one textual match pins; zero or two do not; state exposes
    the force-final trigger via after_call."""
    assert pin_manual_for_inquiry("procedure on the Jazz", [COROLLA, JAZZ]) == ("m2", "vehicle")
    tricity = small_manual("m3", manufacturer="Yamaha", vehicle_model="TRICITY155", factory_code="MWS150-A")
    assert pin_manual_for_inquiry("P0117 on the MWS-150-A", [COROLLA, tricity]) == ("m3", "factory_code")
    assert pin_manual_for_inquiry("procedure for corolla and jazz", [COROLLA, JAZZ]) is None
    assert pin_manual_for_inquiry("something else", [COROLLA, JAZZ]) is None
    state = ManualGuardState(inquiry_text="x", manuals=[COROLLA, JAZZ])
    state.pin_from_vehicle("Honda", "Jazz")
    assert state.pinned is JAZZ and state.check_access("m2") is None
    assert "BLOCKED" in (state.check_access("m1") or "")
    for _ in range(4):
        state.after_call("read_manual_section", {"manual_id": "m2", "section": str(_)})
    assert state.force_final and FORCE_FINAL_INSTRUCTION.startswith("You have now read")
