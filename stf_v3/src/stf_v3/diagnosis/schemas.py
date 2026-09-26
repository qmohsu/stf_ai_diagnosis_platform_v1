"""API models of the diagnosis endpoints (PROD-11, OpenAPI contract v2).

The event names are an enum built from the ONE registry in
``diagnosis.agent.events`` (FM-13): what the job writes, what SSE sends
and what the contract lists can never drift apart.

Author: Xiangzhu Yan
"""

import datetime as dt
import enum
import uuid
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from stf_v3.diagnosis.agent.events import EVENT_TYPES

EventType = enum.Enum("EventType", {name.upper(): name for name in EVENT_TYPES}, type=str)  # type: ignore[misc]
EventType.__doc__ = "Diagnosis event names (= audit_events.event_type = SSE event names)."


class DiagnoseIn(BaseModel):
    """Start a diagnosis of one uploaded log of this vehicle."""

    obd_log_id: uuid.UUID = Field(description="An uploaded log of THIS vehicle (see GET /v3/vehicles/{id}/logs).")


class DiagnoseOut(BaseModel):
    """The conversation that runs (or already runs) the diagnosis."""

    conversation_id: uuid.UUID
    status: str = Field(description="queued | running | done | error | cancelled")
    existing: bool = Field(
        description="True when this vehicle already had an unfinished diagnosis; "
                    "that one is returned and no new one is created (one at a time per vehicle).")
    obd_log_id: uuid.UUID = Field(description="The log the returned conversation diagnoses.")


class ConversationOut(BaseModel):
    """One diagnosis conversation."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    vehicle_id: uuid.UUID
    obd_log_id: uuid.UUID
    status: str = Field(description="queued | running | done | error | cancelled")
    stage: str
    model: Optional[str] = None
    cancel_requested: bool
    error_code: Optional[str] = Field(default=None, description="Stable code when status is error.")
    error_message: Optional[str] = Field(default=None, description="Short sentence for the user.")
    created_at: dt.datetime
    started_at: Optional[dt.datetime] = None
    finished_at: Optional[dt.datetime] = None
    has_report: bool = False
    report_partial: Optional[bool] = None


class MessageOut(BaseModel):
    """One stored model message (request or response), verbatim."""

    seq: int
    kind: str
    content: Dict[str, Any]


class ConversationDetailOut(ConversationOut):
    """Conversation + (optionally) its stored model messages."""

    messages: Optional[List[MessageOut]] = Field(
        default=None, description="Only with ?include_messages=true (can be large).")


class EventOut(BaseModel):
    """One event of the black box (the same object an SSE frame carries)."""

    seq: int = Field(description="1-based, gapless per conversation; SSE `id:`; resume after it.")
    event_type: EventType = Field(description="SSE `event:` name.")  # type: ignore[valid-type]
    payload: Dict[str, Any]
    created_at: dt.datetime


class ReportOut(BaseModel):
    """The diagnosis report."""

    conversation_id: uuid.UUID
    content_md: str
    citations: List[Dict[str, Any]]
    partial: bool = Field(description="True when a limit, a cancel or an error stopped the run early.")
    stopped_reason: str = Field(description="complete | timeout | budget | cancelled | error")
    limitations: List[str]
    model: str
    model_source: Optional[str] = None
    total_tokens: Optional[int] = None
    requests: Optional[int] = None
    tool_calls: Optional[int] = None
    elapsed_s: Optional[float] = None
    created_at: dt.datetime


class CancelOut(BaseModel):
    """Result of a cancel request."""

    conversation_id: uuid.UUID
    status: str = Field(description="Status right after the request (queued runs end at once).")
    cancel_requested: bool
