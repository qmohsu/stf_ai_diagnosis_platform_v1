"""RAG related endpoints."""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.rag.retrieve import retrieve_context, RetrievalResult

router = APIRouter()

class RetrievalRequest(BaseModel):
    query: str
    top_k: int = 3
    filters: Optional[Dict[str, Any]] = None

class RetrievalResponse(BaseModel):
    results: List[RetrievalResult]

@router.post("/retrieve", response_model=RetrievalResponse)
async def retrieve(request: RetrievalRequest):
    """
    Retrieve relevant context for a given query.
    Used by the diagnosis engine to fetch manual chunks.
    """
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be empty")
        
    try:
        results = await retrieve_context(
            query=request.query,
            top_k=request.top_k,
            filters=request.filters
        )
        return RetrievalResponse(results=results)
    except Exception as e:
        print(f"Endpoint error: {e}")
        # Return empty list or 500? Use 500 for valid platform errors
        raise HTTPException(status_code=500, detail=str(e))
