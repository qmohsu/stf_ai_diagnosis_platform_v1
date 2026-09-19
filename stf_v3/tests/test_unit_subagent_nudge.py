"""PROD-09: the one-shot "finish your answer" nudge for both sub-agents.

Seen on the server with Qwen3.6 on vLLM (thinking off): the manual
sub-agent ended a turn with planning prose ("I need to check …") and no
JSON.  The runtime now nudges exactly once with the same history and
budget; a second prose answer is accepted as-is (never loops).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import json

from pydantic_ai.usage import RunUsage

from stf_v3.diagnosis.agent.events import EventSink
from stf_v3.diagnosis.agent.guards import NUDGE_FINAL_INSTRUCTION
from stf_v3.diagnosis.agent.manual_agent import run_manual_agent
from stf_v3.diagnosis.agent.obd_agent import run_obd_agent
from tests.agent_helpers import TEXT, Script, make_deps, response, small_manual, tool_call

JAZZ = small_manual("m2", manufacturer="Honda", vehicle_model="Jazz", factory_code="GK5")
MANUAL_FINAL = json.dumps({"summary": "Fuel pressure is 320 kPa.", "citations": [
    {"manual_id": "m2", "slug": "2-1-fuel-pump-troubleshooting", "quote": "320 kPa"}]})
OBD_FINAL = json.dumps({"summary": "Rail pressure is normal.", "signal_citations": [], "dtc_citations": [],
                        "data_excerpts": [], "limitations": []})


async def test_manual_agent_nudges_once_after_planning_prose() -> None:
    """Prose turn → one nudge (same history, tools still offered) → JSON parsed."""
    script = Script(main=[], manual=[
        response(tool_call("read_manual_section", manual_id="m2", section="2-1-fuel-pump-troubleshooting")),
        response(TEXT(content="I have the spec but need to check the interval. Let me look further.")),
        response(TEXT(content=MANUAL_FINAL)),
    ])
    deps = make_deps(manufacturer="Honda", model="Jazz", manuals=[JAZZ])
    deps.events = EventSink()
    result = await run_manual_agent(deps, script.model(), "What is the fuel pressure spec?", None, "d-1", RunUsage())
    assert script.calls["manual"] == 3 and script.seen_tools["manual"][-1] != []   # tools were NOT withheld
    assert result.summary == "Fuel pressure is 320 kPa." and result.citations[0].manual_id == "m2"
    assert result.stopped_reason == "complete" and result.iterations == 3   # requests accumulate across the nudge (shared usage)


async def test_manual_agent_accepts_a_second_prose_answer_without_looping() -> None:
    """Two prose turns in a row → the second is the summary; exactly one nudge."""
    script = Script(main=[], manual=[
        response(TEXT(content="Still looking.")),
        response(TEXT(content="The spec is 320 kPa (prose, no JSON).")),
    ])
    deps = make_deps(manufacturer="Honda", model="Jazz", manuals=[JAZZ])
    deps.events = EventSink()
    result = await run_manual_agent(deps, script.model(), "Spec?", None, "d-2", RunUsage())
    assert script.calls["manual"] == 2
    assert result.summary.startswith("The spec is 320 kPa") and result.citations == []


async def test_obd_agent_nudges_once_and_a_json_answer_needs_no_nudge() -> None:
    """Same behaviour for the OBD sub-agent; a JSON first answer is never nudged."""
    nudged = Script(main=[], obd=[
        response(tool_call("list_dtcs")),
        response(TEXT(content="Let me also check the rail pressure trend.")),
        response(TEXT(content=OBD_FINAL)),
    ])
    deps = make_deps()
    deps.events = EventSink()
    result = await run_obd_agent(deps, nudged.model(), "Check rail pressure", "d-3", RunUsage())
    assert nudged.calls["obd"] == 3 and result.summary == "Rail pressure is normal."
    direct = Script(main=[], obd=[response(TEXT(content=OBD_FINAL))])
    deps2 = make_deps()
    deps2.events = EventSink()
    result2 = await run_obd_agent(deps2, direct.model(), "Check rail pressure", "d-4", RunUsage())
    assert direct.calls["obd"] == 1 and result2.summary == "Rail pressure is normal."
    assert "final JSON" in NUDGE_FINAL_INSTRUCTION
