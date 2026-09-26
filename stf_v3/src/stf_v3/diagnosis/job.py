"""The diagnosis job body (PROD-11 blueprint §6.3, revised by round 2).

``run_conversation`` is what the queue task calls:

1. **Claim** — ``queued → running`` in one conditional UPDATE; anything
   else (cancelled, already running under another worker, closed by the
   sweeper) makes the job exit without touching the conversation (FM-17).
2. **Assemble** — vehicle, log, manuals from the DB; a vehicle / log that
   disappeared while queued ends the run with a clear code (FM-52).  The
   log is parsed in a thread so a large file never blocks the worker's
   event loop and heartbeats (FM-17).
3. **Wait for the model** (D1 / D2) — while the local endpoint does not
   serve the model: ask the host controller to reconcile, append a
   ``waiting`` event every ``diagnosis_wait_event_s`` with the reason,
   honour cancel, give up at ``diagnosis_model_wait_s`` counted from the
   click (FM-22), and fail at once when the controller reports a failed
   start (FM-24).  The run's own wall clock starts only after this (FM-41).
4. **Run the engine** — events go through the ordered ``EventWriter``
   (FM-1 / FM-19); the engine's ``error`` payload is replaced by a stop
   code + sentence (FM-38); ``diagnosis_done`` and ``done`` are held back
   for the final transaction (FM-3).  Cancel is a flag read off the event
   loop by a small poller (FM-49).
5. **Finalize** — one transaction (store.finalize).  Status rule: cancel →
   ``cancelled`` (partial report kept when there is text); any text →
   ``done`` (partial when a gate stopped it); no text → ``error``
   ``run_failed``.

Any unexpected exception ends the conversation as ``error``
``internal_error`` in its own transaction (FM-2); the raw exception goes
only to the log (FM-38).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import asyncio
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import structlog
from sqlalchemy import func, select, update

from stf_v3.diagnosis.agent import events as ev
from stf_v3.diagnosis.agent.bootstrap import DepsNotFound, load_diag_deps
from stf_v3.diagnosis.agent.context import last_assistant_text
from stf_v3.diagnosis.agent.main_agent import DiagnosisOutcome, stream_diagnosis
from stf_v3.diagnosis.agent.memory import messages_to_jsonable
from stf_v3.diagnosis.agent.model import build_model
from stf_v3.diagnosis.model_service import (
    STOPPED_EXTERNALLY,
    get_state,
    model_ready,
    touch_last_used,
    wait_reason,
)
from stf_v3.diagnosis.models import DiagnosisConversation
from stf_v3.diagnosis.store import (
    EventWriter,
    StaleConversation,
    close_cancelled,
    close_with_error,
    event_payload,
    finalize,
    max_seq,
)
from stf_v3.diagnosis.texts import error_text, stop_text, wait_text
from stf_v3.ingest.loader import LogOwnershipError

log = structlog.get_logger(__name__)

ReadyFn = Callable[[Any], Awaitable[bool]]
RequestFn = Callable[[], Awaitable[None]]


class CancelFlag:
    """Set by the poller when the conversation's cancel flag is seen (FM-49)."""

    def __init__(self) -> None:
        self._set = False

    def set(self) -> None:
        self._set = True

    def is_set(self) -> bool:
        return self._set


async def _noop() -> None:
    return None


async def _poll_cancel(session_factory: Any, cid: uuid.UUID, flag: CancelFlag, every_s: float) -> None:
    while not flag.is_set():
        await asyncio.sleep(every_s)
        async with session_factory() as session:
            requested = (await session.execute(
                select(DiagnosisConversation.cancel_requested).where(DiagnosisConversation.id == cid)
            )).scalar_one_or_none()
        if requested:
            flag.set()
            log.info("diagnosis.cancel_seen", conversation_id=str(cid))


async def _db_now(session: Any) -> Any:
    return (await session.execute(select(func.now()))).scalar_one()


