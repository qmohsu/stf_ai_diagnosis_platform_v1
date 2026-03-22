"""OBD Analysis endpoints — analyze, retrieve, and provide feedback.

GET  /v2/obd/sessions                           — list user sessions
POST /v2/obd/analyze                             — accepts raw TSV body
GET  /v2/obd/{session_id}                       — retrieve session
GET  /v2/obd/{session_id}/history               — diagnosis history
GET  /v2/obd/{session_id}/feedback              — feedback history
POST /v2/obd/{session_id}/diagnose              — local AI diagnosis
POST /v2/obd/{session_id}/feedback/summary      — feedback on summary
POST /v2/obd/{session_id}/feedback/detailed     — feedback on detailed
POST /v2/obd/{session_id}/feedback/rag          — feedback on RAG
POST /v2/obd/{session_id}/feedback/ai_diagnosis — feedback on AI diag
POST /v2/obd/audio/upload                       — upload audio recording
GET  /v2/obd/audio/{feedback_id}                — stream audio playback

Premium endpoints live in ``obd_premium.py``.
"""

from __future__ import annotations

import asyncio
import glob
import hashlib
import json
import os
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, NamedTuple, Optional, Type, Union

import structlog
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.auth.security import get_current_user
from app.db.session import SessionLocal
from app.api.v2.endpoints.log_summary import _run_pipeline, _MAX_FILE_SIZE
from app.api.v2.schemas import (
    DiagnosisHistoryItem,
    DiagnosisHistoryResponse,
    FeedbackHistoryItem,
    FeedbackHistoryResponse,
    LogSummaryV2,
    OBDAnalysisResponse,
    OBDFeedbackRequest,
    OBDSessionSummary,
    SessionListResponse,
)
from app.expert.client import ExpertLLMClient
from app.config import settings
from app.models_db import (
    DiagnosisHistory,
    OBDAIDiagnosisFeedback,
    OBDDetailedFeedback,
    OBDPremiumDiagnosisFeedback,
    OBDRAGFeedback,
    OBDSummaryFeedback,
    OBDAnalysisSession,
    User,
)
from app.rag.retrieve import retrieve_context
from obd_agent.summary_formatter import format_summary_flat_strings

FeedbackModel = Type[Union[
    OBDSummaryFeedback, OBDDetailedFeedback, OBDRAGFeedback,
    OBDAIDiagnosisFeedback, OBDPremiumDiagnosisFeedback,
]]
FeedbackType = Literal[
    "summary", "detailed", "rag", "ai_diagnosis",
    "premium_diagnosis",
]

_MAX_FEEDBACK_PER_SESSION: int = 10
_MAX_DIAGNOSIS_LENGTH: int = 50_000
_ALLOWED_EXTRA_FIELDS = frozenset({
    "diagnosis_text", "retrieved_text", "diagnosis_history_id",
    "audio_file_path", "audio_duration_seconds", "audio_size_bytes",
})

_MIME_TO_EXT: dict[str, str] = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/mp4": "m4a",
    "audio/wav": "wav",
}

# Magic byte signatures for accepted audio container formats.
_AUDIO_SIGNATURES: list[tuple[bytes, int, str]] = [
    # (signature, offset, format)
    (b"\x1a\x45\xdf\xa3", 0, "webm"),   # WebM / Matroska
    (b"OggS", 0, "ogg"),                 # OGG container
    (b"RIFF", 0, "wav"),                 # WAV (RIFF header)
    (b"ftyp", 4, "m4a"),                 # MP4/M4A (ftyp box)
]


def _has_valid_audio_signature(data: bytes) -> bool:
    """Check file magic bytes against known audio signatures.

    Args:
        data: Raw file bytes (at least first 12 bytes).

    Returns:
        True if the file starts with a recognised audio
        container signature.
    """
    for sig, offset, _ in _AUDIO_SIGNATURES:
        end = offset + len(sig)
        if len(data) >= end and data[offset:end] == sig:
            return True
    return False
_expert_client = ExpertLLMClient()


class SessionData(NamedTuple):
    parsed_summary: Optional[dict]
    diagnosis_text: Optional[str]
    premium_diagnosis_text: Optional[str]

logger = structlog.get_logger()

