"""Agent diagnosis endpoint — SSE streaming for the harness loop.

POST /v2/obd/{session_id}/diagnose/agent

Wires ``run_diagnosis_loop()`` to a ``StreamingResponse`` with
``text/event-stream``, following the same SSE pattern as the V1
``/diagnose`` endpoint but emitting agent-specific event types
(``tool_call``, ``tool_result``, ``context_compact``).
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

import structlog
from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from fastapi.responses import StreamingResponse
from openai import AsyncOpenAI
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.v2.endpoints.obd_analysis import (
    _get_session_data,
    _sse_event,
    _store_diagnosis,
)
from app.auth.security import get_current_user
from app.config import settings
from app.expert.client import (
    ExpertLLMClient,
    THINKING_SENTINEL,
)
from app.harness.autonomy import (
    apply_overrides,
    classify_complexity,
)
from app.rag.retrieve import retrieve_context
from app.harness.deps import (
    HarnessConfig,
    HarnessDeps,
    OpenAILLMClient,
)
from app.harness.loop import run_diagnosis_loop
from app.harness.tool_registry import create_default_registry
from app.models_db import (
    DiagnosisHistory,
    User,
)

logger = structlog.get_logger(__name__)

router = APIRouter()

_expert_client = ExpertLLMClient()


# ── V1 one-shot streaming helper ────────────────────────────────────


async def _oneshot_stream(
    session_id: uuid.UUID,
    parsed_summary: Dict[str, Any],
    locale: str,
    tier: int,
    strategy: str,
    skip_padding: bool = False,
) -> None:
    """V1 one-shot diagnosis stream for simple (Tier 0) cases.

    Performs RAG retrieval, then streams tokens from the local
    ``ExpertLLMClient``.  Emits the same SSE event types as the
    V1 ``/diagnose`` endpoint (``status``, ``token``, ``done``,
    ``error``) so the frontend can handle both paths uniformly.

    Args:
        session_id: OBD analysis session UUID.
        parsed_summary: Flat-string summary dict.
        locale: Response language code.
        tier: Autonomy tier (for the ``done`` event).
        strategy: Strategy label (for the ``done`` event).

    Yields:
        SSE event strings.
    """
    # 2KB padding to flush browser buffers.
    if not skip_padding:
        yield ": " + " " * 2048 + "\n\n"

    _init_msgs: Dict[str, str] = {
        "zh-CN": "正在检索上下文并初始化 LLM...",
        "zh-TW": "正在檢索上下文並初始化 LLM...",
    }
    _think_msgs: Dict[str, str] = {
        "zh-CN": "AI 正在深度推理分析中...",
        "zh-TW": "AI 正在深度推理分析中...",
    }
    yield _sse_event(
        "status",
        _init_msgs.get(
            locale,
            "Retrieving context and initializing LLM...",
        ),
    )

    logger.info(
        "oneshot_diagnosis_started",
        session_id=str(session_id),
        model=settings.llm_model,
        locale=locale,
    )

    # RAG retrieval
    rag_query = parsed_summary.get("rag_query", "")
    context_str = ""
    if rag_query:
        try:
            results = await retrieve_context(
                rag_query, top_k=3,
            )
            context_str = "\n\n".join(
                f"{r.source_type} — {r.doc_id}"
                f" — {r.section_title}\n{r.text}"
                for r in results
            )
        except Exception as exc:
            logger.warning(
                "oneshot_rag_retrieval_failed",
                error=str(exc),
            )

    full_text_parts: list[str] = []
    thinking_notified = False
    try:
        gen = _expert_client.generate_obd_diagnosis_stream(
            parsed_summary, context_str, locale=locale,
        )
        async for token in gen:
            if token == THINKING_SENTINEL:
                if not thinking_notified:
                    yield _sse_event(
                        "status",
                        _think_msgs.get(
                            locale,
                            "AI is reasoning...",
                        ),
                    )
                    thinking_notified = True
                yield ": thinking\n\n"
                continue
            full_text_parts.append(token)
            yield _sse_event("token", token)

        full_text = "".join(full_text_parts)
        history_id = _store_diagnosis(
            session_id, "local",
            settings.llm_model, full_text,
        )
        logger.info(
            "oneshot_diagnosis_completed",
            session_id=str(session_id),
            diagnosis_history_id=(
                str(history_id) if history_id else None
            ),
        )
        yield _sse_event("done", {
            "text": full_text,
            "diagnosis_history_id": (
                str(history_id) if history_id else None
            ),
            "autonomy_tier": tier,
            "autonomy_strategy": strategy,
        })

    except Exception as exc:
        logger.error(
            "oneshot_diagnosis_stream_error",
            session_id=str(session_id),
            error=str(exc),
            exc_info=True,
        )
        yield _sse_event("error", {
            "error_type": "stream_error",
            "message": str(exc)[:200],
        })


# ── Endpoint ────────────────────────────────────────────────────────


@router.post(
    "/{session_id}/diagnose/agent",
    summary="Generate agent AI diagnosis (SSE stream)",
)
async def generate_agent_diagnosis(
    session_id: uuid.UUID,
    force: bool = False,
    locale: str = "en",
    max_iterations: Optional[int] = Query(
        default=None, ge=1, le=500,
    ),
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
        force_agent: Force agent mode even for simple cases.
        force_oneshot: Force V1 one-shot regardless of tier.
        current_user: Authenticated user from JWT.
        db: Database session.

    Returns:
        ``StreamingResponse`` with ``text/event-stream``.
    """
    # --- Pre-flight checks ---
    session_data = _get_session_data(
        session_id, current_user, db,
    )
    parsed_summary = session_data.parsed_summary
    existing_diagnosis = session_data.diagnosis_text

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

    # --- Graduated autonomy classification ---
    has_prior = (
        db.query(DiagnosisHistory.id)
        .filter(
            DiagnosisHistory.session_id == session_id,
        )
        .first()
        is not None
    )
    decision = classify_complexity(
        parsed_summary,
        has_prior_diagnosis=has_prior,
    )
    decision = apply_overrides(
        decision,
        force_agent=force_agent,
        force_oneshot=force_oneshot,
    )

    logger.info(
        "autonomy_routed",
        session_id=str(session_id),
        tier=decision.tier,
        strategy=decision.strategy,
        use_agent=decision.use_agent,
    )

    # --- V1 one-shot path (Tier 0 or force_oneshot) ---
    if not decision.use_agent:
        return StreamingResponse(
            _oneshot_stream(
                session_id,
                parsed_summary,
                locale,
                tier=decision.tier,
                strategy=decision.strategy,
            ),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    # --- Build harness dependencies ---
    effective_max_iter = (
        max_iterations
        or decision.suggested_max_iterations
        or 500
    )

    openai_client = AsyncOpenAI(
        api_key=settings.premium_llm_api_key,
        base_url=settings.premium_llm_base_url,
    )
    llm_client = OpenAILLMClient(openai_client)
    tool_registry = create_default_registry()
    config = HarnessConfig(
        model=settings.premium_llm_model,
        max_iterations=effective_max_iter,
        locale=locale,
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

        logger.info(
            "agent_diagnosis_started",
            session_id=str(session_id),
            model=config.model,
            max_iterations=config.max_iterations,
            locale=locale,
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
                    logger.info(
                        "agent_diagnosis_completed",
                        session_id=str(session_id),
                        diagnosis_history_id=(
                            str(history_id)
                            if history_id else None
                        ),
                        iterations=event.payload.get(
                            "iterations", 0,
                        ),
                        tools_called=event.payload.get(
                            "tools_called", [],
                        ),
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
                        "autonomy_tier": decision.tier,
                        "autonomy_strategy": (
                            decision.strategy
                        ),
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

            # --- Fallback to V1 one-shot ---
            logger.warning(
                "agent_fallback_to_oneshot",
                session_id=str(session_id),
                original_error=str(exc)[:200],
            )
            _fb_msgs: Dict[str, str] = {
                "zh-CN": "Agent 诊断失败，"
                         "正在回退到单次诊断...",
                "zh-TW": "Agent 診斷失敗，"
                         "正在回退到單次診斷...",
            }
            yield _sse_event(
                "status",
                _fb_msgs.get(
                    locale,
                    "Agent diagnosis failed. "
                    "Falling back to one-shot "
                    "diagnosis...",
                ),
            )
            async for fb_event in _oneshot_stream(
                session_id,
                parsed_summary,
                locale,
                tier=decision.tier,
                strategy=(
                    f"{decision.strategy} (fallback)"
                ),
                skip_padding=True,
            ):
                yield fb_event

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
