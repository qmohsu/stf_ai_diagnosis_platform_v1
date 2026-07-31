"""Lane run-callable builders for the pipelined eval orchestrator.

HARNESS-31 (#225): the per-lane "how do I execute the system under
test for one golden" logic used to live inline in the two
parametrised test bodies (``test_manual_agent_eval.py`` /
``test_rag_eval.py``).  The session orchestrator needs it BEFORE
any test body runs, so it moved here.  The test files keep only
their pass thresholds and assertions.

This module imports the runners (and transitively the app / agent
stack), so it must be imported LAZILY — inside fixture bodies, not
at conftest module level — to keep collection importable offline
(tiktoken download gotcha, see .claude/memory.md).

Author: Li-Ta Hsu
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from tests.harness.evals.rag_runner import run_rag
from tests.harness.evals.runner import run_manual_agent_unified
from tests.harness.evals.schemas import GoldenEntry, SystemRunResult

RunFn = Callable[[], Awaitable[SystemRunResult]]


# HARNESS-29 (#213): the harness-verified vehicle identity for
# this corpus, injected as the ``## VEHICLE`` block — mirroring
# what production ``delegate_to_manual_agent`` resolves from the
# upload session row (APP-60 make/model).  Entries may override
# via ``GoldenEntry.vehicle`` (empty string = deliberately no
# vehicle, exercising the legacy inference path).
CORPUS_VEHICLE = "Yamaha TRICITY155 (factory code MWS-150-A)"


# RAG-side knobs.  ``top_k=5`` matches the production endpoint
# default.
#
# ``vehicle_model="TRICITY155"``: HARNESS-23 found the corpus had
# drifted since the issue was written — the goldens' Yamaha manual
# ("MWS150-A 中文SERVICE MANUAL.pdf") is stored under vehicle_model
# ``TRICITY155``, and a second manual (``Corolla E11``, Toyota) was
# ingested into the same pgvector table.  The old ``"MWS150-A"``
# label matched zero rows.
#
# ``RAG_EXACT_SCAN``: with two manuals sharing the HNSW index, a
# hard single-manual filter is starved to zero rows — HNSW selects
# the approximate nearest neighbours first (all from the larger
# English Corolla manual for cross-language queries) and only then
# applies the filter, so nothing survives even at the max
# ef_search=1000.  The exact sequential-scan path makes the filter
# faithful again so the RAG lane actually retrieves Yamaha content.
# See ``rag_runner._sync_exact_vector_query`` for the full
# rationale.
RAG_TOP_K = 5
RAG_VEHICLE_MODEL = "TRICITY155"
RAG_EXACT_SCAN = True


def build_manual_run(
    entry: GoldenEntry,
    deps: Optional[Any] = None,
) -> RunFn:
    """Build the manual-agent run callable for one golden.

    Args:
        entry: Golden entry supplying question / obd_context /
            optional vehicle override.
        deps: Optional pre-built ``ManualAgentDeps`` (the
            ``--mock-agent`` stub); ``None`` → the runner's
            process-cached default pointing at local Ollama.

    Returns:
        Zero-arg coroutine function producing a
        ``SystemRunResult`` with ``system_label="manual_agent"``.
    """
    vehicle = (
        entry.vehicle
        if entry.vehicle is not None
        else CORPUS_VEHICLE
    )

    async def _run() -> SystemRunResult:
        return await run_manual_agent_unified(
            entry.question,
            entry.obd_context,
            deps=deps,
            vehicle=vehicle,
        )

    return _run


def build_rag_run(entry: GoldenEntry) -> RunFn:
    """Build the RAG run callable for one golden.

    Args:
        entry: Golden entry supplying the question.

    Returns:
        Zero-arg coroutine function producing a
        ``SystemRunResult`` with ``system_label="rag"``.
    """

    async def _run() -> SystemRunResult:
        return await run_rag(
            question=entry.question,
            top_k=RAG_TOP_K,
            vehicle_model=RAG_VEHICLE_MODEL,
            exact=RAG_EXACT_SCAN,
        )

    return _run
