"""Premium AI Diagnosis endpoints (OpenRouter cloud LLM — opt-in).

GET  /v2/obd/premium/models                        — list available models
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
    _store_diagnosis,
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
                    base_url=settings.premium_llm_base_url,
                    model=settings.premium_llm_model,
                )
    return _premium_client


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/premium/models",
    summary="List available premium LLM models",
)
async def list_premium_models() -> dict:
    """Return admin-curated list of available premium models.

    Returns:
        Dict with ``models`` (list of model ID strings) and
        ``default`` (the default model ID).

    Raises:
        HTTPException: 403 if premium LLM is disabled.
    """
    if not settings.premium_llm_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Premium LLM feature is disabled.",
        )
    return {
        "models": settings.premium_llm_model_list,
        "default": settings.premium_llm_model,
    }


@router.post(
    "/{session_id}/diagnose/premium",
    summary="Generate premium AI diagnosis via cloud LLM "
            "(SSE stream)",
)
async def generate_premium_diagnosis(
    session_id: uuid.UUID,
    force: bool = False,
    model: Optional[str] = None,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Run AI diagnosis via premium cloud LLM with SSE streaming.

    Feature-gated by ``PREMIUM_LLM_ENABLED``. Returns 403 when the
    feature is disabled. SSE event types are identical to the local
    ``/diagnose`` endpoint.

    Args:
        session_id: OBD analysis session UUID.
        force: Force regeneration even if cached.
        model: OpenRouter model ID override (e.g.
            ``"openai/gpt-4o"``). Uses server default if omitted.
        db: Database session dependency.

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

    effective_model = model or settings.premium_llm_model

    # Validate model against admin-curated list
    allowed = settings.premium_llm_model_list
    if effective_model not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Model '{effective_model}' is not in the "
                f"curated list. Available: {allowed}"
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
                OBDPremiumDiagnosisFeedback.session_id
                == session_id
            )
            .count()
        )
        if regen_count >= _MAX_PREMIUM_REGENERATIONS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Maximum {_MAX_PREMIUM_REGENERATIONS} "
                    "premium re-generations reached for this "
                    "session."
                ),
            )

    if not parsed_summary:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Session has no parsed summary — cannot "
                   "generate diagnosis.",
        )

    # RAG retrieval
    rag_query = parsed_summary.get("rag_query", "")
    context_str = ""
    if rag_query:
        try:
            results = await retrieve_context(rag_query, top_k=3)
            context_str = "\n\n".join(
                f"[{r.source_type} - {r.doc_id} - "
                f"{r.section_title}]\n{r.text}"
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
            f"Connecting to {effective_model}...",
        )

        full_text_parts: list[str] = []
        try:
            client = _get_premium_client()
            async for token in (
                client.generate_obd_diagnosis_stream(
                    parsed_summary,
                    context_str,
                    model_override=effective_model,
                )
            ):
                full_text_parts.append(token)
                yield _sse_event("token", token)

            full_text = "".join(full_text_parts)
            _store_diagnosis(
                session_id, "premium",
                effective_model, full_text,
            )
            logger.info(
                "premium_obd_diagnosis_generated",
                session_id=str(session_id),
                model=effective_model,
            )
            yield _sse_event("done", full_text)

        except Exception as exc:
            error_msg = str(exc)
            if (
                settings.premium_llm_api_key
                and settings.premium_llm_api_key in error_msg
            ):
                error_msg = error_msg.replace(
                    settings.premium_llm_api_key,
                    "***REDACTED***",
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
    summary="Submit expert feedback for the premium AI "
            "diagnosis view",
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
