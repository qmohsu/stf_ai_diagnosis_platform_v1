"""Premium AI Diagnosis endpoints (OpenRouter cloud LLM — opt-in).

GET  /v2/obd/premium/models                        — list available models
POST /v2/obd/{session_id}/diagnose/premium          — SSE stream
POST /v2/obd/{session_id}/feedback/premium_diagnosis — expert feedback
"""

from __future__ import annotations

import threading
import uuid
from typing import Optional

import openai
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
    _validate_diagnosis_history_id,
    _MAX_DIAGNOSIS_LENGTH,
)
from app.api.v2.schemas import OBDFeedbackRequest
from app.auth.security import get_current_user
from app.config import settings
from app.expert.model_availability import (
    get_available_models,
    get_blocked_models,
    is_cache_stale,
    mark_model_blocked,
    refresh_availability,
)
from app.models_db import (
    DiagnosisHistory,
    OBDPremiumDiagnosisFeedback,
    User,
)
from app.rag.retrieve import retrieve_context

logger = structlog.get_logger()

router = APIRouter()

# ---------------------------------------------------------------------------
# Premium client (lazy singleton)
# ---------------------------------------------------------------------------

_MAX_PREMIUM_REGENERATIONS: int = 3
_MAX_FALLBACK_ATTEMPTS: int = 3

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


def _redact_api_key(msg: str) -> str:
    """Replace the premium API key in *msg* with a placeholder.

    Args:
        msg: Error message that may contain the API key.

    Returns:
        Sanitised message string.
    """
    key = settings.premium_llm_api_key
    if key and key in msg:
        return msg.replace(key, "***REDACTED***")
    return msg


