"""RAG retrieval service using pgvector cosine similarity."""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from app.db.session import SessionLocal
from app.models_db import RagChunk
from app.rag.embedding import embedding_service

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=4)


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


def _sync_query(
    vector: list, top_k: int,
) -> List[RetrievalResult]:
    """Run pgvector cosine-distance query synchronously.

    Args:
        vector: Query embedding (768-dim float list).
        top_k: Maximum number of results.

    Returns:
        List of RetrievalResult sorted by relevance.
    """
    db = SessionLocal()
    try:
        distance_col = RagChunk.embedding.cosine_distance(
            vector,
        ).label("distance")

        rows = (
            db.query(
                RagChunk.text,
                RagChunk.doc_id,
                RagChunk.source_type,
                RagChunk.section_title,
                RagChunk.chunk_index,
                RagChunk.metadata_json,
                distance_col,
            )
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
            meta = row.metadata_json or {}

            results.append(RetrievalResult(
                text=row.text,
                score=score,
                doc_id=row.doc_id or "unknown",
                source_type=row.source_type or "unknown",
                section_title=(
                    row.section_title or "unknown"
                ),
                chunk_index=row.chunk_index or 0,
                metadata=meta,
            ))

        return results
    except Exception as e:
        logger.error(
            "pgvector retrieval failed", exc_info=e,
        )
        return []
    finally:
        db.close()


async def retrieve_context(
    query: str,
    top_k: int = 3,
    filters: Optional[Dict[str, Any]] = None,
) -> List[RetrievalResult]:
    """Retrieve relevant chunks from pgvector.

    Args:
        query: Search query string.
        top_k: Maximum number of results to return.
        filters: Optional filter criteria (reserved for
            future use).

    Returns:
        List of RetrievalResult items sorted by relevance.
    """
    # 1. Clamp top_k to safe bounds
    top_k = max(1, min(top_k, 20))

    # 2. Generate embedding
    vector = await embedding_service.get_embedding(query)
    if not vector:
        logger.warning(
            "Failed to generate embedding for query.",
        )
        return []

    # 3. Run sync DB query in thread pool to avoid
    #    blocking the event loop.
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _executor, partial(_sync_query, vector, top_k),
    )
