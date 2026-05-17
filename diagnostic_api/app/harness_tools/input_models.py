"""Pydantic input models for harness tool validation.

Each model defines the expected input for a tool handler.
JSON Schema is auto-generated via ``model_json_schema()``
and used in the OpenAI function-calling ``parameters`` field.

Note: ``_session_id`` is injected by the agent loop before
dispatch — it is NOT part of the tool schema and the LLM
never sees or passes it.
"""

from __future__ import annotations

from typing import List, Literal, Optional, Tuple

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


class ListManualsInput(BaseModel):
    """Input for the list_manuals tool."""

    vehicle_model: Optional[str] = Field(
        default=None,
        description=(
            "Filter by vehicle model (e.g. 'MWS-150-A'). "
            "Omit to list all available manuals."
        ),
    )


class GetManualTocInput(BaseModel):
    """Input for the get_manual_toc tool."""

    manual_id: str = Field(
        ...,
        description=(
            "Manual filename stem "
            "(e.g. 'MWS150A_Service_Manual'). "
            "Use list_manuals to discover available IDs."
        ),
    )
    max_depth: int = Field(
        default=3,
        ge=1,
        le=99,
        description=(
            "Cap on how deep the heading tree goes "
            "(1 = chapters only, 3 = chapters + sections + "
            "subsections, 99 = full tree).  Default 3 keeps "
            "the response small enough to fit in a typical "
            "context budget; pass a higher value to drill in."
        ),
    )


class ReadManualSectionInput(BaseModel):
    """Input for the read_manual_section tool."""

    manual_id: str = Field(
        ...,
        description="Manual filename stem.",
    )
    section: str = Field(
        ...,
        description=(
            "Section heading text or slug "
            "(e.g. '3-2-fuel-system-troubleshooting' or "
            "'Fuel System Troubleshooting'). "
            "Use get_manual_toc to find available sections."
        ),
    )
    include_subsections: bool = Field(
        default=True,
        description=(
            "Include child subsections in the result."
        ),
    )


# ── OBD investigation primitives (HARNESS-19) ────────────────────


class ListSignalsInput(BaseModel):
    """Input for the list_signals tool.

    Discovery primitive — answers 'what signals does this OBD log
    contain?' before any reading.  Cheap.
    """

    pattern: Optional[str] = Field(
        default=None,
        description=(
            "Glob-style filter on signal name (case-insensitive). "
            "Examples: '*TEMP*', 'A_YAM_*', 'RPM'. "
            "Omit to list all signals."
        ),
    )
    subsystem: Literal["engine", "abs", "all"] = Field(
        default="all",
        description=(
            "Filter by ECU subsystem. 'engine' = K-Line engine "
            "ECU (Channel A). 'abs' = CAN ABS ECU (Channel B). "
            "Defaults to 'all'."
        ),
    )


class ReadWindowInput(BaseModel):
    """Input for the read_window tool.

    Targeted sample read for one or more signals in a time window,
    with auto-downsampling.  Medium token cost.
    """

    signals: List[str] = Field(
        ...,
        min_length=1,
        max_length=8,
        description=(
            "Signal/column names to read (e.g. ['RPM', "
            "'COOLANT_TEMP', 'A_YAM_INJ_MS']). 1-8 signals per "
            "call. Use list_signals first to discover available "
            "names."
        ),
    )
    start_time: Optional[str] = Field(
        default=None,
        description=(
            "Start of time window (ISO format, e.g. "
            "'2026-05-08T11:21:30'). Omit to read from session "
            "start."
        ),
    )
    end_time: Optional[str] = Field(
        default=None,
        description=(
            "End of time window (ISO format). Omit to read to "
            "session end."
        ),
    )
    max_rows: int = Field(
        default=50,
        ge=1,
        le=500,
        description=(
            "Max sample rows to return. If the window has more "
            "samples than this, the tool evenly downsamples. "
            "Hard cap 500 (privacy boundary)."
        ),
    )


_STATS_INCLUDE_T = Literal[
    "basic", "percentiles", "trend", "extrema",
]


class GetSignalStatsInput(BaseModel):
    """Input for the get_signal_stats tool.

    Aggregate primitive — summary statistics without raw samples.
    Cheap. Reuses obd_agent.statistics_extractor under the hood.
    """

    signals: List[str] = Field(
        ...,
        min_length=1,
        max_length=10,
        description=(
            "Signal names to summarize (1-10 per call)."
        ),
    )
    time_range: Optional[Tuple[str, str]] = Field(
        default=None,
        description=(
            "Optional (start, end) ISO timestamps. Omit for "
            "full session."
        ),
    )
    include: Optional[List[_STATS_INCLUDE_T]] = Field(
        default=None,
        description=(
            "Which stat groups to include. Choose from "
            "'basic' (min/max/mean/std/count), "
            "'percentiles' (p5/p25/p50/p75/p95), "
            "'trend' (linreg_slope, autocorr_lag1), "
            "'extrema' (timestamps of min and max). "
            "Default: ['basic', 'percentiles']."
        ),
    )


