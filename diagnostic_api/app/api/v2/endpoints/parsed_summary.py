"""POST /v2/tools/parse-summary-raw -- full pipeline + Dify formatting.

Runs the v2 diagnostic pipeline then formats the result into 10 flat-string
fields that the Dify workflow can consume directly, replacing the 130-line
inline code node.
"""

from __future__ import annotations

import asyncio
import os
import tempfile

import structlog
from fastapi import APIRouter, HTTPException, Request, status

from app.api.v2.endpoints.log_summary import _run_pipeline, _MAX_FILE_SIZE
from app.api.v2.schemas import ParsedSummary
from obd_agent.summary_formatter import format_summary_for_dify

logger = structlog.get_logger()

router = APIRouter()


@router.post(
    "/parse-summary-raw",
    response_model=ParsedSummary,
    status_code=status.HTTP_200_OK,
    summary="Parse raw OBD log and return Dify-ready flat strings",
)
async def parse_summary_raw(request: Request) -> ParsedSummary:
    """Accept raw OBD TSV log text, run the full v2 pipeline, and return
    10 flat-string fields ready for the Dify workflow.
    """
    body_bytes = await request.body()

    if len(body_bytes) == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Body must not be empty.",
        )

    if len(body_bytes) > _MAX_FILE_SIZE:
        logger.warning("parsed_summary_raw_too_large", size=len(body_bytes))
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

        logger.info("parsed_summary_started", size=len(body_bytes))

        v2_result = await asyncio.to_thread(_run_pipeline, tmp_path)

        parsed = format_summary_for_dify(v2_result.model_dump())

        logger.info(
            "parsed_summary_completed",
            vehicle_id=parsed.get("vehicle_id", ""),
            parse_ok=parsed.get("parse_ok", ""),
        )

        return ParsedSummary(**parsed)

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "parsed_summary_error",
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
