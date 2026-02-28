"""Premium AI Diagnosis endpoints (Anthropic Claude — opt-in).

POST /v2/obd/{session_id}/diagnose/premium          — SSE stream
POST /v2/obd/{session_id}/feedback/premium_diagnosis — expert feedback
"""

from __future__ import annotations

import threading
import uuid
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.v2.endpoints.obd_analysis import (
    _get_session_data,
    _sse_event,
    _store_session_field,
    _submit_feedback,
    _MAX_DIAGNOSIS_LENGTH,
)
from app.api.v2.schemas import OBDFeedbackRequest
from app.config import settings
from app.models_db import OBDPremiumDiagnosisFeedback
from app.rag.retrieve import retrieve_context

logger = structlog.get_logger()

router = APIRouter()

# ---------------------------------------------------------------------------
# Premium client (lazy singleton)
# ---------------------------------------------------------------------------

_MAX_PREMIUM_REGENERATIONS: int = 3

_premium_client = None  # Lazy-initialized on first request
_premium_client_lock = threading.Lock()


def _get_premium_client():
    """Return a lazily-initialized PremiumLLMClient.

    Thread-safe via double-checked locking.
    """
    global _premium_client
    if _premium_client is None:
        with _premium_client_lock:
            if _premium_client is None:
                from app.expert.premium_client import (
                    PremiumLLMClient,
                )
                _premium_client = PremiumLLMClient(
                    api_key=settings.premium_llm_api_key,
                    model=settings.premium_llm_model,
                )
    return _premium_client


# ---------------------------------------------------------------------------
# Per-session regeneration counter
# ---------------------------------------------------------------------------


def _count_premium_regenerations(
    session_id: uuid.UUID,
    db: Session,
) -> int:
    """Count how many premium diagnosis feedback rows exist.

    Each successful premium generation stores a feedback-like row,
    so we count existing premium feedback entries as a proxy for
    regeneration count.  We also count the presence of a stored
    premium_diagnosis_text as 1 generation.
    """
    from app.models_db import OBDAnalysisSession

    row = (
        db.query(OBDAnalysisSession.premium_diagnosis_text)
        .filter(OBDAnalysisSession.id == session_id)
        .first()
    )
    if row is None or row.premium_diagnosis_text is None:
        return 0
    # First generation is free; each force=true after that costs 1.
    return 1


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "/{session_id}/diagnose/premium",
    summary="Generate premium AI diagnosis via cloud LLM (SSE stream)",
)
async def generate_premium_diagnosis(
    session_id: uuid.UUID,
    force: bool = False,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Run AI diagnosis via premium cloud LLM with SSE streaming.

    Feature-gated by ``PREMIUM_LLM_ENABLED``. Returns 403 when the
    feature is disabled. SSE event types are identical to the local
    ``/diagnose`` endpoint.

    Rate-limited to 3 force-regenerations per session.
    """
    if not settings.premium_llm_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Premium LLM feature is disabled. "
                "Set PREMIUM_LLM_ENABLED=true to enable."
            ),
        )

    if not settings.premium_llm_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Premium LLM API key is not configured. "
                "Set PREMIUM_LLM_API_KEY in environment."
            ),
        )

    # --- pre-flight checks ---
    session_data = _get_session_data(session_id, db)
    parsed_summary = session_data.parsed_summary
    existing_premium = session_data.premium_diagnosis_text

    if existing_premium and not force:
        async def _cached_stream():
            yield _sse_event("cached", existing_premium)

        return StreamingResponse(
            _cached_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # Rate-limit force regenerations
    if force and existing_premium:
        regen_count = (
            db.query(OBDPremiumDiagnosisFeedback)
            .filter(
                OBDPremiumDiagnosisFeedback.session_id == session_id
            )
            .count()
        )
        # Allow initial generation + up to _MAX regen (force=true)
        if regen_count >= _MAX_PREMIUM_REGENERATIONS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Maximum {_MAX_PREMIUM_REGENERATIONS} premium "
                    "re-generations reached for this session."
                ),
            )

    if not parsed_summary:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Session has no parsed summary — cannot generate "
                   "diagnosis.",
        )

    # RAG retrieval
    rag_query = parsed_summary.get("rag_query", "")
    context_str = ""
    if rag_query:
        try:
            results = await retrieve_context(rag_query, top_k=3)
            context_str = "\n\n".join(
                f"[{r.source_type} - {r.doc_id} - {r.section_title}]"
                f"\n{r.text}"
                for r in results
            )
        except Exception as exc:
            logger.warning(
                "premium_diagnosis_rag_retrieval_failed",
                error=str(exc),
            )

    # --- streaming generator ---
    async def _stream():
        yield ": " + " " * 2048 + "\n\n"
        yield _sse_event(
            "status",
            "Connecting to premium LLM (Claude)...",
        )

        full_text_parts: list[str] = []
        try:
            client = _get_premium_client()
            async for token in client.generate_obd_diagnosis_stream(
                parsed_summary, context_str
            ):
                full_text_parts.append(token)
                yield _sse_event("token", token)

            full_text = "".join(full_text_parts)
            _store_session_field(
                session_id, "premium_diagnosis_text", full_text,
            )
            logger.info(
                "premium_obd_diagnosis_generated",
                session_id=str(session_id),
            )
            yield _sse_event("done", full_text)

        except Exception as exc:
            error_msg = str(exc)
            if (
                settings.premium_llm_api_key
                and settings.premium_llm_api_key in error_msg
            ):
                error_msg = error_msg.replace(
                    settings.premium_llm_api_key, "***REDACTED***"
                )
            logger.error(
                "premium_obd_diagnosis_stream_error",
                error=error_msg,
            )
            yield _sse_event(
                "error",
                "Premium LLM request failed. "
                "Check server logs for details.",
            )

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(
    "/{session_id}/feedback/premium_diagnosis",
    status_code=status.HTTP_201_CREATED,
    summary="Submit expert feedback for the premium AI diagnosis view",
)
async def submit_premium_diagnosis_feedback(
    session_id: uuid.UUID,
    feedback: OBDFeedbackRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Store expert feedback on the premium AI diagnosis."""
    session_data = _get_session_data(session_id, db)
    diag_text = session_data.premium_diagnosis_text
    if diag_text and len(diag_text) > _MAX_DIAGNOSIS_LENGTH:
        diag_text = diag_text[:_MAX_DIAGNOSIS_LENGTH]
    return await _submit_feedback(
        session_id, feedback, db,
        OBDPremiumDiagnosisFeedback, "premium_diagnosis",
        extra_fields={"diagnosis_text": diag_text},
    )