_EVENT_PREDICATE_T = Literal[
    "above_threshold",
    "below_threshold",
    "rising_above",
    "falling_below",
    "rate_of_change_above",
    "rate_of_change_below",
    "missing",
]


class FindEventsInput(BaseModel):
    """Input for the find_events tool.

    Grep-of-time-series. Returns (start, end, peak) tuples for
    windows where the predicate holds.  Cheap.
    """

    signal: str = Field(
        ...,
        description="Signal name to scan.",
    )
    predicate: _EVENT_PREDICATE_T = Field(
        ...,
        description=(
            "Condition to find. "
            "'above_threshold'/'below_threshold' require "
            "threshold parameter. "
            "'rising_above'/'falling_below' require threshold; "
            "match only at crossings (first sample where signal "
            "crosses from one side to the other). "
            "'rate_of_change_above'/'rate_of_change_below' "
            "require threshold and operate on finite differences "
            "(value units per second). "
            "'missing' finds N/A windows; threshold ignored."
        ),
    )
    threshold: Optional[float] = Field(
        default=None,
        description=(
            "Threshold value (required for all predicates "
            "except 'missing')."
        ),
    )
    min_duration_seconds: float = Field(
        default=1.0,
        ge=0.0,
        description=(
            "Drop events shorter than this. Useful for "
            "filtering single-sample spikes."
        ),
    )
    merge_gap_seconds: float = Field(
        default=2.0,
        ge=0.0,
        description=(
            "Merge adjacent events whose gap is below this "
            "duration. 0 disables merging."
        ),
    )
    time_range: Optional[Tuple[str, str]] = Field(
        default=None,
        description=(
            "Optional (start, end) ISO timestamps. Omit for "
            "full session."
        ),
    )
    max_events: int = Field(
        default=20,
        ge=1,
        le=100,
        description=(
            "Max events to return (most recent if more match)."
        ),
    )


class ListDTCsInput(BaseModel):
    """Input for the list_dtcs tool.

    Enumerates fault codes in the session. Surfaces standard
    P/C/B/U codes and Yamaha-proprietary hex codes.  Cheap.
    """

    status: Literal["stored", "pending", "all"] = Field(
        default="all",
        description=(
            "Filter by DTC status. 'stored' = confirmed faults, "
            "'pending' = unconfirmed (not yet two-trip "
            "validated)."
        ),
    )
    ecu: Literal["engine", "abs", "all"] = Field(
        default="all",
        description=(
            "Filter by originating ECU."
        ),
    )


class LookupDTCInput(BaseModel):
    """Input for the lookup_dtc tool.

    Decodes one DTC to description + suspect subsystem + related
    signals.  Falls back to manual-search guidance for codes
    without a decoder.
    """

    code: str = Field(
        ...,
        min_length=2,
        description=(
            "DTC code. Standard format like 'P0117', or "
            "Yamaha proprietary raw hex like "
            "'87F11043000000000000CB'."
        ),
    )


class DelegateToOBDAgentInput(BaseModel):
    """Input for the delegate_to_obd_agent tool.

    Hands an investigation inquiry off to the OBD sub-agent
    (restricted to the 6 OBD primitives), receives back a
    structured finding the main agent can quote.
    """

    inquiry: str = Field(
        ...,
        min_length=10,
        max_length=500,
        description=(
            "The investigation question to pose. Examples: "
            "'Investigate stored DTCs and tell me what's normal "
            "vs. abnormal.', 'What does the charging behaviour "
            "look like across the trip?', 'Are there any "
            "thermal anomalies in the coolant or cylinder head "
            "temperature?'."
        ),
    )


class DelegateToManualAgentInput(BaseModel):
    """Input for the delegate_to_manual_agent tool.

    Hands a manual-lookup inquiry to the manual sub-agent,
    receives back a structured finding with cited sections.
    """

    inquiry: str = Field(
        ...,
        min_length=10,
        max_length=500,
        description=(
            "The lookup question to pose. Examples: "
            "'What is the diagnostic procedure for DTC P0117 "
            "on MWS-150-A?', 'What is the spark plug torque "
            "specification?', 'How do I test the coolant "
            "temperature sensor circuit?'."
        ),
    )
    obd_context: Optional[str] = Field(
        default=None,
        max_length=2000,
        description=(
            "Optional OBD findings context to help the manual "
            "agent disambiguate (e.g. observed DTCs, key signal "
            "anomalies)."
        ),
    )
