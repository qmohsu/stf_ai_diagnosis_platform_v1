"""RAG runner for the comparative manual-eval suite.

The complement to ``runner.run_manual_agent``: takes a question
and returns a unified ``SystemRunResult`` whose ``retrieved_slugs``
are obtained via pgvector top-k retrieval rather than agent
navigation.

Why this lives in the eval suite (not in production code):

The production RAG path (``app.rag.retrieve.retrieve_context``)
is already used by the V1 endpoints and the V2 obd-analysis flow.
This module is a *measurement adapter* — it calls the same
production retrieval but normalises the output into the eval
suite's ``SystemRunResult`` shape and captures latency / cost
trade-off metrics that production callers don't need.

Author: Li-Ta Hsu
"""

from __future__ import annotations

import time
from typing import List, Literal, Optional

import structlog

from app.harness_tools.manual_fs import slugify
from app.rag.retrieve import retrieve_context, RetrievalResult


RagMode = Literal["vector", "keyword", "hybrid"]
from tests.harness.evals.schemas import (
    RetrievedChunkMetadata,
    SystemRunResult,
)

logger = structlog.get_logger(__name__)


# ── Constants ─────────────────────────────────────────────────────


_DEFAULT_TOP_K = 5
"""Default top-k for RAG retrievals.  Aligns with what production
endpoints request (``/v2/obd/.../diagnose`` uses 3–5).  Exposed
as an argument so eval reports can also probe ``top_k=10``,
``top_k=20`` for recall@k curves."""


_TEXT_PREVIEW_CHARS = 120
"""Length of the per-chunk text preview included in
``RetrievedChunkMetadata.text_preview`` for human inspection
of the eval report."""


# ── Public entry point ────────────────────────────────────────────


async def run_rag(
    question: str,
    top_k: int = _DEFAULT_TOP_K,
    vehicle_model: Optional[str] = None,
    mode: RagMode = "vector",
    alpha: float = 0.5,
) -> SystemRunResult:
    """Execute RAG retrieval against pgvector and normalise the result.

    Args:
        question: The user inquiry.  Sent verbatim as the query
            string — no rewriting.
        top_k: Maximum number of chunks to return (clamped to
            ``[1, 20]`` by the underlying ``retrieve_context``).
        vehicle_model: Optional filter, passed straight through
            to ``retrieve_context``.  Note the production
            inconsistency: ``rag_chunks.vehicle_model`` is
            ``"MWS150-A"`` while ``manuals.vehicle_model`` is
            ``"MWS-150-A"``.  Pass ``"MWS150-A"`` to filter the
            current corpus, or ``None`` for unfiltered retrieval
            across all manuals.
        mode: Retrieval mode passed to ``retrieve_context``.
            ``"vector"`` reproduces pre-APP-56 behaviour and is
            the default.  ``"hybrid"`` enables linear fusion with
            ``ts_rank`` keyword scoring (Issue #18).  The
            ``system_label`` on the returned result stays
            ``"rag"`` regardless — callers tag their own report
            rows with the mode they invoked.
        alpha: Weight on the vector branch in ``[0, 1]`` when
            ``mode="hybrid"``.  Ignored otherwise.

    Returns:
        ``SystemRunResult`` with ``system_label="rag"`` and
        populated ``retrieved_slugs``,
        ``retrieved_chunk_metadata``, ``output_text`` (top-k
        chunks concatenated for the judge's must_contain
        scan), and trade-off metrics.

        On retrieval failure or empty results, returns a result
        with empty ``retrieved_slugs`` and a sentinel
        ``output_text`` so the judge can still score it.
    """
    wall_start = time.perf_counter()
    try:
        chunks: List[RetrievalResult] = await retrieve_context(
            query=question,
            top_k=top_k,
            vehicle_model=vehicle_model,
            mode=mode,
            alpha=alpha,
        )
    except Exception as exc:  # noqa: BLE001 — eval should not crash
        logger.error(
            "rag_runner.retrieve_failed",
            error=repr(exc),
            question_preview=question[:80],
        )
        chunks = []
    wall_end = time.perf_counter()
    latency_ms_wall = (wall_end - wall_start) * 1000

    # ── Normalise chunks → metadata + slug lists ────────────────

    metadata: List[RetrievedChunkMetadata] = []
    slug_seq: List[str] = []
    for chunk in chunks:
        # ``chunk.section_title`` is the heading text.  Slugify
        # it to bridge to ``GoldenCitation.slug`` (which is the
        # parser-canonical form from ``parse_heading_tree``).
        chunk_slug = slugify(chunk.section_title or "")

        meta = chunk.metadata or {}
        # ``metadata`` JSON has ``dtc_codes`` and ``has_image``
        # per the ingestion pipeline (APP-45 onward).  Read
        # defensively in case older chunks lack the keys.
        dtc_codes = meta.get("dtc_codes") or []
        has_image = bool(meta.get("has_image", False))
        if not isinstance(dtc_codes, list):
            dtc_codes = []

        text_preview = (chunk.text or "")[:_TEXT_PREVIEW_CHARS]

        metadata.append(RetrievedChunkMetadata(
            chunk_index=chunk.chunk_index,
            score=chunk.score,
            section_title=chunk.section_title or "",
            slug=chunk_slug,
            has_image=has_image,
            dtc_codes=[str(c) for c in dtc_codes],
            text_preview=text_preview,
        ))
        slug_seq.append(chunk_slug)

    # Deduplicate while preserving order — multiple chunks may
    # belong to the same heading; ``section_recall`` should not
    # count them multiple times.
    seen: set = set()
    unique_slugs: List[str] = []
    for s in slug_seq:
        if s and s not in seen:
            seen.add(s)
            unique_slugs.append(s)

    # RAG has no separate synthesis step — its retrieval IS its
    # claim.  Both ``claim_slugs`` and ``read_slugs`` get the
    # same list, which makes ``exploration_cost`` always 0.0
    # for RAG (everything it accessed is part of its claim).
    # That's intentional: the navigation-vs-grounding distinction
    # only exists for the agent.
    claim_slugs = unique_slugs
    read_slugs = unique_slugs

    # ── Compose output_text for the judge's must_contain scan ────

    if chunks:
        output_text = "\n\n".join(
            (chunk.text or "") for chunk in chunks
        )
    else:
        output_text = "(no chunks retrieved)"

    # ── Latency: LLM-only is the embedding-call time ─────────────
    # ``retrieve_context`` doesn't currently expose embedding
    # latency separately; we approximate with wall-clock until
    # the production retriever surfaces a finer-grained timing.
    # For the comparative benchmark this is acceptable because
    # the LLM-only latency for RAG is dominated by the embedding
    # call (the pgvector query is sub-millisecond).
    latency_ms_llm = latency_ms_wall

    logger.info(
        "rag_runner.complete",
        question_preview=question[:80],
        top_k=top_k,
        mode=mode,
        alpha=alpha if mode == "hybrid" else None,
        retrieved=len(chunks),
        unique_slugs=len(unique_slugs),
        latency_ms_wall=round(latency_ms_wall, 1),
    )

    return SystemRunResult(
        system_label="rag",
        question=question,
        output_text=output_text,
        claim_slugs=claim_slugs,
        read_slugs=read_slugs,
        retrieved_chunk_metadata=metadata,
        latency_ms_wall=latency_ms_wall,
        latency_ms_llm=latency_ms_llm,
        cost_usd=0.0,  # Local Ollama embedding — free.
        tool_trace=[],
        stopped_reason="complete",
        iterations=1,
    )
