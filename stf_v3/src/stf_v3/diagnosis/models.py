"""Diagnosis tables: conversations, messages, reports, audit_events.

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
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from stf_v3.db import Base


class DiagnosisConversation(Base):
    """The container: one diagnosis run (and later its follow-ups)."""

    __tablename__ = "diagnosis_conversations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued','running','done','error','cancelled')",
            name="status_values",
        ),
        Index("ix_conversations_vehicle_time", "vehicle_id", "created_at"),
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
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Message(Base):
    """A Pydantic AI model message, serialized verbatim as JSONB."""

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "seq", name="uq_messages_conv_seq"),
        CheckConstraint(
            "role IN ('system','user','assistant','tool')", name="role_values"
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
    role: Mapped[str] = mapped_column(String(10), nullable=False)
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
