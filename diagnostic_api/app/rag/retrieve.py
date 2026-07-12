"""RAG retrieval service.

Supports three retrieval modes against the ``rag_chunks`` table:

- ``vector`` (default) — pgvector cosine similarity over the
  ``embedding`` column.  Preserves prior behaviour.
- ``keyword`` — ``tsvector @@ plainto_tsquery`` keyword match,
  ranked by ``ts_rank``.  Useful for exact-token queries
  (DTC codes, part numbers).
- ``hybrid`` — linear combination of the two, controlled by
  ``alpha``.  Designed to make DTC queries land their exact match
  in the top-K without regressing natural-language recall.

Hybrid scoring uses per-query min-max normalisation of the keyword
``ts_rank`` so the linear combination with the bounded cosine
similarity score is meaningful.  Final ``score`` on the returned
``RetrievalResult`` is the fused score; absolute values are not
comparable across queries in hybrid mode (relative order within a
single query is what matters).

Filtered queries bypass the HNSW index (APP-62 / Issue #156): when a
hard ``vehicle_model`` filter is set, the planner is forced to an
exact sequential scan for the current transaction only — HNSW picks
approximate nearest neighbours FIRST and filters AFTER, which starves
a single-manual filter to zero rows once a second, larger manual
shares the index.  See ``_force_exact_scan_for_filter``.

APP-56 / Issue #18.  APP-62 / Issue #156.
"""

import asyncio
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models_db import RagChunk
from app.rag.embedding import embedding_service

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=4)

# How many candidates each branch pulls before fusion.  A hit ranked
# #5 by keyword and #5 by vector should still rank well after fusion;
# this only works if both branches over-pull relative to top_k.
_CANDIDATE_MULTIPLIER = 4
_CANDIDATE_CAP = 100

RetrievalMode = Literal["vector", "keyword", "hybrid"]


class RetrievalResult(BaseModel):
    """Retrieval result item."""

    text: str
    score: float
    doc_id: str
    source_type: str
    section_title: str
    chunk_index: int
    metadata: Dict[str, Any] = {}


class RetrievalService:
    """Service wrapper for retrieval logic."""

    async def retrieve_context(
        self, query: str, limit: int = 3,
    ) -> List["RetrievalResult"]:
        """Retrieve relevant context for a query.

        Args:
            query: Search query string.
            limit: Maximum number of results.

        Returns:
            List of matching RetrievalResult items.
        """
        return await retrieve_context(query, top_k=limit)


def _row_to_result(
    row: Any, score: float,
) -> RetrievalResult:
    """Build a ``RetrievalResult`` from a query row + final score."""
    meta = row.metadata_json or {}
    return RetrievalResult(
        text=row.text,
        score=score,
        doc_id=row.doc_id or "unknown",
        source_type=row.source_type or "unknown",
        section_title=row.section_title or "unknown",
        chunk_index=row.chunk_index or 0,
        metadata=meta,
    )


def _force_exact_scan_for_filter(
    db: Session,
    vehicle_model: Optional[str],
) -> str:
    """Pick the scan path for this transaction; force exact if filtered.

    HNSW on pgvector 0.7.4 (no iterative scan) selects the approximate
    nearest neighbours FIRST and applies the ``WHERE`` clause AFTER.
    With >=2 manuals sharing the index, a hard ``vehicle_model``
    filter starves to 0 rows when the other manual dominates the
    candidate pool — proven at the pgvector maximum
    ``hnsw.ef_search=1000`` with TRICITY155 (1665 chunks) vs
    Corolla E11 (3051 chunks) returning zero TRICITY155 rows
    (HARNESS-23 / Issue #156).

    ``SET LOCAL`` scopes the planner override to the current
    transaction only, so unfiltered queries on other connections keep
    the HNSW speedup.  The exact scan returns the same cosine scores
    in true order (HNSW is only an approximate-NN speedup) and is
    cheap at this corpus scale (~5k chunks total, <=3051 per manual,
    sub-100 ms) — mirrors the eval adapter
    ``tests/harness/evals/rag_runner._sync_exact_vector_query``,
    which proved the exact path correct.

    Args:
        db: Open session; the override binds to its current
            transaction.
        vehicle_model: The hard vehicle-model filter, or ``None``.

    Returns:
        ``"exact"`` if index scans were disabled for the transaction,
        ``"hnsw"`` otherwise.
    """
    if not vehicle_model:
        return "hnsw"
    db.execute(sa_text("SET LOCAL enable_indexscan = off"))
    db.execute(sa_text("SET LOCAL enable_bitmapscan = off"))
    return "exact"


