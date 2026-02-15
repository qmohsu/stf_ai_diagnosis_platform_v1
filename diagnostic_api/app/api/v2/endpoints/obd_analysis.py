"""OBD Analysis endpoints — analyze, retrieve, and provide feedback.

POST /v2/obd/analyze                    — accepts raw TSV body, runs pipeline, caches result
GET  /v2/obd/{session_id}              — cache-first, DB fallback retrieval
POST /v2/obd/{session_id}/feedback/summary  — expert feedback on summary view
POST /v2/obd/{session_id}/feedback/detailed — expert feedback on detailed view
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
import uuid
from typing import Literal, Type, Union

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.v2.endpoints.log_summary import _run_pipeline, _MAX_FILE_SIZE
from app.api.v2.schemas import (
    LogSummaryV2,
    OBDAnalysisResponse,
    OBDFeedbackRequest,
)
from app.cache import CachedSession, obd_cache
from app.models_db import OBDDetailedFeedback, OBDSummaryFeedback, OBDAnalysisSession
from obd_agent.summary_formatter import format_summary_for_dify

FeedbackModel = Type[Union[OBDSummaryFeedback, OBDDetailedFeedback]]
FeedbackType = Literal["summary", "detailed"]

logger = structlog.get_logger()

router = APIRouter()


@router.post(
    "/analyze",
    response_model=OBDAnalysisResponse,
    status_code=status.HTTP_200_OK,
    summary="Analyze raw OBD log (stateless — no DB write)",
)
async def analyze_obd_log(
    request: Request,
) -> OBDAnalysisResponse:
    """Accept raw OBD TSV log text, run the full pipeline, cache the
    result in-memory, and return session_id + full LogSummaryV2 result.

    No database row is created — the session is persisted only when
    expert feedback is submitted via the feedback endpoint.
    """
    body_bytes = await request.body()

    if len(body_bytes) == 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Body must not be empty.",
        )

    if len(body_bytes) > _MAX_FILE_SIZE:
        logger.warning("obd_analyze_too_large", size=len(body_bytes))
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Text exceeds 10 MB limit.",
        )

    input_hash = hashlib.sha256(body_bytes).hexdigest()
    raw_text = body_bytes.decode("utf-8", errors="replace")
    session_id = str(uuid.uuid4())
    tmp_path: str | None = None

    try:
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=".txt", mode="wb",
        ) as tmp:
            tmp.write(body_bytes)
            tmp_path = tmp.name

        logger.info("obd_analyze_started", session_id=session_id, size=len(body_bytes))

        result: LogSummaryV2 = await asyncio.to_thread(_run_pipeline, tmp_path)

        result_dict = result.model_dump(mode="json")
        parsed_dict = format_summary_for_dify(result_dict)

        # Store in cache (no DB write)
        cached = CachedSession(
            session_id=session_id,
            status="COMPLETED",
            vehicle_id=result.vehicle_id,
            input_text_hash=input_hash,
            input_size_bytes=len(body_bytes),
            raw_input_text=raw_text,
            result_payload=result_dict,
            parsed_summary_payload=parsed_dict,
            error_message=None,
        )
        obd_cache.put(cached)

        logger.info(
            "obd_analyze_completed",
            session_id=session_id,
            vehicle_id=result.vehicle_id,
        )

        return OBDAnalysisResponse(
            session_id=session_id,
            status="COMPLETED",
            result=result,
            raw_input_text=raw_text,
            parsed_summary=parsed_dict,
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "obd_analyze_error",
            session_id=session_id,
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


@router.get(
    "/{session_id}",
    response_model=OBDAnalysisResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve an OBD analysis session (cache-first, DB fallback)",
)
async def get_obd_session(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> OBDAnalysisResponse:
    """Retrieve an OBD session — checks in-memory cache first, then falls
    back to Postgres for sessions already promoted via feedback.
    """
    sid = str(session_id)

    # 1. Cache hit
    cached = obd_cache.get(sid)
    if cached is not None:
        result = None
        if cached.result_payload:
            result = LogSummaryV2(**cached.result_payload)
        return OBDAnalysisResponse(
            session_id=cached.session_id,
            status=cached.status,
            result=result,
            error_message=cached.error_message,
            raw_input_text=cached.raw_input_text,
            parsed_summary=cached.parsed_summary_payload,
        )

    # 2. DB fallback (post-feedback sessions)
    db_session = (
        db.query(OBDAnalysisSession)
        .filter(OBDAnalysisSession.id == session_id)
        .first()
    )
    if not db_session:
        raise HTTPException(status_code=404, detail="OBD analysis session not found")

    result = None
    if db_session.result_payload:
        result = LogSummaryV2(**db_session.result_payload)

    return OBDAnalysisResponse(
        session_id=str(db_session.id),
        status=db_session.status,
        result=result,
        error_message=db_session.error_message,
        raw_input_text=db_session.raw_input_text,
        parsed_summary=db_session.parsed_summary_payload,
    )


def _ensure_session_in_db(
    session_id: uuid.UUID,
    db: Session,
) -> None:
    """Promote a cached session to Postgres if it hasn't been persisted yet.

    Uses a check-then-insert pattern: if the session already exists in DB
    (from a prior promotion), this is a no-op.  On concurrent promotion
    the IntegrityError is caught and silently ignored.
    """
    sid = str(session_id)
    cached = obd_cache.get(sid)
    if cached is None:
        return  # nothing to promote

    # Already in DB? Skip the INSERT.
    exists = (
        db.query(OBDAnalysisSession.id)
        .filter(OBDAnalysisSession.id == session_id)
        .first()
    )
    if exists is not None:
        obd_cache.pop(sid)
        return

    db_session = OBDAnalysisSession(
        id=session_id,
        status=cached.status,
        vehicle_id=cached.vehicle_id,
        input_text_hash=cached.input_text_hash,
        input_size_bytes=cached.input_size_bytes,
        raw_input_text=cached.raw_input_text,
        result_payload=cached.result_payload,
        parsed_summary_payload=cached.parsed_summary_payload,
        error_message=cached.error_message,
        created_at=cached.created_at,
    )
    db.add(db_session)
    try:
        db.flush()
    except IntegrityError:
        # Concurrent promotion — session already inserted by another request.
        db.rollback()
        logger.info("obd_feedback_concurrent_promotion", session_id=sid)
    else:
        obd_cache.pop(sid)


def _insert_feedback(
    session_id: uuid.UUID,
    feedback: OBDFeedbackRequest,
    db: Session,
    model_class: FeedbackModel,
    feedback_type: FeedbackType,
) -> dict:
    """Insert a feedback row and commit.  The session must already exist in DB."""
    sid = str(session_id)

    db_feedback = model_class(
        id=uuid.uuid4(),
        session_id=session_id,
        rating=feedback.rating,
        is_helpful=feedback.is_helpful,
        comments=feedback.comments,
        corrected_diagnosis=feedback.corrected_diagnosis,
    )
    db.add(db_feedback)
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error(
            "obd_feedback_commit_failed",
            session_id=sid,
            error=str(exc),
            exc_info=True,
        )
        raise

    logger.info(
        "obd_feedback_submitted",
        session_id=sid,
        rating=feedback.rating,
        is_helpful=feedback.is_helpful,
        feedback_type=feedback_type,
    )
    return {"status": "ok", "feedback_id": str(db_feedback.id)}


async def _submit_feedback(
    session_id: uuid.UUID,
    feedback: OBDFeedbackRequest,
    db: Session,
    model_class: FeedbackModel,
    feedback_type: FeedbackType,
) -> dict:
    """Promote a cached session to Postgres (if needed) and store feedback.

    Returns 404 if the session is not found in cache or DB.
    """
    # Ensure session row exists in DB (promotes from cache if necessary).
    _ensure_session_in_db(session_id, db)

    # Verify the session exists before inserting feedback.
    exists = (
        db.query(OBDAnalysisSession.id)
        .filter(OBDAnalysisSession.id == session_id)
        .first()
    )
    if not exists:
        raise HTTPException(status_code=404, detail="OBD analysis session not found")

    return _insert_feedback(session_id, feedback, db, model_class, feedback_type)


@router.post(
    "/{session_id}/feedback/summary",
    status_code=status.HTTP_201_CREATED,
    summary="Submit expert feedback for the summary view",
)
async def submit_summary_feedback(
    session_id: uuid.UUID,
    feedback: OBDFeedbackRequest,
    db: Session = Depends(get_db),
) -> dict:
    return await _submit_feedback(
        session_id, feedback, db, OBDSummaryFeedback, "summary",
    )


@router.post(
    "/{session_id}/feedback/detailed",
    status_code=status.HTTP_201_CREATED,
    summary="Submit expert feedback for the detailed view",
)
async def submit_detailed_feedback(
    session_id: uuid.UUID,
    feedback: OBDFeedbackRequest,
    db: Session = Depends(get_db),
) -> dict:
    return await _submit_feedback(
        session_id, feedback, db, OBDDetailedFeedback, "detailed",
    )
