"""Manual-agent runner for the evaluation suite.

Thin wrapper that the parametrized eval test uses to execute the
manual sub-agent once per golden entry.  Responsibilities:

- Build default ``ManualAgentDeps`` pointing at the local Ollama
  endpoint (``settings.llm_endpoint``) so the agent runs on the
  same model that ships (``qwen3.5:27b-q8_0``).
- Allow tests / plumbing runs to inject alternate deps via the
  ``deps`` kwarg — this is how the ``--mock-agent`` flag works.
- Return a fully-populated ``ManualAgentResult`` ready for the
  judge.

The actual ReAct loop lives in ``app.harness_agents.manual_agent``;
this module only handles the "how do I configure a production-ish
agent for one eval run" concern.

Author: Li-Ta Hsu
"""

from __future__ import annotations

from typing import Optional

import structlog
from openai import AsyncOpenAI

from app.config import settings
from app.harness.deps import OpenAILLMClient
from app.harness_agents.manual_agent import (
    ManualAgentConfig,
    ManualAgentDeps,
    create_manual_agent_registry,
    run_manual_agent as _run_agent_loop,
)
from app.harness_agents.types import ManualAgentResult

logger = structlog.get_logger(__name__)


# Module-level cache for the real deps so we don't rebuild the
# OpenAI client + tool registry once per golden entry.  Tests
# that need isolation pass their own ``deps`` kwarg.
_cached_deps: Optional[ManualAgentDeps] = None


def _build_default_deps() -> ManualAgentDeps:
    """Construct deps pointing at local Ollama.

    The eval suite's primary target is the model that ships —
    ``qwen3.5:27b-q8_0`` served by Ollama on the PolyU box.  For
    ceiling comparison (phase 5), callers can override
    ``ManualAgentConfig.model`` to an OpenRouter identifier and
    pass their own deps.

    Returns:
        ``ManualAgentDeps`` ready to pass into
        ``run_manual_agent``.
    """
    base_url = f"{settings.llm_endpoint}/v1"
    client = AsyncOpenAI(
        api_key="ollama",
        base_url=base_url,
        timeout=300.0,
    )
    return ManualAgentDeps(
        llm_client=OpenAILLMClient(client),
        tool_registry=create_manual_agent_registry(),
        config=ManualAgentConfig(),
    )


def _get_default_deps() -> ManualAgentDeps:
    """Return a process-cached default deps instance."""
    global _cached_deps
    if _cached_deps is None:
        _cached_deps = _build_default_deps()
    return _cached_deps


async def run_manual_agent(
    question: str,
    obd_context: Optional[str] = None,
    deps: Optional[ManualAgentDeps] = None,
) -> ManualAgentResult:
    """Run the manual sub-agent for one golden entry.

    Args:
        question: The inquiry to answer.
        obd_context: Optional OBD context snippet.
        deps: Pre-built dependency container.  Tests use this to
            inject a fake ``LLMClient``.  When ``None``, a
            process-cached default pointing at local Ollama is
            lazily constructed from ``settings``.

    Returns:
        A ``ManualAgentResult`` with summary, citations,
        raw_sections, tool_trace, and diagnostics metadata.
    """
    effective_deps = deps or _get_default_deps()

    logger.info(
        "manual_agent_runner_invoked",
        question_preview=question[:80],
        has_obd_context=obd_context is not None,
        model=effective_deps.config.model,
    )

    return await _run_agent_loop(
        question, obd_context, effective_deps,
    )


def _reset_cache_for_testing() -> None:
    """Test-only helper: drop the cached deps.

    Tests that swap environment variables between cases must
    call this so a previously-built cached deps doesn't leak.
    """
    global _cached_deps
    _cached_deps = None
