"""Diagnosis persistence (PROD-11): the black box, finalisation, sweeping.

Rules this module enforces (round-2 failure modes):

* **One writer per conversation, in order** (FM-19).  While a job runs,
  only its ``EventWriter`` appends events; it commits them in ``seq``
  order in small batches, so an SSE reader that reads "everything after
  my last seq" never skips a row committed late (FM-1: visible within
  about a second).  Closing paths used when no job is alive (cancel of a
  queued run, the sweeper) lock the conversation row and continue from
  the stored maximum — never from the engine's in-memory numbering.
* **The end is one transaction** (FM-3): messages, report, status and the
  ``diagnosis_done`` / ``done`` events commit together, so a client that
  sees ``done`` can always read the report.
* **Failure paths write the minimum** in their own transaction (FM-2) and
  only touch conversations that are still unfinished; every path into a
  final status appends ``done`` (FM-12).
* **Payloads are JSONB-safe** (FM-2): NUL characters removed, NaN / inf
  replaced, anything else made JSON-able.
* Timestamps come from the database (``now()``), never the host (FM-35).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import asyncio
import math
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

import structlog
from pydantic_core import to_jsonable_python
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from stf_v3.diagnosis.agent import events as ev
from stf_v3.diagnosis.models import (
    ACTIVE_STATUSES,
    AuditEvent,
    DiagnosisConversation,
    Message,
    Report,
)
from stf_v3.diagnosis.texts import error_text

# Multi-table flushes below need every FK target registered (see jobs/app.py).
import stf_v3.metadata  # noqa: E402,F401,I001

log = structlog.get_logger(__name__)

SessionFactory = async_sessionmaker
EventItem = Tuple[str, Dict[str, Any]]


class StaleConversation(RuntimeError):
    """The conversation is no longer running (closed by cancel or sweeper)."""


# ── JSON safety ────────────────────────────────────────────────────────


def json_safe(value: Any) -> Any:
    """A JSONB-storable copy of ``value`` (FM-2).

    Strings lose NUL characters (Postgres JSONB rejects ``\\u0000``), NaN
    and infinities become ``None``, dict keys become strings, and any
    other object goes through ``to_jsonable_python`` (dates, UUIDs,
    dataclasses) with ``str`` as the last resort.
    """
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.replace("\x00", "")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k).replace("\x00", ""): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    return json_safe(to_jsonable_python(value, fallback=str))


# ── black box primitives ───────────────────────────────────────────────


async def max_seq(session: AsyncSession, conversation_id: uuid.UUID) -> int:
    """Highest stored ``seq`` of a conversation (0 when none)."""
    value = (await session.execute(
        select(func.coalesce(func.max(AuditEvent.seq), 0)).where(
            AuditEvent.conversation_id == conversation_id)
    )).scalar_one()
    return int(value)


def add_events(
    session: AsyncSession, conversation_id: uuid.UUID, first_seq: int, items: Sequence[EventItem]
) -> int:
    """Stages ``items`` as rows ``first_seq…``; returns the next free seq."""
    seq = first_seq
    for event_type, payload in items:
        if event_type not in ev.EVENT_TYPES:
            raise ValueError(f"unknown event type {event_type!r}")
        session.add(AuditEvent(
            conversation_id=conversation_id, seq=seq, event_type=event_type,
            payload=json_safe(payload),
        ))
        seq += 1
    return seq


async def lock_conversation(
    session: AsyncSession, conversation_id: uuid.UUID
) -> Optional[DiagnosisConversation]:
    """The conversation row, locked FOR UPDATE for the transaction."""
    return (await session.execute(
        select(DiagnosisConversation)
        .where(DiagnosisConversation.id == conversation_id)
        .with_for_update()
    )).scalar_one_or_none()


async def read_events(
    session: AsyncSession, conversation_id: uuid.UUID, after_seq: int, limit: int = 500
) -> List[AuditEvent]:
    """Stored events with ``seq > after_seq``, in order."""
    return list((await session.execute(
        select(AuditEvent)
        .where(AuditEvent.conversation_id == conversation_id, AuditEvent.seq > after_seq)
        .order_by(AuditEvent.seq)
        .limit(limit)
    )).scalars().all())


def event_payload(event: ev.AgentEvent) -> Dict[str, Any]:
    """Stored payload of an engine event: its payload + the delegation id."""
    payload = dict(event.payload)
    if event.parent_tool_call_id:
        payload["parent_tool_call_id"] = event.parent_tool_call_id
    return payload


# ── the job's single writer ────────────────────────────────────────────


_FLUSH = object()


class EventWriter:
    """Ordered, batched appender for one running conversation (FM-1 / FM-19).

    ``put`` never blocks; one background task commits the queue in order,
    at the latest ``flush_s`` after the first unsaved event (or every
    ``batch`` events).  ``close`` flushes the rest and re-raises a write
    failure, so the job never believes events were stored when they were
    not.
    """

    def __init__(
        self, session_factory: SessionFactory, conversation_id: uuid.UUID, first_seq: int,
        *, flush_s: float = 0.5, batch: int = 20,
    ) -> None:
        self._factory = session_factory
        self._cid = conversation_id
        self.next_seq = first_seq
        self._flush_s = flush_s
        self._batch = batch
        self._queue: "asyncio.Queue[Any]" = asyncio.Queue()
        self._task: Optional["asyncio.Task[None]"] = None

    def start(self) -> "EventWriter":
        """Starts the writer task (idempotent)."""
        if self._task is None:
            self._task = asyncio.create_task(self._run())
        return self

    def put(self, event_type: str, payload: Dict[str, Any]) -> None:
        """Queues one event (never blocks)."""
        self._queue.put_nowait((event_type, payload))

    async def close(self) -> int:
        """Flushes everything queued, stops the task; returns the next seq."""
        if self._task is None:
            return self.next_seq
        self._queue.put_nowait(None)
        await self._task
        return self.next_seq

    async def _flush(self, items: List[EventItem]) -> None:
        async with self._factory() as session:
            async with session.begin():
                self.next_seq = add_events(session, self._cid, self.next_seq, items)

    async def _run(self) -> None:
        buf: List[EventItem] = []
        started = 0.0
        closing = False
        while not closing:
            timeout = None
            if buf:
                timeout = max(0.0, self._flush_s - (time.monotonic() - started))
            try:
                item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            except (TimeoutError, asyncio.TimeoutError):
                item = _FLUSH
            if item is None:
                closing = True
            elif item is not _FLUSH:
                if not buf:
                    started = time.monotonic()
                buf.append(item)
            if buf and (item is _FLUSH or closing or len(buf) >= self._batch):
                await self._flush(buf)
                buf = []


# ── final and failure transactions ─────────────────────────────────────


def _message_rows(conversation_id: uuid.UUID, messages: Sequence[Dict[str, Any]]) -> List[Message]:
    rows = []
    for i, msg in enumerate(messages, start=1):
        kind = msg.get("kind")
        if kind not in ("request", "response"):
            raise ValueError(f"message {i} has kind {kind!r}")
        usage = msg.get("usage") if kind == "response" else None
        rows.append(Message(
            conversation_id=conversation_id, seq=i, kind=kind, content=json_safe(msg),
            token_usage=json_safe(usage) if usage else None,
        ))
    return rows


async def finalize(
    session_factory: SessionFactory,
    conversation_id: uuid.UUID,
    *,
    status: str,
    report: Optional[Dict[str, Any]],
    messages: Sequence[Dict[str, Any]],
    tail: Sequence[EventItem],
    model: Optional[str],
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
) -> Optional[uuid.UUID]:
    """The end of a run in ONE transaction (FM-3).

    Messages, the report (when given), the ``tail`` events (the engine's
    held-back ``diagnosis_done`` / ``done``; ``diagnosis_done`` gets the
    report id, or is dropped when there is no report) and the final status
    commit together.

    Raises:
        StaleConversation: The conversation is no longer ``running``.
    """
    async with session_factory() as session:
        async with session.begin():
            conv = await lock_conversation(session, conversation_id)
            if conv is None or conv.status != "running":
                raise StaleConversation(str(conversation_id))
            session.add_all(_message_rows(conversation_id, messages))
            report_id: Optional[uuid.UUID] = None
            if report is not None:
                row = Report(conversation_id=conversation_id, **json_safe_report(report))
                session.add(row)
                await session.flush()
                report_id = row.id
            items: List[EventItem] = []
            for event_type, payload in tail:
                if event_type == ev.DIAGNOSIS_DONE:
                    if report_id is None:
                        continue
                    payload = {**payload, "report_id": str(report_id)}
                items.append((event_type, payload))
            add_events(session, conversation_id, await max_seq(session, conversation_id) + 1, items)
            conv.status = status
            conv.model = (model or "")[:100] or None
            conv.error_code = error_code
            conv.error_message = error_message
            conv.finished_at = func.now()
            conv.updated_at = func.now()
    log.info("diagnosis.finalized", conversation_id=str(conversation_id), status=status,
             report=bool(report_id), messages=len(messages))
    return report_id


def json_safe_report(report: Dict[str, Any]) -> Dict[str, Any]:
    """Report columns with JSON columns made storable."""
    out = dict(report)
    out["citations"] = json_safe(out.get("citations", []))
    out["limitations"] = json_safe(out.get("limitations", []))
    return out


async def close_with_error(
    session_factory: SessionFactory,
    conversation_id: uuid.UUID,
    code: str,
    *,
    locale: str,
    stage: str = "job",
) -> bool:
    """Ends an unfinished conversation as ``error`` (own transaction, FM-2).

    Appends ``error`` (code + sentence, never raw exception text, FM-38)
    and ``done`` after the stored maximum seq.  Returns False when the
    conversation was already finished.
    """
    message = error_text(code, locale)
    async with session_factory() as session:
        async with session.begin():
            conv = await lock_conversation(session, conversation_id)
            if conv is None or conv.status not in ACTIVE_STATUSES:
                return False
            add_events(session, conversation_id, await max_seq(session, conversation_id) + 1, [
                (ev.ERROR, {"stopped_reason": "error", "code": code, "message": message, "stage": stage}),
                (ev.DONE, {"stopped_reason": "error", "code": code, "partial": False}),
            ])
            conv.status = "error"
            conv.error_code = code
            conv.error_message = message
            conv.finished_at = func.now()
            conv.updated_at = func.now()
    log.warning("diagnosis.closed_with_error", conversation_id=str(conversation_id), code=code, stage=stage)
    return True


async def close_cancelled(
    session_factory: SessionFactory, conversation_id: uuid.UUID, *, from_statuses: Sequence[str]
) -> bool:
    """Ends a conversation as ``cancelled`` when its status is in
    ``from_statuses`` (queued, or an orphaned running one) — appends
    ``done``.  Returns False when the status did not match."""
    async with session_factory() as session:
        async with session.begin():
            conv = await lock_conversation(session, conversation_id)
            if conv is None or conv.status not in from_statuses:
                return False
            add_events(session, conversation_id, await max_seq(session, conversation_id) + 1, [
                (ev.DONE, {"stopped_reason": "cancelled", "partial": False}),
            ])
            conv.status = "cancelled"
            conv.cancel_requested = True
            conv.finished_at = func.now()
            conv.updated_at = func.now()
    log.info("diagnosis.cancelled_directly", conversation_id=str(conversation_id))
    return True


# ── sweeping (FM-9 / FM-12 / FM-35) ────────────────────────────────────

JobStatusFn = Callable[[int], Awaitable[Optional[str]]]
_FINISHED_JOB = {"succeeded", "failed", "cancelled", "aborted"}


async def sweep(
    session_factory: SessionFactory, job_status: JobStatusFn, *, stale_s: int, locale: str
) -> Dict[str, int]:
    """Closes unfinished conversations nobody will ever finish.

    * queued with no job id for longer than ``stale_s`` → the enqueue
      never happened (API died between commit and defer, FM-9);
    * a job id whose job is finished or gone while the conversation is
      still unfinished → the job ended without closing it.

    A job that is still ``todo`` / ``doing`` is left alone: whether its
    worker is dead is decided by the stalled-job recovery from worker
    heartbeats, never from elapsed time (FM-35).
    """
    counts = {"no_job": 0, "job_finished": 0}
    async with session_factory() as session:
        rows = (await session.execute(
            select(
                DiagnosisConversation.id, DiagnosisConversation.job_id,
                (DiagnosisConversation.created_at
                 < func.now() - func.make_interval(0, 0, 0, 0, 0, 0, int(stale_s))).label("stale"),
            ).where(DiagnosisConversation.status.in_(ACTIVE_STATUSES))
        )).all()
    for cid, job_id, stale in rows:
        if job_id is None:
            if stale and await close_with_error(session_factory, cid, "queue_unavailable", locale=locale, stage="sweep"):
                counts["no_job"] += 1
            continue
        status = await job_status(int(job_id))
        if status is None or status in _FINISHED_JOB:
            if await close_with_error(session_factory, cid, "diagnosis_interrupted", locale=locale, stage="sweep"):
                counts["job_finished"] += 1
    if any(counts.values()):
        log.warning("diagnosis.sweep", **counts)
    return counts


async def close_for_job(
    session_factory: SessionFactory, job_id: int, code: str, *, locale: str
) -> bool:
    """Closes the unfinished conversation run by ``job_id`` (stalled worker)."""
    async with session_factory() as session:
        cid = (await session.execute(
            select(DiagnosisConversation.id).where(
                DiagnosisConversation.job_id == job_id,
                DiagnosisConversation.status.in_(ACTIVE_STATUSES),
            )
        )).scalar_one_or_none()
    if cid is None:
        return False
    return await close_with_error(session_factory, cid, code, locale=locale, stage="stalled")
