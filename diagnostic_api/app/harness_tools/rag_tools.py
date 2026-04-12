"""RAG manual search tool for the harness agent loop.

Provides semantic search over ingested service manuals stored
in pgvector.  Supports vehicle-model filtering and chunk
exclusion for iterative investigation.
"""

from __future__ import annotations

from typing import Any, Dict, List

import structlog

from app.harness.tool_registry import ToolDefinition
from app.harness_tools.input_models import SearchManualInput
from app.rag.retrieve import retrieve_context

logger = structlog.get_logger(__name__)

_MAX_TEXT_LEN = 500  # Truncate chunk text to this many chars.


# ------------------------------------------------------------------
# Formatter
# ------------------------------------------------------------------


def _fmt_result(r: Any) -> str:
    """Format a single RetrievalResult as one line.

    Args:
        r: A ``RetrievalResult`` object from ``retrieve_context``.

    Returns:
        Formatted line, e.g.
        ``"[0.87] MWS150-A#Fuel System — Check fuel…"``.
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

    Supports vehicle-model filtering and chunk exclusion
    for follow-up searches.

    Args:
        input_data: Must contain ``query`` (str).  Optional
            ``vehicle_model``, ``top_k``, ``exclude_chunk_ids``.

    Returns:
        Formatted text of matched manual sections with source
        metadata and similarity scores, or a clear message if
        no results found.
    """
    query: str = input_data["query"]
    top_k: int = input_data.get("top_k", 5)
    vehicle_model: str | None = input_data.get(
        "vehicle_model",
    )
    exclude_ids: List[int] | None = input_data.get(
        "exclude_chunk_ids",
    )

    results = await retrieve_context(
        query,
        top_k=top_k,
        vehicle_model=vehicle_model,
        exclude_chunk_ids=exclude_ids,
    )

    if not results:
        if vehicle_model:
            return (
                f"No service manual sections found for "
                f"vehicle model '{vehicle_model}' matching "
                f"'{query}'. Try searching without a "
                f"vehicle_model filter, or try different "
                f"keywords (e.g. use DTC codes, symptom "
                f"descriptions, or component names)."
            )
        return (
            f"No manual sections found for '{query}'. "
            f"Try different keywords: use DTC codes "
            f"directly (e.g. 'P0300'), describe the "
            f"symptom (e.g. 'engine misfire at idle'), "
            f"or name the component (e.g. 'fuel injector "
            f"inspection')."
        )

    return "\n".join(_fmt_result(r) for r in results)


# ------------------------------------------------------------------
# ToolDefinition export
# ------------------------------------------------------------------


SEARCH_MANUAL_DEF = ToolDefinition(
    name="search_manual",
    description=(
        "Search vehicle service manuals for diagnostic "
        "procedures, specifications, and repair instructions. "
        "Uses semantic similarity search over ingested PDF "
        "manuals. Returns matched sections with source "
        "document ID, section title, and relevance score. "
        "Use vehicle_model to restrict results to one "
        "vehicle's manual. Use exclude_chunk_ids to get "
        "fresh results in follow-up searches."
    ),
    input_schema=SearchManualInput.model_json_schema(),
    handler=search_manual,
    input_model=SearchManualInput,
    is_read_only=True,
)
