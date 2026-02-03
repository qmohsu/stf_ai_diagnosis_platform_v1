"""POST /v1/tools/summarize-log -- upload a .txt OBD log and get a JSON summary."""

from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import PurePosixPath

import structlog
from fastapi import APIRouter, File, HTTPException, UploadFile, status

from obd_agent.log_summarizer import LogSummary, summarize_log_file

logger = structlog.get_logger()

router = APIRouter()

_MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
_READ_CHUNK = 64 * 1024  # 64 KB


def _safe_filename(raw: str | None) -> str:
    """Sanitise user-supplied filename for logging (truncate, strip control chars)."""
    name = (raw or "")[:255]
    return name.replace("\n", "").replace("\r", "").replace("\x00", "")


# TODO: Add API key or JWT authentication before production deployment.
@router.post(
    "/summarize-log",
    response_model=LogSummary,
    status_code=status.HTTP_200_OK,
    summary="Summarize an OBD log file",
)
async def summarize_log(file: UploadFile = File(...)) -> LogSummary:
    """Accept a ``.txt`` OBD TSV log file and return a compact JSON summary.

    Raises
    ------
    HTTPException 413
        File exceeds 10 MB.
    HTTPException 422
        Bad extension, empty file, or parse failure.
    """
    # --- validate extension and content type --------------------------------
    filename = _safe_filename(file.filename)
    ext = PurePosixPath(filename).suffix.lower()
    if ext != ".txt":
        logger.warning("log_summary_bad_extension", filename=filename)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Only .txt files are accepted, got: {filename!r}",
        )

    content_type = (file.content_type or "").lower()
    if content_type and content_type not in ("text/plain", "application/octet-stream"):
        logger.warning("log_summary_bad_content_type", content_type=content_type)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Expected text/plain content type, got: {content_type!r}",
        )

    # --- read in chunks, enforce size limit before full buffering ----------
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_FILE_SIZE:
            logger.warning("log_summary_file_too_large", size=total)
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="File exceeds 10 MB limit.",
            )
        chunks.append(chunk)
    contents = b"".join(chunks)

    if len(contents) == 0:
        logger.warning("log_summary_empty_file", filename=filename)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Uploaded file is empty.",
        )

    # --- write to temp file and summarise ----------------------------------
    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=".txt",
        ) as tmp:
            tmp.write(contents)
            tmp_path = tmp.name

        logger.info(
            "log_summary_started",
            filename=filename,
            size=len(contents),
        )

        summary: LogSummary = await asyncio.to_thread(
            summarize_log_file, tmp_path,
        )

        logger.info(
            "log_summary_completed",
            filename=filename,
            vehicle_id=summary.vehicle_id,
            sample_count=summary.time_range.sample_count,
        )

        return summary

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "log_summary_parse_error",
            filename=filename,
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
