"""PROD-10 (D4 runtime fix): the manual sub-agent keeps its citations.

The golden calibration run found 20 of 30 manual answers with no citation
although the right sections were read.  Two causes, both on the V3 side:

* on the force-final turn (search tools withheld) Qwen3.6 kept "calling
  tools"; with none offered the call came back as text and the answer was
  lost.  Now that turn offers exactly one tool, ``final_answer``, whose
  arguments carry the schema (summary + citation objects).  It is not
  offered on normal turns (when it was, the model answered after one or
  two reads);
* in text answers the model wrote citations as strings, which the
  V2-copied parser dropped.  Strings are accepted now, but only when they
  resolve to a section that was actually read (free text such as
  ``"Section '<title>': …"`` would otherwise count as a wrong claim).

Driven by scripted FunctionModels.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RunUsage

from stf_v3.diagnosis.agent.events import EventSink
from stf_v3.diagnosis.agent.manual_agent import (
    FINAL_ANSWER_TOOL,
    ManualFinalAnswer,
    citation_from_item,
    final_from_output,
    parse_final_json,
    run_manual_agent,
)
from stf_v3.diagnosis.agent.types import SectionRef
from tests.agent_helpers import TEXT, Script, make_deps, response, small_manual, tool_call

JAZZ = small_manual("m2", manufacturer="Honda", vehicle_model="Jazz", factory_code="GK5")
READ = [SectionRef(manual_id="m2", slug="2-1-fuel-pump-troubleshooting", text="x"),
        SectionRef(manual_id="m2", slug="3-1-battery", text="y")]
FOUR_READS = [response(tool_call("read_manual_section", manual_id="m2", section=s))
              for s in ("1-1-specifications", "1-2-maintenance-schedule", "2-1-fuel-pump-troubleshooting", "3-1-battery")]


async def _run(model: Any) -> Any:
    deps = make_deps(manufacturer="Honda", model="Jazz", manuals=[JAZZ])
    deps.events = EventSink()
    return await run_manual_agent(deps, model, "What is the fuel pressure spec?", None, "d-1", RunUsage())


def test_string_citations_are_kept_only_when_they_name_a_read_section() -> None:
    """``"<slug>: <quote>"`` / bare slug of a read section → kept with the
    manual id of the read sections; ``"Section '<title>': …"`` that resolves
    to nothing read → dropped (no wrong claim)."""
    payload = json.dumps({"summary": "320 kPa.", "citations": [
        "2-1-fuel-pump-troubleshooting: 燃油壓力 320 kPa", "3-1-battery",
        "Section '故障代碼編號 P0117': something", "Engine specs: 0.90 L"]})
    summary, cits = parse_final_json(payload, READ)
    assert summary == "320 kPa."
    assert [(c.manual_id, c.slug, c.quote) for c in cits] == [
        ("m2", "2-1-fuel-pump-troubleshooting", "燃油壓力 320 kPa"), ("m2", "3-1-battery", "")]


def test_object_citations_with_other_keys_and_the_v2_format() -> None:
    """``section`` / ``excerpt`` keys and a missing manual id are accepted;
    the requested V2 object format parses exactly as before (an unread slug
    in an OBJECT is still kept, as V2 did, so the judge sees the claim)."""
    items = [{"section": "3-1-battery", "excerpt": "12 V"}, {"manual_id": "m2", "slug": ""}, 42, "  "]
    cits = [citation_from_item(i, READ) for i in items]
    assert cits[0] is not None and (cits[0].manual_id, cits[0].slug, cits[0].quote) == ("m2", "3-1-battery", "12 V")
    assert cits[1:] == [None, None, None]
    payload = json.dumps({"summary": "s", "citations": [
        {"manual_id": "m2", "slug": "Fuel Pump Troubleshooting", "quote": "320 kPa"},
        {"manual_id": "m2", "slug": "9-9-not-read", "quote": ""}]})
    _, cits = parse_final_json(payload, READ)
    assert [c.slug for c in cits] == ["2-1-fuel-pump-troubleshooting", "9-9-not-read"]


def test_the_final_answer_tool_output_is_the_answer() -> None:
    """The output-tool model maps to (summary, citations) with canonical slugs."""
    out = ManualFinalAnswer(summary="320 kPa.", citations=[
        {"slug": "Fuel Pump Troubleshooting", "quote": "320 kPa"}])  # type: ignore[list-item]
    summary, cits = final_from_output(out, READ)
    assert summary == "320 kPa." and [(c.manual_id, c.slug) for c in cits] == [("m2", "2-1-fuel-pump-troubleshooting")]


async def test_final_answer_is_offered_only_on_the_forced_turn() -> None:
    """Normal turns: the four search tools, no ``final_answer`` (offered
    always, it made the model answer after one or two reads).  After four
    reads: exactly ``final_answer``.  After it is used: no tool; the closing
    text is ignored and the submitted answer is the result (no nudge)."""
    seen: List[List[str]] = []
    script = FOUR_READS + [
        response(tool_call(FINAL_ANSWER_TOOL, summary="Fuel pressure is 320 kPa.", citations=[
            {"manual_id": "m2", "slug": "Fuel Pump Troubleshooting", "quote": "320 kPa"}])),
        response(TEXT(content="DONE")),
    ]
    calls = {"n": 0}

    def fn(messages: List[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(sorted(t.name for t in info.function_tools))
        i = calls["n"]
        calls["n"] += 1
        return script[min(i, len(script) - 1)]

    result = await _run(FunctionModel(fn, model_name="final-answer-script"))
    assert all(FINAL_ANSWER_TOOL not in s and len(s) == 4 for s in seen[:4])
    assert seen[4] == [FINAL_ANSWER_TOOL] and seen[5] == []
    assert result.nudged is False and result.summary == "Fuel pressure is 320 kPa."
    assert [(c.manual_id, c.slug) for c in result.citations] == [("m2", "2-1-fuel-pump-troubleshooting")]
    assert FINAL_ANSWER_TOOL not in [t.name for t in result.tool_trace]


async def test_a_forced_prose_answer_is_nudged_once() -> None:
    """If the forced turn still answers in prose, one nudge (search tools
    still withheld, ``final_answer`` offered); the JSON reply is then
    parsed.  Never loops."""
    final = json.dumps({"summary": "Fuel pressure is 320 kPa.",
                        "citations": ["2-1-fuel-pump-troubleshooting: 320 kPa"]})
    script = Script(main=[], manual=[response(tool_call("list_manuals"))] + FOUR_READS + [
        response(TEXT(content="The fuel pressure is 320 kPa according to the section I read.")),
        response(TEXT(content=final))])
    result = await _run(script.model())
    assert result.nudged is True
    assert script.seen_tools["manual"][-1] == [FINAL_ANSWER_TOOL] == script.seen_tools["manual"][-2]
    assert [c.slug for c in result.citations] == ["2-1-fuel-pump-troubleshooting"]
