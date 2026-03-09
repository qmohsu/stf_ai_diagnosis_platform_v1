"""Weaviate retrieval service for RAG knowledge chunks."""

import logging
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from app.rag.client import get_client
from app.rag.embedding import embedding_service

logger = logging.getLogger(__name__)


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


async def retrieve_context(
    query: str,
    top_k: int = 3,
    filters: Optional[Dict[str, Any]] = None,
) -> List[RetrievalResult]:
    """Retrieve relevant chunks from Weaviate.

    Args:
        query: Search query string.
        top_k: Maximum number of results to return.
        filters: Optional filter criteria for the query.

    Returns:
        List of RetrievalResult items sorted by relevance.
    """
    # 1. Clamp top_k to safe bounds
    top_k = max(1, min(top_k, 20))

    # 2. Generate embedding
    vector = await embedding_service.get_embedding(query)
    if not vector:
        logger.warning("Failed to generate embedding for query.")
        return []

    # 3. Query Weaviate
    client = get_client()
    try:
        gql_query = f"""
        {{
          Get {{
            KnowledgeChunk(
              nearVector: {{
                vector: {vector}
              }}
              limit: {int(top_k)}
            ) {{
              text
              doc_id
              source_type
              section_title
              chunk_index
              _additional {{
                distance
                score
              }}
            }}
          }}
        }}
        """

        response = client.graphql_raw_query(gql_query)

        chunk_data = None
        if hasattr(response, 'get'):
            chunk_data = response.get
        elif hasattr(response, 'result'):
            res = response.result
            if (
                hasattr(res, 'get')
                and not callable(res.get)
            ):
                chunk_data = res.get
            elif isinstance(res, dict):
                chunk_data = (
                    res.get('data', {}).get('Get')
                )

        if (
            not chunk_data
            or 'KnowledgeChunk' not in chunk_data
        ):
            return []

        results = []
        for obj in chunk_data['KnowledgeChunk']:
            meta = obj.get('_additional', {})
            dist = meta.get('distance', 1.0)
            score = 1.0 - dist if dist is not None else 0.0

            results.append(RetrievalResult(
                text=obj.get('text', ""),
                score=score,
                doc_id=obj.get('doc_id', "unknown"),
                source_type=obj.get(
                    'source_type', "unknown",
                ),
                section_title=obj.get(
                    'section_title', "unknown",
                ),
                chunk_index=obj.get('chunk_index', 0),
                metadata=meta,
            ))

        return results

    except Exception as e:
        logger.error("Weaviate retrieval failed", exc_info=e)
        return []
    finally:
        client.close()
