"""Pydantic response models for the v2 summarisation API.

Mirrors the ``to_dict()`` output of each pipeline stage so that FastAPI can
generate accurate OpenAPI schemas and validate responses automatically.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field

from obd_agent.log_summarizer import PIDStatModel, TimeRange


# ---------------------------------------------------------------------------
# Stage 1: Statistics  (statistics_extractor.SignalStatistics.to_dict())
# ---------------------------------------------------------------------------


class SignalStatsSchema(BaseModel):
    """Per-signal statistical profile (15 fields).

    ``NaN`` / ``Inf`` values from the extractor are serialised as ``None``.
    """

    mean: Optional[float] = None
    std: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None
    p5: Optional[float] = None
    p25: Optional[float] = None
    p50: Optional[float] = None
    p75: Optional[float] = None
    p95: Optional[float] = None
    autocorrelation_lag1: Optional[float] = None
    mean_abs_change: Optional[float] = None
    max_abs_change: Optional[float] = None
    energy: Optional[float] = None
    entropy: Optional[float] = None
    valid_count: int = 0


class ValueStatistics(BaseModel):
    """Aggregated value statistics for all signals."""

    stats: Dict[str, SignalStatsSchema] = Field(default_factory=dict)
    column_units: Dict[str, str] = Field(default_factory=dict)
    resample_interval_seconds: float = 1.0


# ---------------------------------------------------------------------------
# Stage 2: Anomaly detection  (anomaly_detector.AnomalyReport.to_dict())
# ---------------------------------------------------------------------------


class AnomalyEventSchema(BaseModel):
    """A single detected anomaly with temporal context."""

    time_window: List[str]  # [start_iso, end_iso]
    signals: List[str]
    pattern: str
    context: str
    severity: str
    detector: str
    score: float


# ---------------------------------------------------------------------------
# Stage 3: Clue generation  (clue_generator.DiagnosticClueReport.to_dict())
# ---------------------------------------------------------------------------


class DiagnosticClueSchema(BaseModel):
    """A single traceable diagnostic clue."""

    rule_id: str
    category: str
    clue: str
    evidence: List[str]
    severity: str


# ---------------------------------------------------------------------------
# Unified v2 response
# ---------------------------------------------------------------------------


class LogSummaryV2(BaseModel):
    """Unified v2 response combining legacy summary with full pipeline output."""

    # Legacy summariser
    vehicle_id: str
    time_range: TimeRange
    dtc_codes: List[str] = Field(default_factory=list)
    pid_summary: Dict[str, PIDStatModel] = Field(default_factory=dict)

    # Pipeline output
    value_statistics: ValueStatistics
    anomaly_events: List[AnomalyEventSchema] = Field(default_factory=list)
    diagnostic_clues: List[str] = Field(default_factory=list)
    clue_details: List[DiagnosticClueSchema] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Parsed summary for Dify (flat strings)
# ---------------------------------------------------------------------------


class ParsedSummary(BaseModel):
    """Flat-string response consumed by the Dify workflow.

    All fields are ``str`` (not ``bool``) because Dify's if-else node
    compares ``parse_ok is "YES"``.
    """

    parse_ok: str
    vehicle_id: str
    time_range: str
    dtc_codes: str
    pid_summary: str
    anomaly_events: str
    diagnostic_clues: str
    rag_query: str
    debug: str


# ---------------------------------------------------------------------------
# OBD Analysis session & feedback schemas
# ---------------------------------------------------------------------------


class OBDAnalysisResponse(BaseModel):
    """Response returned after submitting an OBD log for analysis."""

    session_id: str
    status: str
    result: Optional[LogSummaryV2] = None
    error_message: Optional[str] = None


class OBDSessionSummary(BaseModel):
    """Lightweight session metadata (for listing / retrieval)."""

    session_id: str
    vehicle_id: Optional[str] = None
    status: str
    input_size_bytes: int
    created_at: str
    updated_at: str


class OBDFeedbackRequest(BaseModel):
    """Expert feedback on an OBD analysis session."""

    rating: int = Field(..., ge=1, le=5)
    is_helpful: bool
    comments: Optional[str] = None
    corrected_diagnosis: Optional[str] = None
