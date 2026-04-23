"""Manual-agent runner — Phase 1 stub.

This module exposes the stable entry point used by the eval
harness::

    result = await run_manual_agent(question, obd_context)

In Phase 1 (plumbing), the implementation returns a fixed dummy
``ManualAgentResult`` without calling any LLM.  In Phase 2 the
real restricted ReAct loop (4 manual-navigation tools, no
``read_obd_data``) will replace the stub body; the signature and
return type are frozen.

Author: Li-Ta Hsu
"""

from __future__ import annotations

from typing import Optional

import structlog

from tests.harness.evals.schemas import (
    Citation,
    ManualAgentResult,
    SectionRef,
    ToolCallTrace,
)

logger = structlog.get_logger(__name__)


# Stub constants used until the real agent lands in Phase 2.
_STUB_MANUAL_ID = "MWS150A_Service_Manual"
_STUB_SLUG = "3-2-fuel-system-troubleshooting"
_STUB_TEXT = (
    "Stubbed section text — Phase 1 plumbing only.  "
    "The real manual agent will return actual content "
    "pulled from read_manual_section."
)


async def run_manual_agent(
    question: str,
    obd_context: Optional[str] = None,
) -> ManualAgentResult:
    """Run the manual sub-agent against a diagnostic inquiry.

    Phase 1 stub: returns a deterministic dummy result so the
    eval harness (judge + parametrized pytest) can be verified
    end-to-end without LLM cost.  The real implementation will
    drive a restricted ReAct loop over ``list_manuals``,
    ``get_manual_toc``, ``read_manual_section``, and
    ``search_manual`` — no ``read_obd_data``.

    Args:
        question: The diagnostic inquiry to investigate.  In
            production this is typically derived from
            ``read_obd_data`` output.
        obd_context: Optional OBD context snippet that primes
            the agent (observed DTCs, symptom summary).

    Returns:
        A ``ManualAgentResult`` with a summary, citations, raw
        section refs, and a tool-call trace.
    """
    logger.info(
        "manual_agent_stub_invoked",
        question_preview=question[:80],
        has_obd_context=obd_context is not None,
    )
    return ManualAgentResult(
        summary=(
            "Stub summary — Phase 1 plumbing verification.  "
            "Replace this with the real agent output in "
            "Phase 2."
        ),
        citations=[
            Citation(
                manual_id=_STUB_MANUAL_ID,
                slug=_STUB_SLUG,
                quote="stub quote",
            ),
        ],
        raw_sections=[
            SectionRef(
                manual_id=_STUB_MANUAL_ID,
                slug=_STUB_SLUG,
                text=_STUB_TEXT,
                had_images=False,
            ),
        ],
        tool_trace=[
            ToolCallTrace(
                name="list_manuals",
                input={},
                latency_ms=0.0,
            ),
            ToolCallTrace(
                name="get_manual_toc",
                input={"manual_id": _STUB_MANUAL_ID},
                latency_ms=0.0,
            ),
            ToolCallTrace(
                name="read_manual_section",
                input={
                    "manual_id": _STUB_MANUAL_ID,
                    "section": _STUB_SLUG,
                },
                latency_ms=0.0,
            ),
        ],
        iterations=3,
        total_tokens=0,
        stopped_reason="complete",
    )
