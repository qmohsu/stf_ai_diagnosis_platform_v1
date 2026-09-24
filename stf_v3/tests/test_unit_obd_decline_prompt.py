"""PROD-10 D5 follow-up: the OBD sub-agent declines without citations.

The V3 golden baseline found Qwen3.6 declining correctly in words ("no
evidence of misfire") while still citing the RPM it had checked and the two
undecodable Yamaha DTCs, because V2's prompt carried two rules that
contradict each other in a decline: "every DTC in the summary must be
cited" and "a decline cites nothing".  qwen3.5 resolved the conflict the
other way.  The prompt now says which rule wins, shows a decline object,
stops a decline after discovery, and asks for the named DTC to be cited
when the question is about one.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import json
import re
from typing import List

from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RunUsage

from stf_v3.diagnosis.agent.events import EventSink
from stf_v3.diagnosis.agent.obd_agent import run_obd_agent
from stf_v3.diagnosis.agent.subagent_prompts import OBD_AGENT_SYSTEM_PROMPT
from tests.agent_helpers import make_deps

_P = OBD_AGENT_SYSTEM_PROMPT


def _rule(prefix: str) -> str:
    """The Rules bullet that starts with ``prefix`` (continuation lines joined)."""
    rules = _P[_P.index("## Rules"):]
    for bullet in rules.split("\n- ")[1:]:
        text = " ".join(line.strip() for line in bullet.splitlines())
        if text.startswith(prefix):
            return text
    raise AssertionError(f"no rule starting with {prefix!r}")


def test_the_decline_rule_wins_over_cite_every_dtc() -> None:
    """The two V2 rules no longer contradict: a decline is the stated exception."""
    rule = _rule("Every DTC mentioned in `summary`")
    assert "EXCEPT in a no-evidence decline" in rule
    assert "decline rule below wins" in rule


def test_checked_signals_and_undecodable_dtcs_are_not_decline_citations() -> None:
    """The decline rule names the two things Qwen3.6 cited in the baseline."""
    rule = _rule("**No-evidence declines.**")
    assert "`signal_citations` and `dtc_citations` must be EMPTY" in rule
    assert "checked only to rule something out" in rule
    assert "undecodable Yamaha hex DTCs" in rule
    assert "NOT citations in a decline" in rule


def test_the_decline_example_has_both_citation_lists_empty() -> None:
    """A concrete decline object follows the schema, with ``[]`` for both lists."""
    start = _P.index("A no-evidence decline uses the same object")
    block = _P[start:_P.index("## Rules")]
    assert '"summary": "There is no evidence in the OBD data of' in block
    assert '"signal_citations": [],' in block
    assert '"dtc_citations": [],' in block


def test_a_decline_stops_after_discovery() -> None:
    """The decide-first step sits between discovery and quantifying, and the
    process steps stay numbered 1..6 (the request-limit tail of adversarial-002)."""
    process = _P[_P.index("## Process"):_P.index("## Final output schema")]
    steps = [int(n) for n in re.findall(r"^(\d)\. ", process, flags=re.MULTILINE)]
    assert steps == [1, 2, 3, 4, 5, 6]
    decide = process.index("3. Decide whether the log can answer the inquiry at all")
    assert process.index("2. Discover") < decide < process.index("4. Quantify and locate")
    assert "at most one\n   `get_signal_stats`" in process
    assert "undecoded Yamaha-proprietary raw signal" in process


def test_a_question_about_a_named_dtc_cites_it() -> None:
    """The mirror rule: asked about a code, cite it even if undecodable."""
    rule = _rule("When the inquiry asks about a specific DTC")
    assert "cite that DTC in `dtc_citations` with its status" in rule
    assert "even when it cannot be decoded" in rule


async def test_the_obd_agent_runs_on_this_prompt_and_keeps_a_decline_empty() -> None:
    """End to end on a scripted model: the sub-agent is instructed with the
    new prompt, and a decline naming the Yamaha DTCs in its summary comes
    back with no citations and the ``no evidence`` wording intact."""
    seen: List[str] = []
    decline = json.dumps({
        "summary": ("There is no evidence in the OBD data of engine misfire. The log has no "
                    "misfire counter; two Yamaha hex DTCs (87F11043000000000000CB, "
                    "87F11047000000000000CF) cannot be decoded."),
        "signal_citations": [], "dtc_citations": [], "raw_data": [],
        "limitations": ["No misfire counter in this log"],
    })

    def model_fn(messages: List[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(info.instructions or "")
        return ModelResponse(parts=[TextPart(content=decline)])

    deps = make_deps()
    deps.events = EventSink()
    result = await run_obd_agent(deps, FunctionModel(model_fn, model_name="decline"),
                                 "Is the engine misfiring during this trip?", "d-1", RunUsage())
    assert seen and "EXCEPT in a no-evidence decline" in seen[0]
    assert result.stopped_reason == "complete" and not result.nudged
    assert result.summary.startswith("There is no evidence in the OBD data")
    assert result.signal_citations == [] and result.dtc_citations == []
