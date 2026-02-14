"""OBD Analysis endpoints — analyze, retrieve, and provide feedback.

POST /v2/obd/analyze     — accepts raw TSV body, runs pipeline, persists session
GET  /v2/obd/{session_id} — retrieves persisted session
POST /v2/obd/{session_id}/feedback — stores expert feedback
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.v2.endpoints.log_summary import _run_pipeline, _MAX_FILE_SIZE
from app.api.v2.schemas import (
    LogSummaryV2,
    OBDAnalysisResponse,
    OBDFeedbackRequest,
)
from app.models_db import OBDAnalysisFeedback, OBDAnalysisSession

logger = structlog.get_logger()

router = APIRouter()


@router.post(
    "/analyze",
    response_model=OBDAnalysisResponse,
    status_code=status.HTTP_200_OK,
    summary="Analyze raw OBD log and persist session",
)
async def analyze_obd_log(
    request: Request,
    db: Session = Depends(get_db),
) -> OBDAnalysisResponse:
    """Accept raw OBD TSV log text, run the full pipeline, persist the
    session, and return session_id + full LogSummaryV2 result.
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

    # Create PENDING session
    db_session = OBDAnalysisSession(
        id=uuid.uuid4(),
        status="PENDING",
        input_text_hash=input_hash,
        input_size_bytes=len(body_bytes),
    )
    db.add(db_session)
    db.commit()
    db.refresh(db_session)

    session_id = str(db_session.id)
    tmp_path: str | None = None

    try:
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=".txt", mode="wb",
        ) as tmp:
            tmp.write(body_bytes)
            tmp_path = tmp.name

        logger.info("obd_analyze_started", session_id=session_id, size=len(body_bytes))

        result: LogSummaryV2 = await asyncio.to_thread(_run_pipeline, tmp_path)

        # Persist COMPLETED
        db_session.status = "COMPLETED"
        db_session.vehicle_id = result.vehicle_id
        db_session.result_payload = result.model_dump(mode="json")
        db.commit()

        logger.info(
            "obd_analyze_completed",
            session_id=session_id,
            vehicle_id=result.vehicle_id,
        )

        return OBDAnalysisResponse(
            session_id=session_id,
            status="COMPLETED",
            result=result,
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
        # Persist FAILED status (best-effort)
        try:
            db_session.status = "FAILED"
            db_session.error_message = str(exc)[:1000]
            db.commit()
        except Exception:
            db.rollback()

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
    summary="Retrieve a persisted OBD analysis session",
)
async def get_obd_session(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> OBDAnalysisResponse:
    """Retrieve a previously analysed OBD session by its ID."""
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
    )


@router.post(
    "/{session_id}/feedback",
    status_code=status.HTTP_201_CREATED,
    summary="Submit expert feedback for an OBD analysis session",
)
async def submit_obd_feedback(
    session_id: uuid.UUID,
    feedback: OBDFeedbackRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Store expert feedback for an OBD analysis session.

    Returns 409 if feedback already exists for the session.
    """
    # Verify session exists
    db_session = (
        db.query(OBDAnalysisSession)
        .filter(OBDAnalysisSession.id == session_id)
        .first()
    )
    if not db_session:
        raise HTTPException(status_code=404, detail="OBD analysis session not found")

    # Check for duplicate
    existing = (
        db.query(OBDAnalysisFeedback)
        .filter(OBDAnalysisFeedback.session_id == session_id)
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Feedback already submitted for this session")

    db_feedback = OBDAnalysisFeedback(
        id=uuid.uuid4(),
        session_id=session_id,
        rating=feedback.rating,
        is_helpful=feedback.is_helpful,
        comments=feedback.comments,
        corrected_diagnosis=feedback.corrected_diagnosis,
    )
    db.add(db_feedback)
    db.commit()

    logger.info(
        "obd_feedback_submitted",
        session_id=str(session_id),
        rating=feedback.rating,
        is_helpful=feedback.is_helpful,
    )

    return {"status": "ok", "feedback_id": str(db_feedback.id)}
