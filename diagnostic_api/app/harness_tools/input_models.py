"""Pydantic input models for harness tool validation.

Each model defines the expected input for a tool handler.
JSON Schema is auto-generated via ``model_json_schema()``
and used in the OpenAI function-calling ``parameters`` field.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class SessionInput(BaseModel):
    """Input for tools that operate on a single session."""

    session_id: str = Field(
        ...,
        description="UUID of the OBD analysis session",
    )


class DetectAnomaliesInput(BaseModel):
    """Input for the detect_anomalies tool."""

    session_id: str = Field(
        ...,
        description="UUID of the OBD analysis session",
    )
    focus_signals: Optional[List[str]] = Field(
        default=None,
        description=(
            "Optional list of signal names to focus "
            "anomaly detection on"
        ),
    )


class SearchManualInput(BaseModel):
    """Input for the search_manual tool."""

    query: str = Field(
        ...,
        description=(
            "Search query for manual sections (e.g., "
            "'P0300 misfire diagnosis procedure')"
        ),
    )
    top_k: int = Field(
        default=3,
        description="Number of results to return",
    )


class RefineSearchInput(BaseModel):
    """Input for the refine_search tool."""

    query: str = Field(
        ...,
        description=(
            "Refined search query based on current "
            "investigation findings"
        ),
    )
    top_k: int = Field(
        default=3,
        description="Number of results to return",
    )
    exclude_doc_ids: List[str] = Field(
        default_factory=list,
        description=(
            "Document IDs to exclude from results "
            "(already retrieved)"
        ),
    )


class SearchCaseHistoryInput(BaseModel):
    """Input for the search_case_history tool."""

    dtc_codes: List[str] = Field(
        ...,
        description=(
            "DTC codes to search for "
            "(e.g., ['P0300', 'P0301'])"
        ),
    )
    vehicle_id: Optional[str] = Field(
        default=None,
        description="Optional vehicle ID to filter by",
    )
    limit: int = Field(
        default=5,
        description=(
            "Maximum number of past cases to return"
        ),
    )