async def _wait_for_model(
    session_factory: Any, settings: Any, cid: uuid.UUID, created_at: Any, writer: EventWriter,
    *, ready_fn: ReadyFn, request_reconcile: RequestFn, sleep: Callable[[float], Awaitable[None]],
) -> str:
    """``ready`` / ``cancelled`` / ``timeout`` / ``start_failed`` / ``stopped``."""
    last_event = -1e18
    last_request = -1e18
    while True:
        if await ready_fn(settings):
            return "ready"
        async with session_factory() as session:
            cancel = (await session.execute(
                select(DiagnosisConversation.cancel_requested).where(DiagnosisConversation.id == cid)
            )).scalar_one_or_none()
            state = await get_state(session)
            now = await _db_now(session)
        if cancel:
            return "cancelled"
        waited = (now - created_at).total_seconds()
        if waited >= settings.diagnosis_model_wait_s:
            return "timeout"
        if state is not None and state.state == "failed" and state.failed_at is not None \
                and state.failed_at >= created_at:
            return "stopped" if state.failure_reason == STOPPED_EXTERNALLY else "start_failed"
        mono = time.monotonic()
        if mono - last_request >= settings.diagnosis_wait_event_s:
            try:
                await request_reconcile()
            except Exception as exc:  # noqa: BLE001 — the periodic pass still runs
                log.warning("diagnosis.reconcile_request_failed", error=type(exc).__name__)
            last_request = mono
        if mono - last_event >= settings.diagnosis_wait_event_s:
            reason, fmt = wait_reason(state, now)
            writer.put(ev.WAITING, {
                "reason": reason, "message": wait_text(reason, settings.default_locale, **fmt),
                "waited_s": int(waited), "limit_s": int(settings.diagnosis_model_wait_s),
                "model_state": state.state if state is not None else None, **fmt,
            })
            last_event = mono
        await sleep(settings.diagnosis_wait_poll_s)


def _report_row(report: Any) -> Dict[str, Any]:
    return {
        "content_md": report.content_md,
        "citations": [c.to_dict() for c in report.citations],
        "model": (report.model or "unknown")[:100],
        "total_tokens": report.total_tokens,
        "elapsed_s": round(float(report.elapsed_s or 0.0), 2),
        "partial": bool(report.partial),
        "stopped_reason": report.stopped_reason,
        "limitations": list(report.limitations),
        "requests": report.requests,
        "tool_calls": report.tool_calls,
        "model_source": (report.model_source or "")[:100] or None,
    }


def _produced_text(outcome: DiagnosisOutcome) -> bool:
    """Did the run produce any diagnosis text (vs. only the partial header)?"""
    if outcome.stopped_reason == "complete":
        return bool((outcome.report.content_md or "").strip())
    return bool((last_assistant_text(outcome.messages) or "").strip())


def _public_error_payload(payload: Dict[str, Any], locale: str) -> Dict[str, Any]:
    """The engine's ``error`` event without raw exception text (FM-38)."""
    reason = str(payload.get("stopped_reason") or "error")
    return {
        "stopped_reason": reason, "code": f"stopped_{reason}", "message": stop_text(reason, locale),
        "partial_report_chars": payload.get("partial_report_chars"),
    }


