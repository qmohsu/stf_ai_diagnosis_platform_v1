"""Diagnosis endpoints (PROD-11): start, history, detail, events, report, cancel.

Every endpoint's docstring is its Swagger description — a reviewer who
never read the code must be able to run a diagnosis from them (FM-14).

Author: Xiangzhu Yan
"""

import uuid
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, Header, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from stf_v3.auth.manager import current_user
from stf_v3.auth.models import User
from stf_v3.db import SessionLocal, get_session
from stf_v3.diagnosis import service
from stf_v3.diagnosis.schemas import (
    CancelOut,
    ConversationDetailOut,
    ConversationOut,
    DiagnoseIn,
    DiagnoseOut,
    EventOut,
    ReportOut,
)
from stf_v3.diagnosis.sse import event_dict, stream_events
from stf_v3.diagnosis.store import read_events
from stf_v3.settings import settings

router = APIRouter(prefix="/v3", tags=["diagnosis"])

_ERR = {"description": "`{detail, code}`", "content": {"application/json": {"example": {
    "detail": "Vehicle not found", "code": "vehicle_not_found"}}}}


@router.post(
    "/vehicles/{vehicle_id}/diagnose",
    response_model=DiagnoseOut,
    status_code=status.HTTP_202_ACCEPTED,
    responses={200: {"model": DiagnoseOut, "description": "This vehicle already had an unfinished "
                     "diagnosis; that conversation is returned (`existing: true`)."},
               404: _ERR, 503: _ERR},
)
async def diagnose(
    vehicle_id: uuid.UUID,
    body: DiagnoseIn,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Any:
    """Starts a one-click diagnosis of one uploaded log of this vehicle.

    Returns **202** with `conversation_id` at once — the diagnosis runs in
    the background.  Follow it with `GET /v3/conversations/{id}/events`,
    then read `GET /v3/conversations/{id}/report`.

    * One diagnosis at a time per vehicle: if one is still queued or
      running, it is returned with **200** and `existing: true` (no new one).
    * The model is started on demand; the first diagnosis after a pause
      waits about 10–12 minutes (the event stream shows `waiting` events
      with the reason) and gives up after 60 minutes.
    * Errors: 404 `vehicle_not_found` (no such vehicle or not your
      workshop), 404 `log_not_found` (the log is not one of THIS vehicle's),
      503 `queue_unavailable`.
    """
    conv, existing = await service.start_diagnosis(session, user, vehicle_id, body.obd_log_id)
    out = DiagnoseOut(conversation_id=conv.id, status=conv.status, existing=existing,
                      obd_log_id=conv.obd_log_id)
    if existing:
        return JSONResponse(status_code=200, content=out.model_dump(mode="json"))
    return out


@router.get("/vehicles/{vehicle_id}/conversations", response_model=List[ConversationOut],
            responses={404: _ERR})
async def list_conversations(
    vehicle_id: uuid.UUID,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> List[ConversationOut]:
    """Diagnosis history of one vehicle, newest first (status, report flags)."""
    return await service.list_conversations(session, user, vehicle_id, limit, offset)


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailOut,
            responses={404: _ERR})
async def get_conversation(
    conversation_id: uuid.UUID,
    include_messages: bool = Query(default=False, description="Add the stored model messages (large)."),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ConversationDetailOut:
    """One conversation: status (`queued | running | done | error | cancelled`),
    timing, error code / sentence, whether a report exists and is partial."""
    return await service.get_conversation(session, user, conversation_id, include_messages)


@router.get(
    "/conversations/{conversation_id}/events",
    response_model=List[EventOut],
    responses={
        200: {"description": "JSON array (replay), or `text/event-stream` when the request "
                             "sends `Accept: text/event-stream`.",
              "content": {"text/event-stream": {"schema": {"type": "string"}, "example":
                          "event: tool_call\nid: 7\ndata: {\"seq\":7,\"event_type\":\"tool_call\","
                          "\"payload\":{...},\"created_at\":\"...\"}\n\n"}}},
        401: {"description": "Missing / expired token: refresh it, then reconnect with Last-Event-ID."},
        404: _ERR,
    },
)
async def conversation_events(
    conversation_id: uuid.UUID,
    request: Request,
    after_seq: int = Query(default=0, ge=0, description="Only events with seq > after_seq."),
    last_event_id: Optional[str] = Header(default=None, alias="Last-Event-ID",
                                          description="SSE reconnect: the last `id:` received."),
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> Any:
    """The diagnosis process: replay (JSON) or live stream (SSE).

    **Replay** (default, what Swagger shows): every stored event with
    `seq > after_seq`, in order.

    **Live**: send `Accept: text/event-stream`.  Frames are
    `event: <event_type>` / `id: <seq>` / `data: <the same JSON object>`;
    `: keepalive` comments every 15 s.  The stream ends after the `done`
    event (a partial run sends `error`, then `diagnosis_done`, then
    `done` — keep reading until `done`).  To resume after a disconnect,
    reconnect with `Last-Event-ID: <last seq>` (or `?after_seq=`); only
    the missing events are sent.  The token goes in the `Authorization`
    header only — browsers use `fetch()` with a streaming body reader
    (the native `EventSource` cannot send headers)::

        curl -N -H "Authorization: Bearer $TOKEN" -H "Accept: text/event-stream" \\
             https://<host>/v3/conversations/<id>/events

    Event names: `waiting` (queued / model starting / GPU busy …, with
    `reason` and `message`), `session_start`, `reasoning`, `token` (report
    text, one piece per model reply), `tool_call`, `tool_result`,
    `hypothesis`, `context_compact`, `diagnosis_done` (with `report_id`),
    `error` (`code` + `message`), `done`.  Sub-agent events carry
    `payload.parent_tool_call_id`.
    """
    conv = await service.authorised_conversation(session, user, conversation_id)
    start = after_seq
    if last_event_id and last_event_id.strip().isdigit():
        start = max(start, int(last_event_id.strip()))
    if "text/event-stream" in request.headers.get("accept", ""):
        # FM-36 / FM-53: give the request's connection back NOW — the
        # request session would otherwise hold it for the whole stream.
        cid = conv.id
        await session.close()
        return StreamingResponse(
            # No is_disconnected() polling: Starlette cancels the generator when
            # the client goes away, and the keepalive write surfaces a dead
            # connection within sse_keepalive_s.
            stream_events(cid, start, session_factory=SessionLocal, settings=settings),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    rows = await read_events(session, conv.id, start, limit=10_000)
    return [event_dict(r) for r in rows]


@router.get("/conversations/{conversation_id}/report", response_model=ReportOut,
            responses={404: _ERR})
async def get_report(
    conversation_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> ReportOut:
    """The diagnosis report (Markdown + citations).

    `partial: true` means a time / usage limit, a cancel or an error
    stopped the run early; `limitations` says why.  Errors: 404
    `report_not_ready` (still running), 404 `report_unavailable` (the
    diagnosis ended without a report — see the conversation's
    `error_code`), 404 `conversation_not_found`.
    """
    return await service.get_report(session, user, conversation_id)


@router.post("/conversations/{conversation_id}/cancel", response_model=CancelOut,
             status_code=status.HTTP_202_ACCEPTED, responses={404: _ERR, 409: _ERR})
async def cancel_conversation(
    conversation_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> CancelOut:
    """Cancels a diagnosis.

    A queued one ends at once (`status: cancelled`).  A running one stops
    before its next model request — usually within a minute; the event
    stream then ends with `done` (`stopped_reason: cancelled`) and any text
    produced so far is kept as a partial report.  409
    `conversation_finished` when it already ended.
    """
    return await service.cancel(session, user, conversation_id)
