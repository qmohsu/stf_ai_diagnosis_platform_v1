"""POST /v2/tools/summarize-log-raw -- full diagnostic pipeline endpoint.

Chains the legacy summariser with the 4-stage pipeline (normalise →
statistics → anomaly detection → clue generation) and returns a unified
``LogSummaryV2`` response.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import structlog
from fastapi import APIRouter, HTTPException, Request, status

from app.api.v2.schemas import (
    AnomalyEventSchema,
    DiagnosticClueSchema,
    LogSummaryV2,
    SignalStatsSchema,
    ValueStatistics,
)
from obd_agent.anomaly_detector import detect_anomalies
from obd_agent.clue_generator import generate_clues
from obd_agent.log_summarizer import LogSummary, summarize_log_file
from obd_agent.statistics_extractor import extract_statistics
from obd_agent.time_series_normalizer import normalize_log_file

logger = structlog.get_logger()

router = APIRouter()

_MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


# ---------------------------------------------------------------------------
# Pipeline helper (sync — run via asyncio.to_thread)
# ---------------------------------------------------------------------------


def _run_pipeline(tmp_path: str) -> LogSummaryV2:
    """Legacy summariser + all 4 pipeline stages."""
    # Stage 0: legacy summary (for pid_summary / backward compat)
    summary: LogSummary = summarize_log_file(tmp_path)

    # Stage 1: normalise
    ts = normalize_log_file(tmp_path)

    # Stage 2: statistics
    sig_stats = extract_statistics(ts)

    # Stage 3: anomaly detection
    anomaly_report = detect_anomalies(ts, stats=sig_stats)

    # Stage 4: clue generation
    clue_report = generate_clues(sig_stats, anomaly_report)

    # --- Build value_statistics from sig_stats.to_dict() ---
    stats_dict = sig_stats.to_dict()
    value_stats = ValueStatistics(
        stats={
            name: SignalStatsSchema(**fields)
            for name, fields in stats_dict["stats"].items()
        },
        column_units=stats_dict["column_units"],
        resample_interval_seconds=stats_dict["resample_interval_seconds"],
    )

    # --- Build anomaly events from anomaly_report.to_dict() ---
    report_dict = anomaly_report.to_dict()
    anomaly_events = [
        AnomalyEventSchema(**ev) for ev in report_dict["events"]
    ]

    # --- Build clues from clue_report.to_dict() ---
    clue_dict = clue_report.to_dict()
    diagnostic_clues = clue_dict["diagnostic_clues"]
    clue_details = [
        DiagnosticClueSchema(**cd) for cd in clue_dict["clue_details"]
    ]

    return LogSummaryV2(
        vehicle_id=summary.vehicle_id,
        time_range=summary.time_range,
        dtc_codes=summary.dtc_codes,
        pid_summary=summary.pid_summary,
        value_statistics=value_stats,
        anomaly_events=anomaly_events,
        diagnostic_clues=diagnostic_clues,
        clue_details=clue_details,
    )


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post(
    "/summarize-log-raw",
    response_model=LogSummaryV2,
    status_code=status.HTTP_200_OK,
    summary="Summarize raw OBD log text (v2 full pipeline)",
)
async def summarize_log_raw_v2(request: Request) -> LogSummaryV2:
    """Accept raw OBD TSV log text and return a unified v2 summary.

    Runs the full pipeline: legacy summariser + normalise + statistics +
    anomaly detection + clue generation.
    """
    body_bytes = await request.body()

    if len(body_bytes) == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Body must not be empty.",
        )

    if len(body_bytes) > _MAX_FILE_SIZE:
        logger.warning("v2_log_summary_raw_too_large", size=len(body_bytes))
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Text exceeds 10 MB limit.",
        )

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=".txt", mode="wb",
        ) as tmp:
            tmp.write(body_bytes)
            tmp_path = tmp.name

        logger.info("v2_log_summary_started", size=len(body_bytes))

        result = await asyncio.to_thread(_run_pipeline, tmp_path)

        logger.info(
            "v2_log_summary_completed",
            vehicle_id=result.vehicle_id,
        )

        return result

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "v2_log_summary_parse_error",
            error=str(exc),
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Failed to parse log file. Ensure it is a valid OBD TSV log.",
        ) from exc
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
