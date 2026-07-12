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

Deliberately synthesis-free (T15 / GitHub issue #159, decided
2026-07-12): this lane concatenates the raw top-k chunks with NO
LLM synthesis step, BY DESIGN — it is the retrieval-floor
comparison base for the agent-vs-RAG eval, and its low
``answer_quality`` (~0.05) is the expected price of that role.
Do not add synthesis here; a synthesised-RAG arm, if ever wanted,
is a separate additive ``rag_synth`` lane. Full decision:
``docs/harness_14_phase6_followups.md`` -> "T15 decision — RAG
synthesis".

Author: Li-Ta Hsu
"""

from __future__ import annotations

import asyncio
import time
from functools import partial
from typing import Any, Dict, List, Literal, Optional

import httpx
import structlog
from sqlalchemy import text as sa_text

from app.config import settings
from app.db.session import SessionLocal
from app.harness_tools.manual_fs import slugify
from app.rag.retrieve import RetrievalResult, retrieve_context


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


# ── Exact (non-HNSW) retrieval for the comparative baseline ───────


def _format_pgvector(vector: List[float]) -> str:
    """Format a float list as a pgvector text literal."""
    return "[" + ",".join(f"{v:.7f}" for v in vector) + "]"


def _sync_exact_vector_query(
    vector: List[float],
    top_k: int,
    vehicle_model: Optional[str],
) -> List[RetrievalResult]:
    """Exact (sequential-scan) cosine query, optionally model-filtered.

    The production ``retrieve_context`` path uses the HNSW index,
    which applies the ``vehicle_model`` filter *after* selecting the
    approximate nearest neighbours.  Once a second, larger manual
    shares the index, a hard single-manual filter is starved to zero
    rows for cross-language queries: the English ``Corolla E11``
    manual crowds the Chinese-translated ``TRICITY155`` manual out of
    the candidate pool entirely — even at ``hnsw.ef_search`` = 1000
    (the pgvector 0.7.4 max), the TRICITY155 filter returns 0 rows
    (HARNESS-23, verified 2026-06-20).

    Forcing a sequential scan makes the filter exact again, so the
    agent-vs-RAG baseline measures the *corpus's* retrieval quality
    rather than the HNSW recall pathology.  The scores are identical
    cosine similarities — HNSW is purely an approximate-NN speedup,
    so an exact scan is the faithful "cosine-distance retrieval" the
    ticket calls for.  Per-manual corpora are small (≤3051 chunks),
    so the full scan is sub-100 ms.

    This stays in the eval suite (a measurement adapter); production
    retrieval is left untouched.

    Args:
        vector: Query embedding (768-dim float list).
        top_k: Maximum rows to return.
        vehicle_model: Optional exact ``vehicle_model`` filter.

    Returns:
        ``RetrievalResult`` list sorted by descending similarity.
        Empty list on failure (the eval must not crash).
    """
    db = SessionLocal()
    try:
        # ``SET LOCAL`` scopes the planner override to this
        # transaction only; production queries on other connections
        # keep using the HNSW index.
        db.execute(sa_text("SET LOCAL enable_indexscan = off"))
        db.execute(sa_text("SET LOCAL enable_bitmapscan = off"))

        filter_sql = ""
        params: Dict[str, Any] = {
            "qv": _format_pgvector(vector),
            "limit": top_k,
        }
        if vehicle_model:
            filter_sql = "WHERE vehicle_model = :vm"
            params["vm"] = vehicle_model

        sql = sa_text(
            f"""
            SELECT
                text,
                doc_id,
                source_type,
                section_title,
                chunk_index,
                metadata_json,
                1 - (embedding <=> CAST(:qv AS vector)) AS score
            FROM rag_chunks
            {filter_sql}
            ORDER BY embedding <=> CAST(:qv AS vector)
            LIMIT :limit
            """
        )
        rows = db.execute(sql, params).fetchall()
        results: List[RetrievalResult] = []
        for row in rows:
            results.append(RetrievalResult(
                text=row.text,
                score=(
                    float(row.score)
                    if row.score is not None else 0.0
                ),
                doc_id=row.doc_id or "unknown",
                source_type=row.source_type or "unknown",
                section_title=row.section_title or "unknown",
                chunk_index=row.chunk_index or 0,
                metadata=row.metadata_json or {},
            ))
        return results
    except Exception as exc:  # noqa: BLE001 — eval must not crash.
        logger.error(
            "rag_runner.exact_retrieve_failed",
            error=repr(exc),
        )
        return []
    finally:
        db.close()


async def _embed_query(text: str, attempts: int = 3) -> List[float]:
    """Embed ``text`` with a fresh per-call ``httpx.AsyncClient``.

    EVAL-ONLY WORKAROUND — read before "fixing" (HARNESS-23 T17,
    issue #160).

    Why a fresh client per call: the production ``embedding_service``
    (``app.rag.embedding.embedding_service``) reuses a single
    module-level ``httpx.AsyncClient`` for connection pooling.  That
    is correct inside the app's single long-lived event loop, but
    breaks under pytest-asyncio, where each test runs in its OWN
    event loop: the singleton's client (and its pooled connections)
    stays bound to whichever loop first created it.  Once that loop
    closes, calls from a later test's loop hit a dead-loop
    connection, the request dies inside ``get_embedding()``'s broad
    exception handler, and retrieval SILENTLY returns zero chunks.
    A short-lived client created in the current loop sidesteps the
    cross-loop reuse entirely.

    Symptom signature (how this was found): the first combined
    HARNESS-23 baseline run (2026-06-20) silently retrieved 0 chunks
    on 15/30 RAG entries in an ALTERNATING pattern — the tell-tale
    of loop-affinity breakage (whether a test's loop matched the
    client's birth loop flip-flopped across the suite).  If an eval
    report ever shows alternating zero-retrievals again, suspect a
    shared async client before suspecting the corpus.

    Do NOT:
      * Do NOT swap this back to the production ``embedding_service``
        singleton "for consistency" — that reintroduces the 15/30
        silent-zero failure described above.
      * Do NOT "fix forward" ``app/rag/embedding.py`` (e.g. per-call
        clients or loop tracking in the service).  Production runs a
        single long-lived loop; its pooled singleton is CORRECT and
        faster there.  This is an eval-harness concern only.

    Args:
        text: Query string to embed.
        attempts: Max tries before giving up (transient Ollama hiccup
            resilience).

    Returns:
        768-dim embedding, or empty list after ``attempts`` failures.
    """
    base_url = settings.embedding_endpoint or settings.llm_endpoint
    payload = {"model": settings.embedding_model, "input": text}
    last_exc: Optional[Exception] = None
    for _ in range(attempts):
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    f"{base_url}/api/embed", json=payload,
                )
                resp.raise_for_status()
                embeddings = resp.json().get("embeddings", [])
                if embeddings and embeddings[0]:
                    return embeddings[0]
        except Exception as exc:  # noqa: BLE001 — retry then give up.
            last_exc = exc
    if last_exc is not None:
        logger.error("rag_runner.embed_failed", error=repr(last_exc))
    return []


async def _exact_vector_retrieve(
    question: str,
    top_k: int,
    vehicle_model: Optional[str],
) -> List[RetrievalResult]:
    """Embed the query, then run an exact cosine scan off-thread.

    Mirrors ``retrieve_context``'s embed-then-``run_in_executor``
    shape so the surrounding ``run_rag`` normalisation is identical
    for both the exact and HNSW paths.

    Args:
        question: User inquiry, embedded verbatim.
        top_k: Maximum rows to return.
        vehicle_model: Optional exact ``vehicle_model`` filter.

    Returns:
        ``RetrievalResult`` list, or empty on embedding failure.
    """
    vector = await _embed_query(question)
    if not vector:
        logger.warning(
            "rag_runner.embed_empty",
            question_preview=question[:80],
        )
        return []
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        partial(
            _sync_exact_vector_query, vector, top_k, vehicle_model,
        ),
    )


# ── Public entry point ────────────────────────────────────────────


async def run_rag(
    question: str,
    top_k: int = _DEFAULT_TOP_K,
    vehicle_model: Optional[str] = None,
    mode: RagMode = "vector",
    alpha: float = 0.5,
    exact: bool = False,
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
        exact: When ``True``, bypass the HNSW index and run an exact
            sequential-scan cosine query (``_exact_vector_retrieve``)
            instead of ``retrieve_context``.  Required for the
            agent-vs-RAG baseline when a ``vehicle_model`` filter is
            set: HNSW applies the filter *after* approximate-NN
            selection, which starves a single-manual filter to zero
            rows once a second, larger manual shares the index
            (HARNESS-23).  Ignores ``mode``/``alpha`` (exact cosine
            only).

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
        if exact:
            chunks: List[RetrievalResult] = await _exact_vector_retrieve(
                question=question,
                top_k=top_k,
                vehicle_model=vehicle_model,
            )
        else:
            chunks = await retrieve_context(
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