def _sync_vector_query(
    vector: list,
    top_k: int,
    vehicle_model: Optional[str] = None,
    exclude_chunk_ids: Optional[List[int]] = None,
) -> List[RetrievalResult]:
    """Pure cosine-similarity query (legacy behaviour).

    When ``vehicle_model`` is set, the query runs as an exact
    sequential scan instead of HNSW (see
    ``_force_exact_scan_for_filter`` for why).

    Args:
        vector: Query embedding (768-dim float list).
        top_k: Maximum number of results.
        vehicle_model: Optional vehicle model filter
            (e.g. ``"MWS-150-A"``).
        exclude_chunk_ids: Optional chunk indices to exclude.

    Returns:
        List of RetrievalResult sorted by descending similarity.
    """
    db = SessionLocal()
    try:
        scan_path = _force_exact_scan_for_filter(db, vehicle_model)
        logger.info(
            "rag_retrieval mode=vector scan_path=%s "
            "vehicle_model=%s top_k=%d",
            scan_path, vehicle_model, top_k,
        )

        distance_col = RagChunk.embedding.cosine_distance(
            vector,
        ).label("distance")

        query = db.query(
            RagChunk.text,
            RagChunk.doc_id,
            RagChunk.source_type,
            RagChunk.section_title,
            RagChunk.chunk_index,
            RagChunk.metadata_json,
            distance_col,
        )

        if vehicle_model:
            query = query.filter(
                RagChunk.vehicle_model == vehicle_model,
            )
        if exclude_chunk_ids:
            query = query.filter(
                RagChunk.chunk_index.notin_(
                    exclude_chunk_ids,
                ),
            )

        rows = (
            query
            .order_by(distance_col)
            .limit(top_k)
            .all()
        )

        results = []
        for row in rows:
            # cosine_distance = 1 - cosine_similarity
            # score = 1 - cosine_distance = cosine_similarity
            score = 1.0 - (
                row.distance if row.distance is not None
                else 1.0
            )
            results.append(_row_to_result(row, score))

        return results
    except Exception as e:
        logger.error(
            "pgvector retrieval failed", exc_info=e,
        )
        return []
    finally:
        db.close()


def _candidate_pool_size(top_k: int) -> int:
    """How many candidates to pull from each fusion branch."""
    return min(top_k * _CANDIDATE_MULTIPLIER, _CANDIDATE_CAP)


# A tsquery token is alphanumeric or alphanumeric-with-internal-dot
# (for things like part-number ``ABC.123``).  Any token that doesn't
# match is dropped — keeps single-quotes, backslashes, and other
# operators that ``to_tsquery`` would reject out of the query.
_TSQUERY_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:\.[A-Za-z0-9]+)*")


def _build_or_tsquery(query_str: str) -> str:
    """Build an OR-joined tsquery string for keyword retrieval.

    ``plainto_tsquery`` AND-joins terms, which makes the keyword
    branch return zero rows for any query longer than ~2 words.
    We want OR semantics — any matching term contributes — so we
    tokenise client-side and emit ``term1 | term2 | ...`` for
    ``to_tsquery``.

    Returns an empty string if no tokens survive (caller must
    avoid passing this to ``to_tsquery`` since the empty query
    raises).
    """
    tokens = [
        tok.lower()
        for tok in _TSQUERY_TOKEN_RE.findall(query_str)
        if tok
    ]
    if not tokens:
        return ""
    return " | ".join(tokens)


def _build_filter_clause(
    vehicle_model: Optional[str],
    exclude_chunk_ids: Optional[List[int]],
) -> tuple[str, Dict[str, Any]]:
    """Build the shared WHERE-clause fragment + params.

    Returns a ``(sql_fragment, params)`` pair where ``sql_fragment``
    is prefixed with ``AND`` (or empty) so it can be appended to a
    base ``WHERE`` clause that always evaluates to true.
    """
    fragments: List[str] = []
    params: Dict[str, Any] = {}
    if vehicle_model:
        fragments.append("vehicle_model = :vehicle_model")
        params["vehicle_model"] = vehicle_model
    if exclude_chunk_ids:
        fragments.append("chunk_index <> ALL(:exclude_ids)")
        params["exclude_ids"] = list(exclude_chunk_ids)
    if not fragments:
        return "", params
    return "AND " + " AND ".join(fragments), params


