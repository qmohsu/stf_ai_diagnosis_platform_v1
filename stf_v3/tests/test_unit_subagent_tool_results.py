"""PROD-10 (D4 runtime fix): sub-agents see tool output whole.

V3 applied the main agent's 2000-token cap to every tool result, also
inside the sub-agents.  The MWS-150-A TOC (~4200 CJK chars) lost its
middle, so the manual sub-agent could not see sections such as
"火星塞的檢查" and guessed; long sections lost their middle too.  V2's
sub-agents never truncated.  Now the cap applies to main-agent calls only;
sub-agents get a runaway guard (16 000 tokens).

Author: Xiangzhu Yan
"""

from __future__ import annotations

from stf_v3.diagnosis.agent.deps import Budgets, SubAgentDeps
from stf_v3.diagnosis.tools._common import execute
from tests.agent_helpers import make_deps, stub_ctx

LONG_CJK = "章節目錄火星塞的檢查" * 500          # 5000 CJK chars ≈ 5000 estimated tokens


async def _call(deps) -> str:  # type: ignore[no-untyped-def]
    async def impl() -> str:
        return LONG_CJK

    return await execute(stub_ctx(deps), "get_manual_toc", {"manual_id": "m1"}, impl)


async def test_a_sub_agent_sees_the_whole_result() -> None:
    """Inside a sub-agent a 5000-token result comes back unchanged."""
    core = make_deps(budgets=Budgets(tool_result_max_tokens=2000, subagent_tool_result_max_tokens=16_000))
    out = await _call(SubAgentDeps(parent=core, parent_tool_call_id="d-1"))
    assert out == LONG_CJK


async def test_the_main_agent_keeps_its_2000_token_cap() -> None:
    """The same call from the main agent is cut to head + marker + tail."""
    core = make_deps(budgets=Budgets(tool_result_max_tokens=2000, subagent_tool_result_max_tokens=16_000))
    out = await _call(core)
    assert "[…truncated" in out and len(out) < len(LONG_CJK)


async def test_the_sub_agent_runaway_guard_still_applies() -> None:
    """A result beyond the sub-agent guard is still truncated."""
    core = make_deps(budgets=Budgets(tool_result_max_tokens=2000, subagent_tool_result_max_tokens=1000))
    out = await _call(SubAgentDeps(parent=core, parent_tool_call_id="d-1"))
    assert "[…truncated" in out


def test_settings_carry_both_caps() -> None:
    """The budgets built from settings carry both caps (explicit env wins)."""
    from stf_v3.settings import Settings

    b = Budgets.from_settings(Settings(subagent_tool_result_max_tokens=9000))
    assert b.tool_result_max_tokens == 2000 and b.subagent_tool_result_max_tokens == 9000