def _build_fallback_queue(
    selected: str,
    available: list[str],
) -> list[str]:
    """Build an ordered list of models to try.

    If *selected* is in *available*, it goes first.  Otherwise
    the queue starts with the first available model.  Up to
    ``_MAX_FALLBACK_ATTEMPTS`` models are included.  If no
    models are available, *selected* is tried as a last resort
    (the availability cache may be stale).

    Args:
        selected: The user's chosen (or default) model.
        available: Models not currently marked as blocked.

    Returns:
        Ordered list of model IDs to attempt.
    """
    queue: list[str] = []
    if selected in available:
        queue.append(selected)
    for m in available:
        if m != selected and len(queue) < _MAX_FALLBACK_ATTEMPTS:
            queue.append(m)
    # If no models are available, still try the selected model
    # as a last resort (the blocked cache may be stale).
    if not queue:
        queue.append(selected)
    return queue


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/premium/models",
    summary="List available premium LLM models",
)
async def list_premium_models(
    current_user: User = Depends(get_current_user),
) -> dict:
    """Return admin-curated list of models, filtered by availability.

    Models that have returned HTTP 403 (region-blocked) are moved to
    a ``blocked`` list.  On the first call (and when the cache
    expires), a lightweight probe is sent to each model to detect
    regional restrictions.

    Returns:
        Dict with ``models`` (available model IDs), ``default``
        (default model ID), and ``blocked`` (blocked model IDs).

    Raises:
        HTTPException: 403 if premium LLM is disabled.
    """
    if not settings.premium_llm_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Premium LLM feature is disabled.",
        )

    curated = settings.premium_llm_model_list

    # Lazy-probe: refresh availability if cache is stale.
    if (
        is_cache_stale()
        and settings.premium_llm_api_key
    ):
        try:
            await refresh_availability(
                settings.premium_llm_api_key,
                settings.premium_llm_base_url,
                curated,
            )
        except Exception as exc:
            logger.warning(
                "premium_model_probe_failed",
                error=str(exc),
            )

    available = get_available_models(curated)
    blocked = get_blocked_models(curated)

    # Pick a safe default: configured default if available,
    # otherwise the first available model.
    default = settings.premium_llm_model
    if default not in available and available:
        default = available[0]

    return {
        "models": available,
        "default": default,
        "blocked": blocked,
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
    locale: str = "en",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Run AI diagnosis via premium cloud LLM with SSE streaming.

    Feature-gated by ``PREMIUM_LLM_ENABLED``. Returns 403 when the
    feature is disabled.  If the selected model returns 403
    (region-blocked), automatically falls back to the next
    available model and notifies the user via SSE status events.

    SSE event types:

    - ``status`` (string) — progress messages
    - ``token`` (string) — streamed text chunks
    - ``done`` (JSON) — ``text``, ``diagnosis_history_id``,
      ``model_used``
    - ``cached`` (JSON) — same as ``done``
    - ``error`` (JSON) — ``message``, ``error_code``

    Args:
        session_id: OBD analysis session UUID.
        force: Force regeneration even if cached.
        model: OpenRouter model ID override (e.g.
            ``"openai/gpt-5.2"``). Uses server default if omitted.
        locale: Response language (``"en"``, ``"zh-TW"``, etc.).
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
    session_data = _get_session_data(session_id, current_user, db)
    parsed_summary = session_data.parsed_summary
    existing_premium = session_data.premium_diagnosis_text

    if existing_premium and not force:
        latest_hist = (
            db.query(DiagnosisHistory.id)
            .filter(
                DiagnosisHistory.session_id == session_id,
                DiagnosisHistory.provider == "premium",
            )
            .order_by(DiagnosisHistory.created_at.desc())
            .first()
        )
        cached_hist_id = (
            str(latest_hist.id) if latest_hist else None
        )

        async def _cached_stream():
            yield _sse_event("cached", {
                "text": existing_premium,
                "diagnosis_history_id": cached_hist_id,
            })

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
                f"{r.source_type} — {r.doc_id} — "
                f"{r.section_title}\n{r.text}"
                for r in results
            )
        except Exception as exc:
            logger.warning(
                "premium_diagnosis_rag_retrieval_failed",
                error=str(exc),
            )

    # --- streaming generator with fallback ---
    async def _stream():
        yield ": " + " " * 2048 + "\n\n"

        available = get_available_models(
            settings.premium_llm_model_list,
        )
        fallback_queue = _build_fallback_queue(
            effective_model, available,
        )

        for attempt_model in fallback_queue:
            yield _sse_event(
                "status",
                f"Connecting to {attempt_model}...",
            )

            full_text_parts: list[str] = []
            try:
                client = _get_premium_client()
                async for token in (
                    client.generate_obd_diagnosis_stream(
                        parsed_summary,
                        context_str,
                        model_override=attempt_model,
                        locale=locale,
                    )
                ):
                    full_text_parts.append(token)
                    yield _sse_event("token", token)

                # ── Success ──
                full_text = "".join(full_text_parts)
                history_id = _store_diagnosis(
                    session_id, "premium",
                    attempt_model, full_text,
                )
                logger.info(
                    "premium_obd_diagnosis_generated",
                    session_id=str(session_id),
                    model=attempt_model,
                )
                if attempt_model != effective_model:
                    yield _sse_event(
                        "status",
                        f"Model {effective_model} is "
                        f"unavailable in your region. "
                        f"Used {attempt_model} instead.",
                    )
                yield _sse_event("done", {
                    "text": full_text,
                    "diagnosis_history_id": (
                        str(history_id)
                        if history_id
                        else None
                    ),
                    "model_used": attempt_model,
                })
                return  # success — exit generator

            except openai.PermissionDeniedError:
                mark_model_blocked(attempt_model)
                logger.warning(
                    "premium_model_region_blocked_fallback",
                    model=attempt_model,
                    session_id=str(session_id),
                )
                yield _sse_event(
                    "status",
                    f"Model {attempt_model} is not "
                    f"available in your region. "
                    f"Trying next model...",
                )
                continue  # try next model

            except Exception as exc:
                error_msg = _redact_api_key(str(exc))
                logger.error(
                    "premium_obd_diagnosis_stream_error",
                    error=error_msg,
                    model=attempt_model,
                )
                yield _sse_event("error", {
                    "message": (
                        "Premium LLM request failed. "
                        "Check server logs for details."
                    ),
                    "error_code": "stream_error",
                })
                return

        # ── All models exhausted ──
        logger.error(
            "premium_all_models_region_blocked",
            session_id=str(session_id),
            attempted=fallback_queue,
        )
        yield _sse_event("error", {
            "message": (
                "All available models are blocked in your "
                "region. Please contact the administrator "
                "to update the model configuration."
            ),
            "error_code": "all_models_blocked",
        })

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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """Store expert feedback on the premium AI diagnosis."""
    # Ownership check FIRST — prevents information disclosure.
    session_data = _get_session_data(
        session_id, current_user, db,
    )
    # Validate optional link to a specific generation.
    hist_id = _validate_diagnosis_history_id(
        feedback.diagnosis_history_id,
        session_id, "premium", db,
    )
    diag_text = session_data.premium_diagnosis_text
    if diag_text and len(diag_text) > _MAX_DIAGNOSIS_LENGTH:
        diag_text = diag_text[:_MAX_DIAGNOSIS_LENGTH]
    extra: dict = {"diagnosis_text": diag_text}
    if hist_id is not None:
        extra["diagnosis_history_id"] = hist_id
    return await _submit_feedback(
        session_id, feedback, current_user, db,
        OBDPremiumDiagnosisFeedback, "premium_diagnosis",
        extra_fields=extra,
    )