async def run_conversation(
    conversation_id: uuid.UUID,
    job_id: Optional[int],
    *,
    settings: Any = None,
    session_factory: Any = None,
    model_factory: Optional[Callable[[Any], Any]] = None,
    ready_fn: Optional[ReadyFn] = None,
    request_reconcile: Optional[RequestFn] = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> str:
    """Runs one queued conversation to a final status; returns that status.

    Returns ``skipped`` when the conversation could not be claimed.  The
    keyword arguments exist for tests (scripted model, fake readiness).
    """
    from stf_v3.db import SessionLocal
    from stf_v3.settings import settings as _settings

    settings = settings or _settings
    sf = session_factory or SessionLocal
    model_factory = model_factory or (lambda s: build_model(s))
    ready_fn = ready_fn or model_ready
    request_reconcile = request_reconcile or _noop
    locale = settings.default_locale
    cid = conversation_id

    # 1. claim (FM-17)
    async with sf() as session:
        async with session.begin():
            row = (await session.execute(
                update(DiagnosisConversation)
                .where(DiagnosisConversation.id == cid, DiagnosisConversation.status == "queued")
                .values(status="running", started_at=func.now(), updated_at=func.now(),
                        job_id=job_id if job_id is not None else DiagnosisConversation.job_id)
                .returning(DiagnosisConversation.vehicle_id, DiagnosisConversation.obd_log_id,
                           DiagnosisConversation.created_at)
            )).first()
            first_seq = (await max_seq(session, cid)) + 1 if row is not None else 1
    if row is None:
        log.info("diagnosis.claim_skipped", conversation_id=str(cid), job_id=job_id)
        return "skipped"
    vehicle_id, log_id, created_at = row
    log.info("diagnosis.claimed", conversation_id=str(cid), job_id=job_id)

    writer = EventWriter(sf, cid, first_seq, flush_s=settings.diagnosis_event_flush_s).start()
    poller: Optional["asyncio.Task[None]"] = None
    try:
        # 2. assemble (FM-52)
        flag = CancelFlag()
        try:
            async with sf() as session:
                deps = await load_diag_deps(session, vehicle_id, log_id, settings, cancel_check=flag.is_set)
        except DepsNotFound as exc:
            await writer.close()
            code = "vehicle_deleted" if "vehicle" in str(exc) else "log_unavailable"
            await close_with_error(sf, cid, code, locale=locale, stage="assemble")
            return "error"
        except LogOwnershipError:
            await writer.close()
            await close_with_error(sf, cid, "log_unavailable", locale=locale, stage="assemble")
            return "error"
        try:
            await asyncio.to_thread(deps.load_log)     # parse off the event loop (FM-17)
        except Exception as exc:  # noqa: BLE001 — the tools report an unreadable log themselves
            log.warning("diagnosis.log_preload_failed", conversation_id=str(cid), error=type(exc).__name__)

        # 3. wait for the model (D1 / D2)
        if settings.llm_is_local:
            verdict = await _wait_for_model(
                sf, settings, cid, created_at, writer,
                ready_fn=ready_fn, request_reconcile=request_reconcile, sleep=sleep)
            if verdict != "ready":
                await writer.close()
                if verdict == "cancelled":
                    await close_cancelled(sf, cid, from_statuses=("running",))
                    return "cancelled"
                code = {"timeout": "model_unavailable", "stopped": "model_stopped"}.get(verdict, "model_start_failed")
                await close_with_error(sf, cid, code, locale=locale, stage="wait_model")
                return "error"
        await touch_last_used(sf)

        # 4. run the engine
        model = model_factory(settings)
        poller = asyncio.create_task(_poll_cancel(sf, cid, flag, settings.diagnosis_cancel_poll_s))
        stream = stream_diagnosis(deps, model)
        tail: List[Tuple[str, Dict[str, Any]]] = []
        async for event in stream:
            payload = event_payload(event)
            if event.event_type in (ev.DIAGNOSIS_DONE, ev.DONE):
                tail.append((event.event_type, payload))
                continue
            if event.event_type == ev.ERROR:
                payload = _public_error_payload(payload, locale)
            writer.put(event.event_type, payload)
        outcome = stream.outcome
        assert outcome is not None
        poller.cancel()
        await writer.close()

        # 5. finalize (FM-3)
        produced = _produced_text(outcome)
        cancelled = outcome.stopped_reason == "cancelled" or flag.is_set()
        if cancelled:
            status, code = "cancelled", None
        elif produced:
            status, code = "done", None
        else:
            status, code = "error", "run_failed"
        try:
            await finalize(
                sf, cid, status=status,
                report=_report_row(outcome.report) if produced else None,
                messages=messages_to_jsonable(outcome.messages),
                tail=tail, model=outcome.report.model,
                error_code=code, error_message=error_text(code, locale) if code else None,
            )
        except StaleConversation:
            log.warning("diagnosis.finalize_stale", conversation_id=str(cid))
            return "stale"
        await touch_last_used(sf)
        log.info("diagnosis.done", conversation_id=str(cid), status=status,
                 stopped_reason=outcome.stopped_reason, requests=outcome.report.requests)
        return status
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — never leave a conversation running (FM-2)
        log.exception("diagnosis.job_failed", conversation_id=str(cid), error=type(exc).__name__)
        try:
            await writer.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            await close_with_error(sf, cid, "internal_error", locale=locale, stage="job")
        except Exception:  # noqa: BLE001 — the sweeper will close it
            log.exception("diagnosis.close_failed", conversation_id=str(cid))
        return "error"
    finally:
        if poller is not None and not poller.done():
            poller.cancel()