def _sync_keyword_query(
    query_str: str,
    top_k: int,
    vehicle_model: Optional[str] = None,
    exclude_chunk_ids: Optional[List[int]] = None,
) -> List[RetrievalResult]:
    """Pure full-text keyword query ranked by ``ts_rank``.

    Args:
        query_str: User query (plain text — tokenised client-side
            into an OR-joined ``to_tsquery`` for OR semantics; see
            ``_build_or_tsquery`` for why ``plainto_tsquery`` is
            unsuitable here).
        top_k: Maximum number of results.
        vehicle_model: Optional vehicle model filter.
        exclude_chunk_ids: Optional chunk indices to exclude.

    Returns:
        List of RetrievalResult sorted by descending ts_rank.
        Empty list if the query contains no usable tokens.
    """
    tsq = _build_or_tsquery(query_str)
    if not tsq:
        return []

    filter_sql, filter_params = _build_filter_clause(
        vehicle_model, exclude_chunk_ids,
    )
    sql = sa_text(
        f"""
        SELECT
            text,
            doc_id,
            source_type,
            section_title,
            chunk_index,
            metadata_json,
            ts_rank(tsv, to_tsquery('english', :tsq)) AS rank
        FROM rag_chunks
        WHERE tsv @@ to_tsquery('english', :tsq)
        {filter_sql}
        ORDER BY rank DESC
        LIMIT :limit
        """
    )

    db = SessionLocal()
    try:
        rows = db.execute(
            sql,
            {"tsq": tsq, "limit": top_k, **filter_params},
        ).fetchall()
        return [_row_to_result(r, float(r.rank)) for r in rows]
    except Exception as e:
        logger.error(
            "keyword retrieval failed", exc_info=e,
        )
        return []
    finally:
        db.close()


def _format_pgvector(vector: List[float]) -> str:
    """Format a Python float list as a pgvector text literal.

    pgvector accepts ``'[1.0,2.0,...]'`` strings on the wire, which
    avoids psycopg2 trying to coerce the list into a Postgres array.
    """
    return "[" + ",".join(f"{v:.7f}" for v in vector) + "]"


def _sync_hybrid_query(
    vector: list,
    query_str: str,
    top_k: int,
    alpha: float,
    vehicle_model: Optional[str] = None,
    exclude_chunk_ids: Optional[List[int]] = None,
) -> List[RetrievalResult]:
    """Hybrid query: linearly fuse keyword and vector rankings.

    Pulls an over-sized candidate pool from each branch
    (``_candidate_pool_size``), min-max normalises ``ts_rank``
    across the keyword pool, then fuses with cosine similarity via
    ``alpha * vec + (1 - alpha) * kw``.  ``alpha=1`` collapses to
    pure vector; ``alpha=0`` collapses to pure keyword.

    When ``vehicle_model`` is set, the statement runs as an exact
    sequential scan — the semantic CTE otherwise hits the same
    HNSW filter starvation as the pure-vector path (see
    ``_force_exact_scan_for_filter``).

    Args:
        vector: Query embedding.
        query_str: User query text.
        top_k: Maximum results to return.
        alpha: Weight on the vector score, in ``[0, 1]``.
        vehicle_model: Optional vehicle model filter.
        exclude_chunk_ids: Optional chunk indices to exclude.

    Returns:
        List of RetrievalResult sorted by descending fused score.
    """
    pool = _candidate_pool_size(top_k)
    filter_sql, filter_params = _build_filter_clause(
        vehicle_model, exclude_chunk_ids,
    )
    tsq = _build_or_tsquery(query_str)

    # If the query has no usable keyword tokens, fall back to the
    # pure semantic CTE — keyword AS would otherwise raise on an
    # empty to_tsquery argument.
    keyword_cte = "" if not tsq else f"""
        , keyword AS (
            SELECT
                id,
                text,
                doc_id,
                source_type,
                section_title,
                chunk_index,
                metadata_json,
                NULL::double precision AS vec_score,
                ts_rank(tsv, to_tsquery('english', :tsq))
                    AS kw_rank
            FROM rag_chunks
            WHERE tsv @@ to_tsquery('english', :tsq)
            {filter_sql}
            ORDER BY kw_rank DESC
            LIMIT :pool
        )
    """
    keyword_union = (
        "" if not tsq
        else "UNION ALL SELECT * FROM keyword"
    )

    sql = sa_text(
        f"""
        WITH semantic AS (
            SELECT
                id,
                text,
                doc_id,
                source_type,
                section_title,
                chunk_index,
                metadata_json,
                1 - (embedding <=> CAST(:qvec AS vector))
                    AS vec_score,
                NULL::real AS kw_rank
            FROM rag_chunks
            WHERE TRUE {filter_sql}
            ORDER BY embedding <=> CAST(:qvec AS vector)
            LIMIT :pool
        )
        {keyword_cte},
        unioned AS (
            SELECT * FROM semantic
            {keyword_union}
        )
        SELECT
            id,
            MAX(text) AS text,
            MAX(doc_id) AS doc_id,
            MAX(source_type) AS source_type,
            MAX(section_title) AS section_title,
            MAX(chunk_index) AS chunk_index,
            MAX(metadata_json::text)::jsonb AS metadata_json,
            MAX(vec_score) AS vec_score,
            MAX(kw_rank) AS kw_rank
        FROM unioned
        GROUP BY id
        """
    )

    params: Dict[str, Any] = {
        "qvec": _format_pgvector(vector),
        "pool": pool,
        **filter_params,
    }
    if tsq:
        params["tsq"] = tsq

    db = SessionLocal()
    try:
        scan_path = _force_exact_scan_for_filter(db, vehicle_model)
        logger.info(
            "rag_retrieval mode=hybrid scan_path=%s "
            "vehicle_model=%s top_k=%d",
            scan_path, vehicle_model, top_k,
        )

        rows = db.execute(sql, params).fetchall()

        if not rows:
            return []

        # Min-max normalise keyword ranks across the candidate pool;
        # cosine similarity is already in roughly [0, 1] from pgvector.
        kw_raw = [
            float(r.kw_rank) if r.kw_rank is not None else 0.0
            for r in rows
        ]
        # Only normalise positive values — chunks with no keyword
        # match contribute 0.0 to the fused score.
        non_zero = [v for v in kw_raw if v > 0.0]
        if non_zero:
            lo, hi = min(non_zero), max(non_zero)
            span = hi - lo if hi > lo else 0.0
            kw_norm = [
                (v - lo) / span if (v > 0.0 and span > 0.0)
                else (1.0 if v > 0.0 else 0.0)
                for v in kw_raw
            ]
        else:
            kw_norm = [0.0 for _ in kw_raw]

        scored: List[tuple[float, Any]] = []
        for row, kw in zip(rows, kw_norm):
            vec = (
                float(row.vec_score)
                if row.vec_score is not None
                else 0.0
            )
            fused = alpha * vec + (1.0 - alpha) * kw
            scored.append((fused, row))

        scored.sort(key=lambda pair: pair[0], reverse=True)

        return [
            _row_to_result(row, score)
            for score, row in scored[:top_k]
        ]
    except Exception as e:
        logger.error(
            "hybrid retrieval failed", exc_info=e,
        )
        return []
    finally:
        db.close()


