"""RAG tool wrappers for the harness agent loop.

``search_manual`` wraps the existing ``retrieve_context()`` function.
``refine_search`` adds ``exclude_doc_ids`` support for adaptive RAG.
Both return text summaries with source metadata.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from app.harness.tool_registry import ToolDefinition
from app.rag.retrieve import retrieve_context

logger = logging.getLogger(__name__)

_MAX_TEXT_LEN = 500  # Truncate chunk text to this many chars.


# ------------------------------------------------------------------
# Shared formatter
# ------------------------------------------------------------------

def _fmt_result(r: Any) -> str:
    """Format a single RetrievalResult as one line.

    Args:
        r: A ``RetrievalResult`` object from ``retrieve_context``.

    Returns:
        Formatted line, e.g.
        ``"[0.87] MWS150-A#Fuel System — Check fuel pressure…"``.
    """
    text = r.text
    if len(text) > _MAX_TEXT_LEN:
        text = text[:_MAX_TEXT_LEN] + "..."
    return (
        f"[{r.score:.2f}] {r.doc_id}#{r.section_title}"
        f" -- {text}"
    )


# ------------------------------------------------------------------
# Tool: search_manual
# ------------------------------------------------------------------

async def search_manual(
    input_data: Dict[str, Any],
) -> str:
    """Search vehicle service manuals via RAG.

    Args:
        input_data: Must contain ``query`` (str).  Optional
            ``top_k`` (int, default 3).

    Returns:
        Formatted text of matched manual sections with source
        metadata and similarity scores.
    """
    query: str = input_data["query"]
    top_k: int = input_data.get("top_k", 3)

    results = await retrieve_context(query, top_k=top_k)

    if not results:
        return "No matching manual sections found."

    return "\n".join(_fmt_result(r) for r in results)


# ------------------------------------------------------------------
# Tool: refine_search
# ------------------------------------------------------------------

async def refine_search(
    input_data: Dict[str, Any],
) -> str:
    """Adaptive RAG search with ``exclude_doc_ids`` dedup.

    Over-fetches from pgvector, filters out already-seen doc_ids,
    then trims to the requested ``top_k``.

    Args:
        input_data: Must contain ``query`` (str).  Optional
            ``top_k`` (int, default 3) and ``exclude_doc_ids``
            (list of doc_id strings to skip).

    Returns:
        Same format as ``search_manual``.
    """
    query: str = input_data["query"]
    top_k: int = input_data.get("top_k", 3)
    exclude: List[str] = input_data.get(
        "exclude_doc_ids", [],
    )

    # Over-fetch to compensate for excluded docs.
    fetch_k = top_k + len(exclude)
    results = await retrieve_context(query, top_k=fetch_k)

    if exclude:
        exclude_set = set(exclude)
        results = [
            r for r in results
            if r.doc_id not in exclude_set
        ]

    results = results[:top_k]

    if not results:
        return "No matching manual sections found."

    return "\n".join(_fmt_result(r) for r in results)


# ------------------------------------------------------------------
# ToolDefinition exports
# ------------------------------------------------------------------

SEARCH_MANUAL_DEF = ToolDefinition(
    name="search_manual",
    description=(
        "Search vehicle service manuals via RAG (pgvector "
        "cosine similarity). Returns matched sections with "
        "source doc_id, section title, and similarity score."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Search query for manual sections (e.g., "
                    "'P0300 misfire diagnosis procedure')"
                ),
            },
            "top_k": {
                "type": "integer",
                "default": 3,
                "description": (
                    "Number of results to return"
                ),
            },
        },
        "required": ["query"],
    },
    handler=search_manual,
)

REFINE_SEARCH_DEF = ToolDefinition(
    name="refine_search",
    description=(
        "Adaptive RAG search -- use this to search for "
        "additional manual sections based on intermediate "
        "diagnosis findings. Supports excluding "
        "already-retrieved documents."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Refined search query based on current "
                    "investigation findings"
                ),
            },
            "top_k": {
                "type": "integer",
                "default": 3,
                "description": (
                    "Number of results to return"
                ),
            },
            "exclude_doc_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Document IDs to exclude from results "
                    "(already retrieved)"
                ),
            },
        },
        "required": ["query"],
    },
    handler=refine_search,
)
