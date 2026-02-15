"""OBD Analysis endpoints — analyze, retrieve, and provide feedback.

POST /v2/obd/analyze                    — accepts raw TSV body, runs pipeline, caches result
GET  /v2/obd/{session_id}              — cache-first, DB fallback retrieval
POST /v2/obd/{session_id}/diagnose     — generate AI diagnosis (Dify workflow style)
POST /v2/obd/{session_id}/feedback/summary       — expert feedback on summary view
POST /v2/obd/{session_id}/feedback/detailed      — expert feedback on detailed view
POST /v2/obd/{session_id}/feedback/rag           — expert feedback on RAG view
POST /v2/obd/{session_id}/feedback/ai_diagnosis  — expert feedback on AI diagnosis view
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import tempfile
import uuid
from dataclasses import replace
from typing import Literal, NamedTuple, Optional, Type, Union

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.db.session import SessionLocal
from app.api.v2.endpoints.log_summary import _run_pipeline, _MAX_FILE_SIZE
from app.api.v2.schemas import (
    LogSummaryV2,
    OBDAnalysisResponse,
    OBDFeedbackRequest,
)
from app.cache import CachedSession, obd_cache
from app.expert.client import ExpertLLMClient
from app.models_db import (
    OBDAIDiagnosisFeedback,
    OBDDetailedFeedback,
    OBDRAGFeedback,
    OBDSummaryFeedback,
    OBDAnalysisSession,
)
from app.rag.retrieve import retrieve_context
from obd_agent.summary_formatter import format_summary_for_dify

FeedbackModel = Type[Union[OBDSummaryFeedback, OBDDetailedFeedback, OBDRAGFeedback, OBDAIDiagnosisFeedback]]
FeedbackType = Literal["summary", "detailed", "rag", "ai_diagnosis"]

_MAX_FEEDBACK_PER_SESSION: int = 10
_MAX_DIAGNOSIS_LENGTH: int = 50_000
_ALLOWED_EXTRA_FIELDS = frozenset({"diagnosis_text", "retrieved_text"})
_expert_client = ExpertLLMClient()


class SessionData(NamedTuple):
    parsed_summary: Optional[dict]
    diagnosis_text: Optional[str]
    source: str

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

    # --- deduplication: return existing session if same file was already analyzed ---
    # 1. Check in-memory cache
    for cached in obd_cache.values():
        if cached.input_text_hash == input_hash and cached.status == "COMPLETED":
            result = None
            if cached.result_payload:
                result = LogSummaryV2(**cached.result_payload)
            logger.info("obd_analyze_dedup_cache", session_id=cached.session_id, hash=input_hash)
            return OBDAnalysisResponse(
                session_id=cached.session_id,
                status=cached.status,
                result=result,
                parsed_summary=cached.parsed_summary_payload,
                diagnosis_text=cached.diagnosis_text,
            )

    # 2. Check DB
    db_dup = SessionLocal()
    try:
        existing = (
            db_dup.query(OBDAnalysisSession)
            .filter(
                OBDAnalysisSession.input_text_hash == input_hash,
                OBDAnalysisSession.status == "COMPLETED",
            )
            .first()
        )
        if existing:
            result = None
            if existing.result_payload:
                result = LogSummaryV2(**existing.result_payload)
            logger.info("obd_analyze_dedup_db", session_id=str(existing.id), hash=input_hash)
            return OBDAnalysisResponse(
                session_id=str(existing.id),
                status=existing.status,
                result=result,
                parsed_summary=existing.parsed_summary_payload,
                diagnosis_text=existing.diagnosis_text,
            )
    finally:
        db_dup.close()

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
            parsed_summary=cached.parsed_summary_payload,
            diagnosis_text=cached.diagnosis_text,
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
        parsed_summary=db_session.parsed_summary_payload,
        diagnosis_text=db_session.diagnosis_text,
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
        diagnosis_text=cached.diagnosis_text,
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
    extra_fields: Optional[dict] = None,
) -> dict:
    """Insert a feedback row and commit.  The session must already exist in DB."""
    sid = str(session_id)

    if extra_fields:
        invalid = set(extra_fields) - _ALLOWED_EXTRA_FIELDS
        if invalid:
            raise ValueError(f"Unexpected extra_fields: {invalid}")

    db_feedback = model_class(
        id=uuid.uuid4(),
        session_id=session_id,
        rating=feedback.rating,
        is_helpful=feedback.is_helpful,
        comments=feedback.comments,
        **(extra_fields or {}),
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
    extra_fields: Optional[dict] = None,
) -> dict:
    """Promote a cached session to Postgres (if needed) and store feedback.

    Returns 404 if the session is not found in cache or DB.
    Returns 429 if the per-session feedback cap has been reached.
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

    # Guard against unbounded feedback submissions.
    existing_count = (
        db.query(model_class)
        .filter(model_class.session_id == session_id)
        .count()
    )
    if existing_count >= _MAX_FEEDBACK_PER_SESSION:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Maximum feedback submissions reached for this session.",
        )

    return _insert_feedback(session_id, feedback, db, model_class, feedback_type, extra_fields)


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


