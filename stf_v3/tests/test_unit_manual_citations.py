"""PROD-10 (D4 runtime fix): the manual sub-agent keeps its citations.

The first golden calibration run found 20 of 30 manual answers with no
citation although the right sections were read: Qwen3.6 wrote citations
as strings (``"<slug>: <quote>"``) that the V2-copied parser dropped, and
the force-final turn often came back as prose or as a tool call written
out as text, with no nudge.  Driven by a scripted FunctionModel.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import json

from pydantic_ai.usage import RunUsage

from stf_v3.diagnosis.agent.events import EventSink
from stf_v3.diagnosis.agent.manual_agent import citation_from_item, parse_final_json, run_manual_agent
from stf_v3.diagnosis.agent.types import SectionRef
from tests.agent_helpers import TEXT, Script, make_deps, response, small_manual, tool_call

JAZZ = small_manual("m2", manufacturer="Honda", vehicle_model="Jazz", factory_code="GK5")
READ = [SectionRef(manual_id="m2", slug="2-1-fuel-pump-troubleshooting", text="x"),
        SectionRef(manual_id="m2", slug="3-1-battery", text="y")]


def test_string_citations_become_citations() -> None:
    """``"<slug>: <quote>"`` and a bare slug are kept; the manual id comes
    from the sections read; the slug is canonicalised."""
    payload = json.dumps({"summary": "320 kPa.", "citations": [
        "2-1-fuel-pump-troubleshooting: 燃油壓力 320 kPa",
        "3-1-battery"]})
    summary, cits = parse_final_json(payload, READ)
    assert summary == "320 kPa."
    assert [(c.manual_id, c.slug, c.quote) for c in cits] == [
        ("m2", "2-1-fuel-pump-troubleshooting", "燃油壓力 320 kPa"), ("m2", "3-1-battery", "")]


def test_object_citations_with_other_keys_are_kept() -> None:
    """``section`` / ``excerpt`` instead of ``slug`` / ``quote``; a missing
    manual id is filled in; empty or non-citation items are skipped."""
    items = [{"section": "3-1-battery", "excerpt": "12 V"}, {"manual_id": "m2", "slug": ""}, 42, "  "]
    cits = [citation_from_item(i, READ) for i in items]
    assert cits[0] is not None and (cits[0].manual_id, cits[0].slug, cits[0].quote) == ("m2", "3-1-battery", "12 V")
    assert cits[1:] == [None, None, None]


def test_the_requested_object_format_is_unchanged() -> None:
    """The V2 format still parses exactly as before."""
    payload = json.dumps({"summary": "s", "citations": [
        {"manual_id": "m2", "slug": "Fuel Pump Troubleshooting", "quote": "320 kPa"}]})
    _, cits = parse_final_json(payload, READ)
    assert [(c.manual_id, c.slug, c.quote) for c in cits] == [("m2", "2-1-fuel-pump-troubleshooting", "320 kPa")]


async def test_a_forced_prose_answer_is_nudged_once_with_tools_still_withheld() -> None:
    """Four reads trip the backstop; the forced turn answers in prose → one
    nudge (still no tools) → the JSON answer with its citation is kept."""
    reads = [response(tool_call("read_manual_section", manual_id="m2", section=s))
             for s in ("1-1-specifications", "1-2-maintenance-schedule", "2-1-fuel-pump-troubleshooting", "3-1-battery")]
    final = json.dumps({"summary": "Fuel pressure is 320 kPa.",
                        "citations": ["2-1-fuel-pump-troubleshooting: 320 kPa"]})
    script = Script(main=[], manual=[response(tool_call("list_manuals"))] + reads + [
        response(TEXT(content="The fuel pressure is 320 kPa according to the section I read.")),
        response(TEXT(content=final))])
    deps = make_deps(manufacturer="Honda", model="Jazz", manuals=[JAZZ])
    deps.events = EventSink()
    result = await run_manual_agent(deps, script.model(), "What is the fuel pressure spec?", None, "d-1", RunUsage())
    assert result.nudged is True
    assert script.seen_tools["manual"][-1] == [] and script.seen_tools["manual"][-2] == []
    assert [c.slug for c in result.citations] == ["2-1-fuel-pump-troubleshooting"]
    assert result.summary == "Fuel pressure is 320 kPa."
