"""Agent diagnosis endpoint — SSE streaming for the harness loop.

POST /v2/obd/{session_id}/diagnose/agent

Wires ``run_diagnosis_loop()`` to a ``StreamingResponse`` with
``text/event-stream``, following the same SSE pattern as the V1
``/diagnose`` endpoint but emitting agent-specific event types
(``tool_call``, ``tool_result``, ``context_compact``).
"""

from __future__ import annotations

import json
import uuid
from typing import Any, Dict, Optional, Union

import structlog
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.auth.security import get_current_user
from app.config import settings
from app.db.session import SessionLocal
from app.harness.deps import (
    HarnessConfig,
    HarnessDeps,
    OpenAILLMClient,
)
from app.harness.loop import run_diagnosis_loop
from app.harness.tool_registry import create_default_registry
from app.models_db import (
    DiagnosisHistory,
    OBDAnalysisSession,
    User,
)

logger = structlog.get_logger(__name__)

router = APIRouter()

_MAX_DIAGNOSIS_LENGTH: int = 50_000


# ── Helpers ─────────────────────────────────────────────────────────


def _sse_event(event: str, data: Union[str, dict]) -> str:
    """Format a single SSE event frame.

    Args:
        event: SSE event type (e.g. ``"tool_call"``, ``"done"``).
        data: Payload — a string or a dict.  Both are
            JSON-serialised into the ``data:`` field.

    Returns:
        SSE-formatted string ready to yield.
    """
    escaped = json.dumps(data)
    return f"event: {event}\ndata: {escaped}\n\n"


def _get_owned_session(
    session_id: uuid.UUID,
    user: User,
    db: Session,
) -> OBDAnalysisSession:
    """Fetch session owned by user or raise 404.

    Returns 404 (not 403) to avoid leaking session
    existence to unauthorised users.

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


def _store_diagnosis(
    session_id: uuid.UUID,
    provider: str,
    model_name: str,
    text: str,
) -> Optional[uuid.UUID]:
    """Store diagnosis on the session row and append a history record.

    Updates ``OBDAnalysisSession.diagnosis_text`` and inserts an
    immutable row into ``diagnosis_history`` with the given provider.

    Args:
        session_id: Target session UUID.
        provider: ``"agent"`` (or ``"local"`` / ``"premium"``).
        model_name: Model identifier that produced the text.
        text: Full diagnosis text (truncated to
            ``_MAX_DIAGNOSIS_LENGTH``).

    Returns:
        The UUID of the newly created ``DiagnosisHistory`` row,
        or ``None`` if the session was not found.
    """
    text = text[:_MAX_DIAGNOSIS_LENGTH]

    history_id = uuid.uuid4()
    db = SessionLocal()
    try:
        db_session = (
            db.query(OBDAnalysisSession)
            .filter(OBDAnalysisSession.id == session_id)
            .first()
        )
        if db_session is not None:
            db_session.diagnosis_text = text
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


# ── Endpoint ────────────────────────────────────────────────────────


@router.post(
    "/{session_id}/diagnose/agent",
    summary="Generate agent AI diagnosis (SSE stream)",
)
async def generate_agent_diagnosis(
    session_id: uuid.UUID,
    force: bool = False,
    locale: str = "en",
    max_iterations: Optional[int] = None,
    force_agent: bool = False,
    force_oneshot: bool = False,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """Run the agent diagnosis loop with SSE streaming.

    Implements the ReAct agent cycle: the LLM calls diagnostic
    tools iteratively until it produces a final diagnosis or the
    iteration budget is exhausted.  Each step is streamed as an
    SSE event.

    SSE event types:
      - ``status``        : progress message (string)
      - ``tool_call``     : before tool execution (JSON object)
      - ``tool_result``   : after tool execution (JSON object)
      - ``hypothesis``    : intermediate reasoning (JSON object)
      - ``done``          : final event with diagnosis text,
                            ``diagnosis_history_id``, iteration
                            count, and tools called
      - ``error``         : generation failed (JSON object)
      - ``cached``        : diagnosis already exists (JSON object)

    Args:
        session_id: OBD analysis session UUID.
        force: Force re-diagnosis even if cached.
        locale: Response language (``en``, ``zh-CN``, ``zh-TW``).
        max_iterations: Override default max iterations.
        force_agent: Force agent mode (reserved for HARNESS-06).
        force_oneshot: Force V1 one-shot (reserved for HARNESS-06).
        current_user: Authenticated user from JWT.
        db: Database session.

    Returns:
        ``StreamingResponse`` with ``text/event-stream``.
    """
    # --- Pre-flight checks ---
    db_session = _get_owned_session(
        session_id, current_user, db,
    )
    parsed_summary = db_session.parsed_summary_payload
    existing_diagnosis = db_session.diagnosis_text

    # --- Cache check ---
    if existing_diagnosis and not force:
        latest_hist = (
            db.query(DiagnosisHistory.id)
            .filter(
                DiagnosisHistory.session_id == session_id,
                DiagnosisHistory.provider == "agent",
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
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    if not parsed_summary:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Session has no parsed summary "
                "— cannot generate diagnosis."
            ),
        )

    # --- Build harness dependencies ---
    openai_client = AsyncOpenAI(
        api_key=settings.premium_llm_api_key,
        base_url=settings.premium_llm_base_url,
    )
    llm_client = OpenAILLMClient(openai_client)
    tool_registry = create_default_registry()
    config = HarnessConfig(
        model=settings.premium_llm_model,
        max_iterations=max_iterations or 10,
    )
    deps = HarnessDeps(
        llm_client=llm_client,
        tool_registry=tool_registry,
        config=config,
    )

    # --- Streaming generator ---
    async def _stream() -> None:
        # 2KB padding to flush browser buffers.
        yield ": " + " " * 2048 + "\n\n"

        _init_msgs: Dict[str, str] = {
            "zh-CN": "正在初始化 Agent 诊断...",
            "zh-TW": "正在初始化 Agent 診斷...",
        }
        yield _sse_event(
            "status",
            _init_msgs.get(
                locale,
                "Initializing agent diagnosis...",
            ),
        )

        diagnosis_text = ""
        try:
            async for event in run_diagnosis_loop(
                session_id, parsed_summary, deps,
            ):
                if event.event_type == "done":
                    diagnosis_text = event.payload.get(
                        "diagnosis", "",
                    )
                    history_id = _store_diagnosis(
                        session_id,
                        "agent",
                        config.model,
                        diagnosis_text,
                    )
                    yield _sse_event("done", {
                        "text": diagnosis_text,
                        "diagnosis_history_id": (
                            str(history_id)
                            if history_id else None
                        ),
                        "iterations": event.payload.get(
                            "iterations", 0,
                        ),
                        "tools_called": event.payload.get(
                            "tools_called", [],
                        ),
                        "autonomy_tier": 1,
                    })
                elif event.event_type == "error":
                    yield _sse_event(
                        "error", event.payload,
                    )
                elif event.event_type == "session_start":
                    yield _sse_event(
                        "status", event.payload,
                    )
                elif event.event_type == "context_compact":
                    yield _sse_event(
                        "status", event.payload,
                    )
                else:
                    # tool_call, tool_result, hypothesis
                    yield _sse_event(
                        event.event_type,
                        event.payload,
                    )

        except Exception as exc:
            logger.error(
                "agent_diagnosis_stream_error",
                session_id=str(session_id),
                error=str(exc),
                exc_info=True,
            )
            yield _sse_event("error", {
                "error_type": "stream_error",
                "message": str(exc)[:200],
            })

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