async def retrieve_context(
    query: str,
    top_k: int = 3,
    filters: Optional[Dict[str, Any]] = None,
    vehicle_model: Optional[str] = None,
    exclude_chunk_ids: Optional[List[int]] = None,
    mode: RetrievalMode = "vector",
    alpha: float = 0.5,
) -> List[RetrievalResult]:
    """Retrieve relevant chunks from pgvector.

    Args:
        query: Search query string.
        top_k: Maximum number of results to return.
        filters: Optional filter criteria (reserved for
            future use).
        vehicle_model: Optional vehicle model to restrict
            search to (e.g. ``"MWS-150-A"``).
        exclude_chunk_ids: Optional chunk indices to exclude
            from results.
        mode: ``"vector"`` (default, preserves prior behaviour),
            ``"keyword"`` (full-text only), or ``"hybrid"``
            (linear fusion of the two).
        alpha: Weight on the vector branch in ``[0, 1]``.  Only
            used when ``mode="hybrid"``.  ``alpha=1`` ≈ pure
            vector; ``alpha=0`` ≈ pure keyword.

    Returns:
        List of RetrievalResult items sorted by relevance.
    """
    # 1. Clamp inputs to safe bounds.
    top_k = max(1, min(top_k, 20))
    alpha = max(0.0, min(alpha, 1.0))

    # 2. Keyword-only path skips the embedding round-trip entirely.
    if mode == "keyword":
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _executor,
            partial(
                _sync_keyword_query,
                query,
                top_k,
                vehicle_model=vehicle_model,
                exclude_chunk_ids=exclude_chunk_ids,
            ),
        )

    # 3. Vector + hybrid both need the embedding.
    vector = await embedding_service.get_embedding(query)
    if not vector:
        logger.warning(
            "Failed to generate embedding for query.",
        )
        return []

    loop = asyncio.get_running_loop()
    if mode == "hybrid":
        return await loop.run_in_executor(
            _executor,
            partial(
                _sync_hybrid_query,
                vector,
                query,
                top_k,
                alpha,
                vehicle_model=vehicle_model,
                exclude_chunk_ids=exclude_chunk_ids,
            ),
        )

    # mode == "vector" (default)
    return await loop.run_in_executor(
        _executor,
        partial(
            _sync_vector_query,
            vector,
            top_k,
            vehicle_model=vehicle_model,
            exclude_chunk_ids=exclude_chunk_ids,
        ),
    )