def _build_session_list_query(
    db: Session,
    user_id: uuid.UUID,
    status_filter: Optional[str],
    vehicle_id: Optional[str],
    created_after: Optional[str],
    created_before: Optional[str],
):
    """Build a filtered query for session listing.

    Args:
        db: Database session.
        user_id: Authenticated user UUID.
        status_filter: Optional status filter.
        vehicle_id: Optional vehicle ID filter.
        created_after: ISO timestamp lower bound.
        created_before: ISO timestamp upper bound.

    Returns:
        SQLAlchemy query filtered by the given params.

    Raises:
        HTTPException: 422 if date filters are invalid.
    """
    base_query = db.query(OBDAnalysisSession).filter(
        OBDAnalysisSession.user_id == user_id,
    )

    if status_filter is not None:
        base_query = base_query.filter(
            OBDAnalysisSession.status == status_filter,
        )
    if vehicle_id is not None:
        base_query = base_query.filter(
            OBDAnalysisSession.vehicle_id == vehicle_id,
        )
    if created_after is not None:
        try:
            dt_after = datetime.fromisoformat(
                created_after,
            )
        except ValueError:
            raise HTTPException(
                status_code=(
                    status.HTTP_422_UNPROCESSABLE_ENTITY
                ),
                detail=(
                    "Invalid created_after timestamp. "
                    "Use ISO 8601 format."
                ),
            )
        base_query = base_query.filter(
            OBDAnalysisSession.created_at >= dt_after,
        )
    if created_before is not None:
        try:
            dt_before = datetime.fromisoformat(
                created_before,
            )
        except ValueError:
            raise HTTPException(
                status_code=(
                    status.HTTP_422_UNPROCESSABLE_ENTITY
                ),
                detail=(
                    "Invalid created_before timestamp. "
                    "Use ISO 8601 format."
                ),
            )
        base_query = base_query.filter(
            OBDAnalysisSession.created_at <= dt_before,
        )

    return base_query


router = APIRouter()


# -------------------------------------------------------------------
# Audio upload & playback
# -------------------------------------------------------------------