@router.post(
    "/{session_id}/feedback/rag",
    status_code=status.HTTP_201_CREATED,
    summary="Submit expert feedback for the RAG view",
)
async def submit_rag_feedback(
    session_id: uuid.UUID,
    feedback: OBDFeedbackRequest,
    db: Session = Depends(get_db),
) -> dict:
    # Snapshot the RAG-retrieved text the user was viewing
    session_data = _get_session_data(session_id, db)
    retrieved_text: Optional[str] = None
    if session_data.parsed_summary:
        rag_query = session_data.parsed_summary.get("rag_query", "")
        if rag_query:
            try:
                results = await retrieve_context(rag_query, top_k=5)
                retrieved_text = "\n\n".join(
                    f"[{r.source_type} - {r.doc_id} - {r.section_title}] "
                    f"(score: {r.score:.3f})\n{r.text}"
                    for r in results
                )
            except Exception as exc:
                logger.warning("rag_feedback_retrieval_failed", error=str(exc))
    if retrieved_text and len(retrieved_text) > _MAX_DIAGNOSIS_LENGTH:
        retrieved_text = retrieved_text[:_MAX_DIAGNOSIS_LENGTH]
    return await _submit_feedback(
        session_id, feedback, db, OBDRAGFeedback, "rag",
        extra_fields={"retrieved_text": retrieved_text},
    )


# ---------------------------------------------------------------------------
# AI Diagnosis (Dify workflow replication)
# ---------------------------------------------------------------------------


def _get_session_data(
    session_id: uuid.UUID,
    db: Session,
) -> SessionData:
    """Return (parsed_summary, diagnosis_text, source) from cache or DB."""
    sid = str(session_id)

    cached = obd_cache.get(sid)
    if cached is not None:
        return SessionData(cached.parsed_summary_payload, cached.diagnosis_text, "cache")

    db_session = (
        db.query(OBDAnalysisSession)
        .filter(OBDAnalysisSession.id == session_id)
        .first()
    )
    if db_session is None:
        raise HTTPException(status_code=404, detail="OBD analysis session not found")

    return SessionData(db_session.parsed_summary_payload, db_session.diagnosis_text, "db")


