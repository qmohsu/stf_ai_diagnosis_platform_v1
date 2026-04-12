"""Pydantic input models for harness tool validation.

Each model defines the expected input for a tool handler.
JSON Schema is auto-generated via ``model_json_schema()``
and used in the OpenAI function-calling ``parameters`` field.

Note: ``_session_id`` is injected by the agent loop before
dispatch — it is NOT part of the tool schema and the LLM
never sees or passes it.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class ReadOBDDataInput(BaseModel):
    """Input for the read_obd_data tool."""

    signals: Optional[List[str]] = Field(
        default=None,
        description=(
            "PID names to read (e.g. ['RPM', 'COOLANT_TEMP',"
            " 'SPEED']). Also accepts semantic names like "
            "'engine_rpm'. Omit to get an overview of "
            "available signals."
        ),
    )
    start_time: Optional[str] = Field(
        default=None,
        description=(
            "Start of time window (ISO format, e.g. "
            "'2025-01-15T10:30:00'). Omit to read from "
            "beginning."
        ),
    )
    end_time: Optional[str] = Field(
        default=None,
        description=(
            "End of time window (ISO format). Omit to "
            "read to end."
        ),
    )
    every_nth: Optional[int] = Field(
        default=None,
        description=(
            "Return every Nth row (downsampling). Use for "
            "long time ranges to avoid output overflow."
        ),
    )


class SearchManualInput(BaseModel):
    """Input for the search_manual tool."""

    query: str = Field(
        ...,
        description=(
            "Search query — use DTC codes, symptom "
            "descriptions, or component names "
            "(e.g. 'P0300 misfire', 'fuel pressure low', "
            "'coolant thermostat replacement procedure')."
        ),
    )
    vehicle_model: Optional[str] = Field(
        default=None,
        description=(
            "Filter to a specific vehicle model "
            "(e.g. 'MWS-150-A'). Omit to search all "
            "manuals."
        ),
    )
    top_k: int = Field(
        default=5,
        description="Number of results to return.",
    )
    exclude_chunk_ids: Optional[List[int]] = Field(
        default=None,
        description=(
            "Chunk indices to exclude (for follow-up "
            "searches to get fresh results)."
        ),
    )