@router.post(
    "/audio/upload",
    status_code=status.HTTP_201_CREATED,
    summary="Upload audio recording for feedback",
)
async def upload_audio(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Accept an audio file and return a short-lived token.

    The token is included in a subsequent feedback submission
    to link the audio to the feedback row.  Files are stored
    in a staging directory until committed.

    Args:
        file: Audio file (WebM, OGG, MP4, or WAV).
        current_user: Authenticated user from JWT.

    Returns:
        Dict with ``audio_token`` and ``size_bytes``.

    Raises:
        HTTPException: 415 if MIME type not allowed.
        HTTPException: 413 if file exceeds size limit.
    """
    content_type = (file.content_type or "").split(";")[0]
    allowed = settings.audio_allowed_mime_type_list
    if content_type not in allowed:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"Unsupported audio type '{content_type}'. "
                f"Allowed: {', '.join(allowed)}"
            ),
        )

    # Read in chunks to avoid unbounded memory usage.
    max_bytes = settings.audio_max_file_size_bytes
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(65_536)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=(
                    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
                ),
                detail=(
                    f"Audio file too large. "
                    f"Max: {max_bytes} bytes."
                ),
            )
        chunks.append(chunk)
    data = b"".join(chunks)

    # Validate magic bytes to prevent arbitrary file uploads.
    if not _has_valid_audio_signature(data):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                "File content does not match a "
                "recognised audio format."
            ),
        )

    ext = _MIME_TO_EXT.get(content_type, "webm")
    audio_token = str(uuid.uuid4())
    user_prefix = str(current_user.id)
    staging_dir = os.path.join(
        settings.audio_storage_path, "staging",
    )
    staging_path = os.path.join(
        staging_dir, f"{user_prefix}_{audio_token}.{ext}",
    )

    with open(staging_path, "wb") as f:
        f.write(data)

    logger.info(
        "audio_uploaded",
        audio_token=audio_token,
        size_bytes=len(data),
        content_type=content_type,
        user_id=str(current_user.id),
    )

    return {
        "audio_token": audio_token,
        "size_bytes": len(data),
    }


@router.get(
    "/audio/{feedback_id}",
    summary="Stream audio recording for a feedback entry",
)
async def get_audio(
    feedback_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    """Return the audio file attached to a feedback row.

    Searches all five feedback tables to locate the row,
    verifies session ownership, and streams the file.

    Args:
        feedback_id: UUID of the feedback entry.
        current_user: Authenticated user from JWT.
        db: Database session dependency.

    Returns:
        FileResponse with the audio file.

    Raises:
        HTTPException: 404 if feedback or audio not found.
    """
    for model_class, _ in _FEEDBACK_TABLES:
        row = (
            db.query(
                model_class.session_id,
                model_class.audio_file_path,
            )
            .filter(model_class.id == feedback_id)
            .first()
        )
        if row is not None:
            break
    else:
        raise HTTPException(
            status_code=404,
            detail="Feedback entry not found.",
        )

    # Verify session ownership.
    _get_owned_session(row.session_id, current_user, db)

    if not row.audio_file_path:
        raise HTTPException(
            status_code=404,
            detail="No audio attached to this feedback.",
        )

    full_path = os.path.realpath(
        os.path.join(
            settings.audio_storage_path,
            row.audio_file_path,
        ),
    )
    storage_root = os.path.realpath(
        settings.audio_storage_path,
    )
    if not full_path.startswith(storage_root + os.sep):
        raise HTTPException(
            status_code=404,
            detail="Audio file not found on disk.",
        )
    if not os.path.isfile(full_path):
        raise HTTPException(
            status_code=404,
            detail="Audio file not found on disk.",
        )

    ext = os.path.splitext(full_path)[1].lstrip(".")
    ext_to_mime = {v: k for k, v in _MIME_TO_EXT.items()}
    media_type = ext_to_mime.get(
        ext, "application/octet-stream",
    )

    return FileResponse(
        full_path,
        media_type=media_type,
        headers={
            "Content-Disposition": "inline",
        },
    )


@router.get(
    "/sessions",
    response_model=SessionListResponse,
    status_code=status.HTTP_200_OK,
    summary="List current user's OBD analysis sessions",
)
async def list_sessions(
    status_filter: Optional[
        Literal["PENDING", "COMPLETED", "FAILED"]
    ] = Query(
        default=None,
        alias="status",
        description=(
            "Filter by session status: "
            "'PENDING', 'COMPLETED', or 'FAILED'."
        ),
    ),
    vehicle_id: Optional[str] = Query(
        default=None,
        description="Filter by vehicle ID (exact match).",
    ),
    created_after: Optional[str] = Query(
        default=None,
        description=(
            "Filter sessions created on or after "
            "this ISO 8601 timestamp."
        ),
    ),
    created_before: Optional[str] = Query(
        default=None,
        description=(
            "Filter sessions created on or before "
            "this ISO 8601 timestamp."
        ),
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SessionListResponse:
    """List OBD analysis sessions for the current user.

    Returns a paginated list of session metadata sorted by
    created_at descending (newest first).  Supports optional
    filters by status, vehicle_id, and date range.

    Args:
        status_filter: Optional status filter.
        vehicle_id: Optional vehicle ID filter.
        created_after: ISO timestamp lower bound.
        created_before: ISO timestamp upper bound.
        limit: Maximum items per page (1-200).
        offset: Number of items to skip.
        current_user: Authenticated user from JWT.
        db: Database session dependency.

    Returns:
        SessionListResponse with items and total count.
    """
    base_query = _build_session_list_query(
        db, current_user.id,
        status_filter, vehicle_id,
        created_after, created_before,
    )

    total = base_query.count()

    rows = (
        base_query
        .order_by(OBDAnalysisSession.created_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )

    items = [
        OBDSessionSummary(
            session_id=str(row.id),
            vehicle_id=row.vehicle_id,
            status=row.status,
            input_size_bytes=row.input_size_bytes or 0,
            created_at=(
                row.created_at.isoformat()
                if row.created_at
                else ""
            ),
            updated_at=(
                row.updated_at.isoformat()
                if row.updated_at
                else ""
            ),
            has_diagnosis=(
                row.diagnosis_text is not None
            ),
            has_premium_diagnosis=(
                row.premium_diagnosis_text is not None
            ),
        )
        for row in rows
    ]

    logger.info(
        "sessions_listed",
        user_id=str(current_user.id),
        total=total,
        limit=limit,
        offset=offset,
    )

    return SessionListResponse(items=items, total=total)


@router.post(
    "/analyze",
    response_model=OBDAnalysisResponse,
    status_code=status.HTTP_200_OK,
    summary="Analyze raw OBD log and persist to DB",
)
async def analyze_obd_log(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OBDAnalysisResponse:
    """Accept raw OBD TSV log text, run the full pipeline, persist the
    session to Postgres, and return session_id + full LogSummaryV2 result.

    Per-user hash-based deduplication: same user + same file =
    same session; different users get separate sessions.
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

    # --- deduplication: return existing session if same user already analyzed this file ---
    existing = (
        db.query(OBDAnalysisSession)
        .filter(
            OBDAnalysisSession.user_id == current_user.id,
            OBDAnalysisSession.input_text_hash == input_hash,
            OBDAnalysisSession.status == "COMPLETED",
        )
        .first()
    )
    if existing:
        result = None
        if existing.result_payload:
            result = LogSummaryV2(**existing.result_payload)
        logger.info("obd_analyze_dedup", session_id=str(existing.id), hash=input_hash)
        return OBDAnalysisResponse(
            premium_llm_enabled=settings.premium_llm_enabled,
            session_id=str(existing.id),
            status=existing.status,
            result=result,
            parsed_summary=existing.parsed_summary_payload,
            diagnosis_text=existing.diagnosis_text,
            premium_diagnosis_text=existing.premium_diagnosis_text,
        )

    session_id = uuid.uuid4()
    tmp_path: str | None = None
    file_rel_path = f"{session_id}.txt"
    file_abs_path = os.path.join(
        settings.obd_log_storage_path, file_rel_path,
    )

    try:
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=".txt", mode="wb",
        ) as tmp:
            tmp.write(body_bytes)
            tmp_path = tmp.name

        logger.info("obd_analyze_started", session_id=str(session_id), size=len(body_bytes))

        result: LogSummaryV2 = await asyncio.to_thread(_run_pipeline, tmp_path)

        result_dict = result.model_dump(mode="json")
        parsed_dict = format_summary_flat_strings(result_dict)

        # Write raw OBD log to persistent filesystem storage
        os.makedirs(
            os.path.dirname(file_abs_path), exist_ok=True,
        )
        with open(file_abs_path, "wb") as f:
            f.write(body_bytes)

        # Persist to DB immediately
        db_session = OBDAnalysisSession(
            id=session_id,
            user_id=current_user.id,
            status="COMPLETED",
            vehicle_id=result.vehicle_id,
            input_text_hash=input_hash,
            input_size_bytes=len(body_bytes),
            raw_input_file_path=file_rel_path,
            result_payload=result_dict,
            parsed_summary_payload=parsed_dict,
            error_message=None,
        )
        db.add(db_session)
        try:
            db.commit()
        except IntegrityError:
            # Concurrent insert with same user_id + input_text_hash
            db.rollback()
            # Clean up orphaned file
            if os.path.exists(file_abs_path):
                os.unlink(file_abs_path)
            existing = (
                db.query(OBDAnalysisSession)
                .filter(
                    OBDAnalysisSession.user_id == current_user.id,
                    OBDAnalysisSession.input_text_hash == input_hash,
                    OBDAnalysisSession.status == "COMPLETED",
                )
                .first()
            )
            if existing:
                result_obj = None
                if existing.result_payload:
                    result_obj = LogSummaryV2(**existing.result_payload)
                logger.info("obd_analyze_dedup_concurrent", session_id=str(existing.id), hash=input_hash)
                return OBDAnalysisResponse(
                    premium_llm_enabled=settings.premium_llm_enabled,
                    session_id=str(existing.id),
                    status=existing.status,
                    result=result_obj,
                    parsed_summary=existing.parsed_summary_payload,
                    diagnosis_text=existing.diagnosis_text,
                    premium_diagnosis_text=existing.premium_diagnosis_text,
                )
            raise

        logger.info(
            "obd_analyze_completed",
            session_id=str(session_id),
            vehicle_id=result.vehicle_id,
        )

        return OBDAnalysisResponse(
            premium_llm_enabled=settings.premium_llm_enabled,
            session_id=str(session_id),
            status="COMPLETED",
            result=result,
            parsed_summary=parsed_dict,
        )

    except HTTPException:
        raise
    except Exception as exc:
        # Clean up orphaned file on failure
        if os.path.exists(file_abs_path):
            os.unlink(file_abs_path)
        logger.error(
            "obd_analyze_error",
            session_id=str(session_id),
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
    summary="Retrieve an OBD analysis session from DB",
)
async def get_obd_session(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OBDAnalysisResponse:
    """Retrieve an OBD session from Postgres."""
    db_session = _get_owned_session(session_id, current_user, db)

    result = None
    if db_session.result_payload:
        result = LogSummaryV2(**db_session.result_payload)

    # Look up latest diagnosis_history IDs for each provider.
    local_hist = (
        db.query(DiagnosisHistory.id)
        .filter(
            DiagnosisHistory.session_id == session_id,
            DiagnosisHistory.provider == "local",
        )
        .order_by(DiagnosisHistory.created_at.desc())
        .first()
    )
    premium_hist = (
        db.query(DiagnosisHistory.id)
        .filter(
            DiagnosisHistory.session_id == session_id,
            DiagnosisHistory.provider == "premium",
        )
        .order_by(DiagnosisHistory.created_at.desc())
        .first()
    )

    return OBDAnalysisResponse(
        premium_llm_enabled=settings.premium_llm_enabled,
        session_id=str(db_session.id),
        status=db_session.status,
        result=result,
        error_message=db_session.error_message,
        parsed_summary=db_session.parsed_summary_payload,
        diagnosis_text=db_session.diagnosis_text,
        premium_diagnosis_text=db_session.premium_diagnosis_text,
        diagnosis_history_id=(
            str(local_hist.id) if local_hist else None
        ),
        premium_diagnosis_history_id=(
            str(premium_hist.id) if premium_hist else None
        ),
    )


@router.get(
    "/{session_id}/history",
    response_model=DiagnosisHistoryResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve diagnosis history for a session",
)
async def get_diagnosis_history(
    session_id: uuid.UUID,
    provider: Optional[Literal["local", "premium"]] = Query(
        default=None,
        description="Filter by provider: 'local' or 'premium'.",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DiagnosisHistoryResponse:
    """Return diagnosis generations for a session (paginated).

    Results are ordered by created_at descending (newest first).
    Optionally filtered by provider ('local' or 'premium').

    Args:
        session_id: OBD analysis session UUID.
        provider: Optional provider filter ('local' or 'premium').
        limit: Maximum number of items to return (1-200).
        offset: Number of items to skip before returning.
        current_user: Authenticated user from JWT.
        db: Database session dependency.

    Returns:
        DiagnosisHistoryResponse with list of history items
        and total count (across all pages).

    Raises:
        HTTPException: 404 if session not found.
    """
    _get_owned_session(session_id, current_user, db)

    base_query = db.query(DiagnosisHistory).filter(
        DiagnosisHistory.session_id == session_id,
    )
    if provider is not None:
        base_query = base_query.filter(
            DiagnosisHistory.provider == provider,
        )

    total = base_query.count()

    rows = (
        base_query
        .order_by(DiagnosisHistory.created_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )

    items = [
        DiagnosisHistoryItem(
            id=str(row.id),
            session_id=str(row.session_id),
            provider=row.provider,
            model_name=row.model_name,
            diagnosis_text=row.diagnosis_text,
            created_at=(
                row.created_at.isoformat()
                if row.created_at
                else ""
            ),
        )
        for row in rows
    ]

    return DiagnosisHistoryResponse(
        session_id=str(session_id),
        items=items,
        total=total,
    )


_FEEDBACK_TABLES: list[tuple[FeedbackModel, FeedbackType]] = [
    (OBDSummaryFeedback, "summary"),
    (OBDDetailedFeedback, "detailed"),
    (OBDRAGFeedback, "rag"),
    (OBDAIDiagnosisFeedback, "ai_diagnosis"),
    (OBDPremiumDiagnosisFeedback, "premium_diagnosis"),
]

_FEEDBACK_TABLES_WITH_HISTORY: frozenset[FeedbackModel] = frozenset({
    OBDAIDiagnosisFeedback, OBDPremiumDiagnosisFeedback,
})


def _build_feedback_items(
    page_rows: list[
        tuple[FeedbackType, Any, Optional[uuid.UUID]]
    ],
    db: Session,
) -> list[FeedbackHistoryItem]:
    """Batch-fetch diagnosis history metadata and build items.

    For feedback rows linked to a ``DiagnosisHistory`` entry,
    resolves ``model_name`` and ``created_at`` via a single
    ``IN`` query, then constructs ``FeedbackHistoryItem`` objects.

    Args:
        page_rows: List of (tab_name, row, hist_id) tuples.
        db: Database session.

    Returns:
        List of ``FeedbackHistoryItem`` Pydantic objects.
    """
    hist_ids = [
        h for _, _, h in page_rows if h is not None
    ]
    hist_map: dict[uuid.UUID, tuple[str, datetime]] = {}
    if hist_ids:
        hist_rows = (
            db.query(
                DiagnosisHistory.id,
                DiagnosisHistory.model_name,
                DiagnosisHistory.created_at,
            )
            .filter(DiagnosisHistory.id.in_(hist_ids))
            .all()
        )
        hist_map = {
            r.id: (r.model_name, r.created_at)
            for r in hist_rows
        }

    items: list[FeedbackHistoryItem] = []
    for tab_name, row, hist_id in page_rows:
        model_name = None
        diag_created = None
        if hist_id and hist_id in hist_map:
            model_name, diag_created = hist_map[hist_id]
        audio_path = getattr(row, "audio_file_path", None)
        audio_dur = getattr(
            row, "audio_duration_seconds", None,
        )
        items.append(FeedbackHistoryItem(
            id=str(row.id),
            session_id=str(row.session_id),
            tab_name=tab_name,
            rating=row.rating,
            is_helpful=row.is_helpful,
            comments=row.comments,
            created_at=(
                row.created_at.isoformat()
                if row.created_at
                else ""
            ),
            diagnosis_history_id=(
                str(hist_id) if hist_id else None
            ),
            diagnosis_model_name=model_name,
            diagnosis_created_at=(
                diag_created.isoformat()
                if diag_created
                else None
            ),
            has_audio=bool(audio_path),
            audio_duration_seconds=audio_dur,
        ))
    return items


@router.get(
    "/{session_id}/feedback",
    response_model=FeedbackHistoryResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve all feedback for a session",
)
async def get_feedback_history(
    session_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FeedbackHistoryResponse:
    """Return all feedback across all 5 tables for a session.

    Results are ordered by created_at descending (newest first).
    Merges rows from obd_summary_feedback,
    obd_detailed_feedback, obd_rag_feedback,
    obd_ai_diagnosis_feedback, and
    obd_premium_diagnosis_feedback.

    Args:
        session_id: OBD analysis session UUID.
        limit: Maximum number of items to return (1-200).
        offset: Number of items to skip before returning.
        current_user: Authenticated user from JWT.
        db: Database session dependency.

    Returns:
        FeedbackHistoryResponse with list of feedback items
        and total count (across all pages).

    Raises:
        HTTPException: 404 if session not found.
    """
    _get_owned_session(session_id, current_user, db)

    # Max 50 rows total (10 per table x 5 tables) due to
    # _MAX_FEEDBACK_PER_SESSION cap, so in-memory merge
    # is acceptable.
    #
    # Each entry is (tab_name, row, diagnosis_history_id|None).
    all_rows: list[
        tuple[FeedbackType, Any, Optional[uuid.UUID]]
    ] = []
    for model_class, tab_name in _FEEDBACK_TABLES:
        has_history = (
            model_class in _FEEDBACK_TABLES_WITH_HISTORY
        )
        columns = [
            model_class.id,
            model_class.session_id,
            model_class.rating,
            model_class.is_helpful,
            model_class.comments,
            model_class.created_at,
            model_class.audio_file_path,
            model_class.audio_duration_seconds,
        ]
        if has_history:
            columns.append(model_class.diagnosis_history_id)
        rows = (
            db.query(*columns)
            .filter(model_class.session_id == session_id)
            .all()
        )
        for row in rows:
            hist_id = (
                row.diagnosis_history_id if has_history else None
            )
            all_rows.append((tab_name, row, hist_id))

    total = len(all_rows)

    all_rows.sort(
        key=lambda r: (
            r[1].created_at
            or datetime.min.replace(tzinfo=timezone.utc)
        ),
        reverse=True,
    )

    page_rows = all_rows[offset:offset + limit]
    items = _build_feedback_items(page_rows, db)

    return FeedbackHistoryResponse(
        session_id=str(session_id),
        items=items,
        total=total,
    )


def _link_audio_to_feedback(
    audio_token: str,
    audio_duration_seconds: Optional[int],
    session_id: uuid.UUID,
    feedback_id: uuid.UUID,
    db_feedback: Any,
    db: Session,
) -> None:
    """Move staged audio file to permanent storage and update row.

    Args:
        audio_token: UUID token from ``upload_audio``.
        audio_duration_seconds: Duration reported by client.
        session_id: Owning session UUID.
        feedback_id: Feedback row UUID (used as filename).
        db_feedback: SQLAlchemy feedback model instance.
        db: Database session.

    Raises:
        HTTPException: 400 if token references no staged file.
    """
    staging_dir = os.path.join(
        settings.audio_storage_path, "staging",
    )
    matches = glob.glob(
        os.path.join(staging_dir, f"*_{audio_token}.*"),
    )
    if not matches:
        raise HTTPException(
            status_code=400,
            detail="Invalid audio_token — no staged file.",
        )
    staging_path = matches[0]

    # Defence-in-depth: ensure resolved path is inside staging.
    resolved = os.path.realpath(staging_path)
    real_staging = os.path.realpath(staging_dir)
    if not resolved.startswith(real_staging + os.sep):
        raise HTTPException(
            status_code=400,
            detail="Invalid audio_token.",
        )

    ext = os.path.splitext(staging_path)[1]

    # Permanent path: {session_id}/{feedback_id}.{ext}
    session_dir = os.path.join(
        settings.audio_storage_path, str(session_id),
    )
    os.makedirs(session_dir, exist_ok=True)
    relative_path = os.path.join(
        str(session_id), f"{feedback_id}{ext}",
    )
    dest_path = os.path.join(
        settings.audio_storage_path, relative_path,
    )
    shutil.move(staging_path, dest_path)

    size_bytes = os.path.getsize(dest_path)
    db_feedback.audio_file_path = relative_path
    db_feedback.audio_duration_seconds = (
        audio_duration_seconds
    )
    db_feedback.audio_size_bytes = size_bytes
    db.commit()

    logger.info(
        "audio_linked_to_feedback",
        feedback_id=str(feedback_id),
        audio_path=relative_path,
        size_bytes=size_bytes,
    )


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

    feedback_id = uuid.uuid4()
    db_feedback = model_class(
        id=feedback_id,
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

    # Link audio file if an audio_token was provided.
    if feedback.audio_token:
        _link_audio_to_feedback(
            feedback.audio_token,
            feedback.audio_duration_seconds,
            session_id,
            feedback_id,
            db_feedback,
            db,
        )

    logger.info(
        "obd_feedback_submitted",
        session_id=sid,
        rating=feedback.rating,
        is_helpful=feedback.is_helpful,
        feedback_type=feedback_type,
        has_audio=bool(feedback.audio_token),
    )
    return {"status": "ok", "feedback_id": str(feedback_id)}


async def _submit_feedback(
    session_id: uuid.UUID,
    feedback: OBDFeedbackRequest,
    user: User,
    db: Session,
    model_class: FeedbackModel,
    feedback_type: FeedbackType,
    extra_fields: Optional[dict] = None,
) -> dict:
    """Store feedback for an existing DB session.

    Returns 404 if the session is not found in DB or not
    owned by the user.
    Returns 429 if the per-session feedback cap has been reached.
    """
    # Verify the session exists and is owned by the user.
    _get_owned_session(session_id, user, db)

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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return await _submit_feedback(
        session_id, feedback, current_user, db,
        OBDSummaryFeedback, "summary",
    )


@router.post(
    "/{session_id}/feedback/detailed",
    status_code=status.HTTP_201_CREATED,
    summary="Submit expert feedback for the detailed view",
)
async def submit_detailed_feedback(
    session_id: uuid.UUID,
    feedback: OBDFeedbackRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return await _submit_feedback(
        session_id, feedback, current_user, db,
        OBDDetailedFeedback, "detailed",
    )


@router.post(
    "/{session_id}/feedback/rag",
    status_code=status.HTTP_201_CREATED,
    summary="Submit expert feedback for the RAG view",
)
async def submit_rag_feedback(
    session_id: uuid.UUID,
    feedback: OBDFeedbackRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    # Snapshot the RAG-retrieved text the user was viewing
    session_data = _get_session_data(session_id, current_user, db)
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
        session_id, feedback, current_user, db,
        OBDRAGFeedback, "rag",
        extra_fields={"retrieved_text": retrieved_text},
    )


# ---------------------------------------------------------------------------
# AI Diagnosis
# ---------------------------------------------------------------------------


def _get_owned_session(
    session_id: uuid.UUID,
    user: User,
    db: Session,
) -> OBDAnalysisSession:
    """Fetch session owned by user or raise 404.

    Returns 404 (not 403) to avoid leaking session
    existence to unauthorized users.

    Args:
        session_id: Target session UUID.
        user: Authenticated user from JWT.
        db: Database session.

    Returns:
        The OBDAnalysisSession row.

    Raises:
        HTTPException: 404 if session not found or not
            owned by the user.
    """
    db_session = (
        db.query(OBDAnalysisSession)
        .filter(
            OBDAnalysisSession.id == session_id,
            OBDAnalysisSession.user_id == user.id,
        )
        .first()
    )
    if db_session is None:
        raise HTTPException(
            status_code=404,
            detail="OBD analysis session not found",
        )
    return db_session


def _validate_diagnosis_history_id(
    diagnosis_history_id: Optional[str],
    session_id: uuid.UUID,
    expected_provider: str,
    db: Session,
) -> Optional[uuid.UUID]:
    """Validate an optional diagnosis_history_id string.

    Returns the parsed UUID if the input is valid and the
    referenced row belongs to ``session_id`` with the correct
    ``provider``.  Returns ``None`` when the input is ``None``.

    Args:
        diagnosis_history_id: Client-supplied string (may be None).
        session_id: The session the feedback is being submitted for.
        expected_provider: ``"local"`` or ``"premium"``.
        db: Database session.

    Returns:
        Validated UUID or None.

    Raises:
        HTTPException: 400 on format / ownership / provider errors.
    """
    if not diagnosis_history_id:
        return None
    try:
        hist_uuid = uuid.UUID(diagnosis_history_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid diagnosis_history_id format.",
        )
    row = (
        db.query(DiagnosisHistory)
        .filter(DiagnosisHistory.id == hist_uuid)
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=400,
            detail="diagnosis_history_id not found.",
        )
    if row.session_id != session_id:
        raise HTTPException(
            status_code=400,
            detail="diagnosis_history_id does not belong "
                   "to this session.",
        )
    if row.provider != expected_provider:
        raise HTTPException(
            status_code=400,
            detail=(
                f"diagnosis_history_id provider mismatch: "
                f"expected '{expected_provider}', "
                f"got '{row.provider}'."
            ),
        )
    return hist_uuid


def _get_session_data(
    session_id: uuid.UUID,
    user: User,
    db: Session,
) -> SessionData:
    """Return SessionData from DB for the given user.

    Returns:
        SessionData with parsed_summary, diagnosis_text,
        and premium_diagnosis_text.

    Raises:
        HTTPException: 404 if session not found or not
            owned by the user.
    """
    db_session = _get_owned_session(session_id, user, db)

    return SessionData(
        db_session.parsed_summary_payload,
        db_session.diagnosis_text,
        db_session.premium_diagnosis_text,
    )


def _store_diagnosis(
    session_id: uuid.UUID,
    provider: str,
    model_name: str,
    text: str,
) -> Optional[uuid.UUID]:
    """Store diagnosis on the session row and append a history record.

    Updates the latest-diagnosis column on ``OBDAnalysisSession``
    (``diagnosis_text`` for local, ``premium_diagnosis_text`` for
    premium) and inserts an immutable row into ``diagnosis_history``.

    Args:
        session_id: Target session UUID.
        provider: ``"local"`` or ``"premium"``.
        model_name: Model identifier that produced the text
            (e.g. ``"qwen3.5:9b"``, ``"anthropic/claude-sonnet-4.6"``).
        text: Full diagnosis text (truncated to
            ``_MAX_DIAGNOSIS_LENGTH``).

    Returns:
        The UUID of the newly created ``DiagnosisHistory`` row,
        or ``None`` if the session was not found.
    """
    text = text[:_MAX_DIAGNOSIS_LENGTH]
    field = (
        "premium_diagnosis_text"
        if provider == "premium"
        else "diagnosis_text"
    )

    history_id = uuid.uuid4()
    db = SessionLocal()
    try:
        db_session = (
            db.query(OBDAnalysisSession)
            .filter(OBDAnalysisSession.id == session_id)
            .first()
        )
        if db_session is not None:
            setattr(db_session, field, text)
            if provider == "premium":
                db_session.premium_diagnosis_model = model_name
            db.add(DiagnosisHistory(
                id=history_id,
                session_id=session_id,
                provider=provider,
                model_name=model_name,
                diagnosis_text=text,
            ))
            db.commit()
            return history_id
        return None
    except Exception:
        db.rollback()
        logger.error(
            "store_diagnosis_failed",
            session_id=str(session_id),
            provider=provider,
            model_name=model_name,
            exc_info=True,
        )
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
    locale: str = "en",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Run the AI diagnosis workflow with SSE streaming.

    SSE event types:
      - ``token``  : incremental text chunk (string)
      - ``done``   : final event; ``data`` is a JSON object with
                     ``text`` (full diagnosis) and
                     ``diagnosis_history_id`` (UUID string or null)
      - ``error``  : generation failed; ``data`` contains error
                     message (string)
      - ``cached`` : diagnosis was already generated; same JSON
                     format as ``done``

    If the diagnosis was previously generated, a single ``cached``
    event is sent and the stream closes immediately.
    """
    # --- pre-flight checks (run before entering the stream generator) ---
    session_data = _get_session_data(session_id, current_user, db)
    parsed_summary = session_data.parsed_summary
    existing_diagnosis = session_data.diagnosis_text

    if existing_diagnosis and not force:
        # Look up the latest history row for this session.
        latest_hist = (
            db.query(DiagnosisHistory.id)
            .filter(
                DiagnosisHistory.session_id == session_id,
                DiagnosisHistory.provider == "local",
            )
            .order_by(DiagnosisHistory.created_at.desc())
            .first()
        )
        cached_hist_id = (
            str(latest_hist.id) if latest_hist else None
        )

        async def _cached_stream():
            yield _sse_event("cached", {
                "text": existing_diagnosis,
                "diagnosis_history_id": cached_hist_id,
            })

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
                f"{r.source_type} — {r.doc_id} — {r.section_title}\n{r.text}"
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
                parsed_summary, context_str, locale=locale
            ):
                full_text_parts.append(token)
                yield _sse_event("token", token)

            full_text = "".join(full_text_parts)
            history_id = _store_diagnosis(
                session_id, "local",
                settings.llm_model, full_text,
            )
            logger.info("obd_diagnosis_generated", session_id=str(session_id))
            yield _sse_event("done", {
                "text": full_text,
                "diagnosis_history_id": (
                    str(history_id) if history_id else None
                ),
            })

        except Exception as exc:
            logger.error("obd_diagnosis_stream_error", error=str(exc))
            yield _sse_event("error", str(exc))

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _sse_event(event: str, data: Union[str, dict]) -> str:
    """Format a single SSE event frame.

    Args:
        event: SSE event type (e.g. ``"token"``, ``"done"``).
        data: Payload — a string or a dict.  Both are
            JSON-serialised into the ``data:`` field.
    """
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Store expert feedback on the local AI diagnosis."""
    # Ownership check FIRST — prevents information disclosure.
    session_data = _get_session_data(
        session_id, current_user, db,
    )
    # Validate optional link to a specific generation.
    hist_id = _validate_diagnosis_history_id(
        feedback.diagnosis_history_id, session_id, "local", db,
    )
    diag_text = session_data.diagnosis_text
    if diag_text and len(diag_text) > _MAX_DIAGNOSIS_LENGTH:
        diag_text = diag_text[:_MAX_DIAGNOSIS_LENGTH]
    extra: dict[str, Any] = {"diagnosis_text": diag_text}
    if hist_id is not None:
        extra["diagnosis_history_id"] = hist_id
    return await _submit_feedback(
        session_id, feedback, current_user, db,
        OBDAIDiagnosisFeedback, "ai_diagnosis",
        extra_fields=extra,
    )