def _store_diagnosis_text(
    session_id: uuid.UUID,
    diagnosis_text: str,
) -> None:
    """Persist diagnosis_text to cache and/or DB.

    Uses its own DB session so it is safe to call from a streaming
    generator (where the request-scoped ``Depends(get_db)`` session
    may already be closed).
    """
    sid = str(session_id)
    text = diagnosis_text[:_MAX_DIAGNOSIS_LENGTH]

    # Update cache (frozen dataclass → replace)
    cached = obd_cache.get(sid)
    if cached is not None:
        updated = replace(cached, diagnosis_text=text)
        obd_cache.put(updated)

    # Update DB if session exists there — use a dedicated session
    db = SessionLocal()
    try:
        db_session = (
            db.query(OBDAnalysisSession)
            .filter(OBDAnalysisSession.id == session_id)
            .first()
        )
        if db_session is not None:
            db_session.diagnosis_text = text
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.post(
    "/{session_id}/diagnose",
    summary="Generate AI diagnosis (SSE stream)",
)
async def generate_diagnosis(
    session_id: uuid.UUID,
    force: bool = False,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Run the Dify-style AI diagnosis workflow with SSE streaming.

    SSE event types:
      - ``token``  : incremental text chunk from the LLM
      - ``done``   : final event; ``data`` contains the full diagnosis text
      - ``error``  : generation failed; ``data`` contains error message
      - ``cached`` : diagnosis was already generated; ``data`` contains full text

    If the diagnosis was previously generated, a single ``cached`` event is
    sent and the stream closes immediately.
    """
    # --- pre-flight checks (run before entering the stream generator) ---
    parsed_summary, existing_diagnosis, source = _get_session_data(session_id, db)

    if existing_diagnosis and not force:
        async def _cached_stream():
            yield _sse_event("cached", existing_diagnosis)

        return StreamingResponse(
            _cached_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    if not parsed_summary:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Session has no parsed summary — cannot generate diagnosis.",
        )

    # RAG retrieval (before stream starts so errors surface as HTTP errors)
    rag_query = parsed_summary.get("rag_query", "")
    context_str = ""
    if rag_query:
        try:
            results = await retrieve_context(rag_query, top_k=3)
            context_str = "\n\n".join(
                f"[{r.source_type} - {r.doc_id} - {r.section_title}]\n{r.text}"
                for r in results
            )
        except Exception as exc:
            logger.warning("diagnosis_rag_retrieval_failed", error=str(exc))

    # --- streaming generator ---
    async def _stream():
        # Send an initial padding comment to force browser buffer flush.
        # Browsers may buffer small fetch ReadableStream chunks; a ~2 KB
        # initial payload ensures the first real SSE events are delivered
        # immediately.
        yield ": " + " " * 2048 + "\n\n"
        yield _sse_event("status", "Retrieving context and initializing LLM...")

        full_text_parts: list[str] = []
        try:
            async for token in _expert_client.generate_obd_diagnosis_stream(
                parsed_summary, context_str
            ):
                full_text_parts.append(token)
                yield _sse_event("token", token)

            full_text = "".join(full_text_parts)
            _store_diagnosis_text(session_id, full_text)
            logger.info("obd_diagnosis_generated", session_id=str(session_id))
            yield _sse_event("done", full_text)

        except Exception as exc:
            logger.error("obd_diagnosis_stream_error", error=str(exc))
            yield _sse_event("error", str(exc))

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse_event(event: str, data: str) -> str:
    """Format a single SSE event frame."""
    escaped = json.dumps(data)
    return f"event: {event}\ndata: {escaped}\n\n"


@router.post(
    "/{session_id}/feedback/ai_diagnosis",
    status_code=status.HTTP_201_CREATED,
    summary="Submit expert feedback for the AI diagnosis view",
)
async def submit_ai_diagnosis_feedback(
    session_id: uuid.UUID,
    feedback: OBDFeedbackRequest,
    db: Session = Depends(get_db),
) -> dict:
    # Snapshot the diagnosis text the user is rating
    session_data = _get_session_data(session_id, db)
    diag_text = session_data.diagnosis_text
    if diag_text and len(diag_text) > _MAX_DIAGNOSIS_LENGTH:
        diag_text = diag_text[:_MAX_DIAGNOSIS_LENGTH]
    return await _submit_feedback(
        session_id, feedback, db, OBDAIDiagnosisFeedback, "ai_diagnosis",
        extra_fields={"diagnosis_text": diag_text},
    )
