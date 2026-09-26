"""Diagnosis service layer (PROD-11): start, read, cancel.

Authorisation always goes through the vehicle (``can_access_vehicle``):
a conversation, its events, report and cancel are visible exactly to the
members of the vehicle's workshop, and everyone else gets the same 404 as
for a missing id — existence is never leaked (FM-5).  A diagnosis may
only use a log of THIS vehicle (FM-6).

Starting (D4 / FM-9 / FM-15): an unfinished diagnosis of the same vehicle
is returned instead of creating a second one (the partial unique index is
the backstop for concurrent clicks); the conversation + its first
``waiting`` (queued) event are committed first, then the job is deferred
(the queue library uses its own connection, so the two cannot share a
transaction); a failed defer closes the conversation as ``error``
``queue_unavailable`` and answers 503.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import uuid
from typing import List, Optional, Tuple

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from stf_v3.auth.models import User
from stf_v3.db import SessionLocal
from stf_v3.diagnosis import store, tasks
from stf_v3.diagnosis.agent import events as ev
from stf_v3.diagnosis.models import (
    ACTIVE_STATUSES,
    FINAL_STATUSES,
    DiagnosisConversation,
    Message,
    Report,
)
from stf_v3.diagnosis.schemas import (
    CancelOut,
    ConversationDetailOut,
    ConversationOut,
    MessageOut,
    ReportOut,
)
from stf_v3.diagnosis.texts import wait_text
from stf_v3.errors import ApiError, not_found
from stf_v3.ingest.models import ObdLog
from stf_v3.settings import settings
from stf_v3.vehicles.service import can_access_vehicle

log = structlog.get_logger(__name__)


async def authorised_conversation(
    session: AsyncSession, user: User, conversation_id: uuid.UUID
) -> DiagnosisConversation:
    """The conversation, if the caller may see its vehicle (else 404, FM-5)."""
    conv = await session.get(DiagnosisConversation, conversation_id)
    if conv is None:
        raise not_found("conversation_not_found", "Conversation not found")
    try:
        await can_access_vehicle(session, user.id, conv.vehicle_id)
    except ApiError as exc:
        if exc.status_code == 404:
            raise not_found("conversation_not_found", "Conversation not found") from exc
        raise
    return conv


async def _active_for_vehicle(session: AsyncSession, vehicle_id: uuid.UUID) -> Optional[DiagnosisConversation]:
    return (await session.execute(
        select(DiagnosisConversation).where(
            DiagnosisConversation.vehicle_id == vehicle_id,
            DiagnosisConversation.status.in_(ACTIVE_STATUSES),
        )
    )).scalar_one_or_none()


async def start_diagnosis(
    session: AsyncSession, user: User, vehicle_id: uuid.UUID, obd_log_id: uuid.UUID
) -> Tuple[DiagnosisConversation, bool]:
    """``(conversation, existing)`` — see the module docstring."""
    vehicle = await can_access_vehicle(session, user.id, vehicle_id)
    vid, uid = vehicle.id, user.id        # plain values: a rollback expires ORM objects
    obd_log = await session.get(ObdLog, obd_log_id)
    if obd_log is None or obd_log.vehicle_id != vid:
        raise not_found("log_not_found", "Log not found")
    log_id = obd_log.id
    existing = await _active_for_vehicle(session, vid)
    if existing is not None:
        return existing, True
    ahead = int((await session.execute(
        select(func.count()).select_from(DiagnosisConversation)
        .where(DiagnosisConversation.status.in_(ACTIVE_STATUSES))
    )).scalar_one())
    conv = DiagnosisConversation(vehicle_id=vid, obd_log_id=log_id, created_by=uid, status="queued")
    session.add(conv)
    try:
        await session.flush()
        store.add_events(session, conv.id, 1, [(ev.WAITING, {
            "reason": "queued", "message": wait_text("queued", settings.default_locale, ahead=ahead),
            "ahead": ahead, "waited_s": 0, "limit_s": int(settings.diagnosis_model_wait_s),
        })])
        await session.commit()
    except IntegrityError:
        await session.rollback()           # a concurrent click won (FM-15)
        existing = await _active_for_vehicle(session, vid)
        if existing is not None:
            return existing, True
        raise
    cid = conv.id
    try:
        job_id = await tasks.defer_diagnosis(cid)
    except Exception as exc:  # noqa: BLE001 — surface as 503, never leave it queued (FM-9)
        log.error("diagnosis.defer_failed", conversation_id=str(cid), error=type(exc).__name__)
        await store.close_with_error(SessionLocal, cid, "queue_unavailable",
                                     locale=settings.default_locale, stage="enqueue")
        raise ApiError(503, "queue_unavailable", "Could not queue the diagnosis") from exc
    await session.execute(
        update(DiagnosisConversation)
        .where(DiagnosisConversation.id == cid, DiagnosisConversation.job_id.is_(None))
        .values(job_id=job_id)
    )
    await session.commit()
    await session.refresh(conv)
    log.info("diagnosis.queued", conversation_id=str(cid), job_id=job_id, ahead=ahead)
    return conv, False


def _conversation_out(conv: DiagnosisConversation, report: Optional[Report]) -> ConversationOut:
    out = ConversationOut.model_validate(conv)
    out.has_report = report is not None
    out.report_partial = report.partial if report is not None else None
    return out


async def list_conversations(
    session: AsyncSession, user: User, vehicle_id: uuid.UUID, limit: int, offset: int
) -> List[ConversationOut]:
    """Diagnosis history of one vehicle, newest first."""
    await can_access_vehicle(session, user.id, vehicle_id)
    rows = (await session.execute(
        select(DiagnosisConversation, Report)
        .outerjoin(Report, Report.conversation_id == DiagnosisConversation.id)
        .where(DiagnosisConversation.vehicle_id == vehicle_id)
        .order_by(DiagnosisConversation.created_at.desc())
        .limit(limit).offset(offset)
    )).all()
    return [_conversation_out(c, r) for c, r in rows]


async def get_conversation(
    session: AsyncSession, user: User, conversation_id: uuid.UUID, include_messages: bool
) -> ConversationDetailOut:
    """One conversation (+ its stored messages on request)."""
    conv = await authorised_conversation(session, user, conversation_id)
    report = (await session.execute(
        select(Report).where(Report.conversation_id == conv.id))).scalar_one_or_none()
    out = ConversationDetailOut(**_conversation_out(conv, report).model_dump())
    if include_messages:
        rows = (await session.execute(
            select(Message).where(Message.conversation_id == conv.id).order_by(Message.seq)
        )).scalars().all()
        out.messages = [MessageOut(seq=m.seq, kind=m.kind, content=m.content) for m in rows]
    return out


async def get_report(session: AsyncSession, user: User, conversation_id: uuid.UUID) -> ReportOut:
    """The report, or 404 ``report_not_ready`` / ``report_unavailable``."""
    conv = await authorised_conversation(session, user, conversation_id)
    report = (await session.execute(
        select(Report).where(Report.conversation_id == conv.id))).scalar_one_or_none()
    if report is None:
        if conv.status in ACTIVE_STATUSES:
            raise not_found("report_not_ready", "The diagnosis is still running")
        raise not_found("report_unavailable", "This diagnosis ended without a report")
    return ReportOut(
        conversation_id=conv.id, content_md=report.content_md, citations=list(report.citations or []),
        partial=report.partial, stopped_reason=report.stopped_reason,
        limitations=list(report.limitations or []), model=report.model, model_source=report.model_source,
        total_tokens=report.total_tokens, requests=report.requests, tool_calls=report.tool_calls,
        elapsed_s=float(report.elapsed_s) if report.elapsed_s is not None else None,
        created_at=report.created_at,
    )


async def cancel(session: AsyncSession, user: User, conversation_id: uuid.UUID) -> CancelOut:
    """Cancels a diagnosis (FM-11).

    * queued → closed at once (``cancelled`` + ``done`` event), its job aborted;
    * running with a live job → the flag is set; the job stops before its
      next model request (or within seconds while waiting for the model);
    * running whose job is gone (orphan) → closed at once;
    * already finished → 409 ``conversation_finished``.
    """
    conv = await authorised_conversation(session, user, conversation_id)
    cid, job_id = conv.id, conv.job_id
    if conv.status in FINAL_STATUSES:
        raise ApiError(409, "conversation_finished", f"The diagnosis already ended ({conv.status})")
    if conv.status == "queued":
        if await store.close_cancelled(SessionLocal, cid, from_statuses=("queued",)):
            if job_id is not None:
                await tasks.cancel_job(job_id)
            return CancelOut(conversation_id=cid, status="cancelled", cancel_requested=True)
    updated = (await session.execute(
        update(DiagnosisConversation)
        .where(DiagnosisConversation.id == cid, DiagnosisConversation.status == "running")
        .values(cancel_requested=True, updated_at=func.now())
        .returning(DiagnosisConversation.job_id)
    )).first()
    await session.commit()
    if updated is None:
        await session.refresh(conv)
        return CancelOut(conversation_id=cid, status=conv.status, cancel_requested=conv.cancel_requested)
    running_job = updated[0]
    alive = running_job is not None and (await tasks.job_status(int(running_job))) in ("todo", "doing")
    if not alive and await store.close_cancelled(SessionLocal, cid, from_statuses=("running",)):
        return CancelOut(conversation_id=cid, status="cancelled", cancel_requested=True)
    return CancelOut(conversation_id=cid, status="running", cancel_requested=True)
