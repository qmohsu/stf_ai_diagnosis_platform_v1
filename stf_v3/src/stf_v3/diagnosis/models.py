"""Diagnosis tables: conversations, messages, reports, audit_events, and the
model-service state row (PROD-11).

PROD-11 revision (migration ``c4e8a1f2b7d3``): a vehicle has at most one
unfinished conversation (partial unique index, D4 / FM-15); conversations
record when they started / finished and a machine error code (FM-38);
messages are stored one Pydantic AI message per row as ``request`` /
``response`` (FM-47); reports carry the partial flag, stop reason and
limitations (FM-48); ``model_service_state`` is the single row the host
controller keeps about the on-demand vLLM (D1, FM-20 / FM-32).

Author: Xiangzhu Yan
"""

import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from stf_v3.db import Base

# Conversation statuses; the first two are "unfinished".
ACTIVE_STATUSES = ("queued", "running")
FINAL_STATUSES = ("done", "error", "cancelled")


class DiagnosisConversation(Base):
    """The container: one diagnosis run (and later its follow-ups)."""

    __tablename__ = "diagnosis_conversations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','done','error','cancelled')",
            name="status_values",
        ),
        Index("ix_conversations_vehicle_time", "vehicle_id", "created_at"),
        # D4 / FM-15: at most one unfinished conversation per vehicle.
        Index(
            "ux_conversations_vehicle_active", "vehicle_id", unique=True,
            postgresql_where=text("status IN ('queued','running')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicles.id"), nullable=False
    )
    obd_log_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("obd_logs.id"), nullable=False
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="queued"
    )
    stage: Mapped[str] = mapped_column(
        String(4), nullable=False, server_default="s1"
    )
    job_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    model: Mapped[Optional[str]] = mapped_column(String(100))
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    error_code: Mapped[Optional[str]] = mapped_column(String(40))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Message(Base):
    """One Pydantic AI model message (``request`` or ``response``), verbatim."""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "seq", name="uq_messages_conv_seq"),
        CheckConstraint(
            "kind IN ('request','response')", name="kind_values"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("diagnosis_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    content: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    token_usage: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Report(Base):
    """The product of a conversation: the diagnosis report."""

    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("diagnosis_conversations.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    total_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    elapsed_s: Mapped[Optional[float]] = mapped_column(Numeric(8, 2))
    # PROD-11 (FM-48): a partial report must be told apart from a full one.
    partial: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    stopped_reason: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="complete"
    )
    limitations: Mapped[Any] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    requests: Mapped[Optional[int]] = mapped_column(Integer)
    tool_calls: Mapped[Optional[int]] = mapped_column(Integer)
    model_source: Mapped[Optional[str]] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuditEvent(Base):
    """Append-only black box; ``event_type`` equals the SSE event name.

    The application DB role is granted INSERT/SELECT only on this table
    (see the initial migration), so rows can never be updated or deleted
    through the application.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        UniqueConstraint("conversation_id", "seq", name="uq_audit_conv_seq"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("diagnosis_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    payload: Mapped[Dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ModelServiceState(Base):
    """What the host controller knows about the on-demand vLLM (one row).

    Written only by the controller task on the host GPU worker (and the
    diagnosis job's ``last_used_at`` touch); read by diagnosis jobs (why
    they are waiting) and ``/v3/health``.  Persisted so a worker restart
    never loses the idle timer or the "we started it" flag (FM-32 / FM-26).
    """

    __tablename__ = "model_service_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="single_row"),
        CheckConstraint(
            "state IN ('stopped','starting','ready','blocked','failed')",
            name="state_values",
        ),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    state: Mapped[str] = mapped_column(
        String(10), nullable=False, server_default="stopped"
    )
    blocked_reason: Mapped[Optional[str]] = mapped_column(String(30))
    started_by_us: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    requested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    ready_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    failed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[Optional[str]] = mapped_column(Text)
    cooldown_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    controller_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    gpu_snapshot: Mapped[Optional[Any]] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
