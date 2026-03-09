"""RAG related endpoints."""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.rag.retrieve import retrieve_context, RetrievalResult

logger = logging.getLogger(__name__)

router = APIRouter()


class RetrievalRequest(BaseModel):
    """RAG retrieval request body."""

    query: str
    top_k: int = Field(default=3, ge=1, le=20)
    filters: Optional[Dict[str, Any]] = None


class RetrievalResponse(BaseModel):
    """RAG retrieval response body."""

    results: List[RetrievalResult]


@router.post("/retrieve", response_model=RetrievalResponse)
async def retrieve(request: RetrievalRequest) -> RetrievalResponse:
    """Retrieve relevant context for a given query.

    Used by the diagnosis engine to fetch manual chunks.

    Args:
        request: Retrieval request with query and optional filters.

    Returns:
        List of matching knowledge chunks.

    Raises:
        HTTPException: 400 if query is empty, 500 on internal error.
    """
    if not request.query.strip():
        raise HTTPException(
            status_code=400, detail="Query cannot be empty",
        )

    try:
        results = await retrieve_context(
            query=request.query,
            top_k=request.top_k,
            filters=request.filters,
        )
        return RetrievalResponse(results=results)
    except Exception as e:
        logger.error("RAG retrieval failed", exc_info=e)
        raise HTTPException(
            status_code=500,
            detail="Knowledge retrieval failed",
        )
