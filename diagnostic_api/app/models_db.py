"""Database models for diagnostic API.

Author: Li-Ta Hsu
Date: January 2026
"""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.orm import relationship

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """User table (stub for future auth)."""

    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utcnow)


class Vehicle(Base):
    """Vehicle registry."""

    __tablename__ = "vehicles"

    id = Column(String(50), primary_key=True, index=True)  # VIN or internal ID
    make = Column(String(50), nullable=True)
    model = Column(String(50), nullable=True)
    year = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    
    # Relationships
    diagnostic_sessions = relationship("DiagnosticSession", back_populates="vehicle")


class DiagnosticSession(Base):
    """Diagnostic session records."""

    __tablename__ = "diagnostic_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    vehicle_id = Column(String(50), ForeignKey("vehicles.id"), nullable=False, index=True)
    
    # Status tracking
    status = Column(String(20), default="PENDING", index=True)  # PENDING, COMPLETED, FAILED
    
    # Request/Response payloads stored as JSONB for flexibility
    request_payload = Column(JSONB, nullable=False)
    result_payload = Column(JSONB, nullable=True)
    
    # Extracted fields for efficient querying without parsing JSONB
    risk_score = Column(Float, nullable=True)
    
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)
    
    # Relationships
    vehicle = relationship("Vehicle", back_populates="diagnostic_sessions")
    feedback = relationship("DiagnosticFeedback", back_populates="session", uselist=False)


class DiagnosticFeedback(Base):
    """Feedback from technicians on diagnostic sessions."""

    __tablename__ = "diagnostic_feedback"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id = Column(UUID(as_uuid=True), ForeignKey("diagnostic_sessions.id"), nullable=False, unique=True)
    
    rating = Column(Integer, nullable=False)  # 1-5
    is_helpful = Column(Boolean, nullable=False)
    comments = Column(Text, nullable=True)
    corrected_diagnosis = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=_utcnow)
    
    # Relationships
    session = relationship("DiagnosticSession", back_populates="feedback")


class OBDAnalysisSession(Base):
    """OBD analysis session records (separate from DiagnosticSession)."""

    __tablename__ = "obd_analysis_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    vehicle_id = Column(String(50), nullable=True, index=True)

    # Status tracking
    status = Column(String(20), default="PENDING", index=True)  # PENDING, COMPLETED, FAILED

    # Input metadata
    input_text_hash = Column(String(64), nullable=False, index=True)  # SHA-256
    input_size_bytes = Column(Integer, nullable=False)

    # Raw OBD TSV text verbatim
    raw_input_text = Column(Text, nullable=True)

    # Result stored as JSONB (full LogSummaryV2)
    result_payload = Column(JSONB, nullable=True)

    # Dify-formatted parsed summary (short JSON)
    parsed_summary_payload = Column(JSONB, nullable=True)

    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    summary_feedback = relationship("OBDSummaryFeedback", back_populates="session", uselist=True)
    detailed_feedback = relationship("OBDDetailedFeedback", back_populates="session", uselist=True)
    rag_feedback = relationship("OBDRAGFeedback", back_populates="session", uselist=True)


class _OBDFeedbackMixin:
    """Shared columns for OBD feedback tables.

    ``session_id`` uses ``@declared_attr`` because its ``ForeignKey`` must be
    unique per table; plain ``Column`` objects (``id``, ``rating``, etc.) are
    safely copied by SQLAlchemy's mixin machinery.
    """

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)

    @declared_attr
    def session_id(cls):
        return Column(
            UUID(as_uuid=True),
            ForeignKey("obd_analysis_sessions.id"),
            nullable=False,
            index=True,
        )

    rating = Column(Integer, nullable=False)  # 1-5
    is_helpful = Column(Boolean, nullable=False)
    comments = Column(Text, nullable=True)
    corrected_diagnosis = Column(Text, nullable=True)

    created_at = Column(DateTime, default=_utcnow)


class OBDSummaryFeedback(_OBDFeedbackMixin, Base):
    """Expert feedback on OBD analysis summary view."""

    __tablename__ = "obd_summary_feedback"

    session = relationship("OBDAnalysisSession", back_populates="summary_feedback")


class OBDDetailedFeedback(_OBDFeedbackMixin, Base):
    """Expert feedback on OBD analysis detailed view."""

    __tablename__ = "obd_detailed_feedback"

    session = relationship("OBDAnalysisSession", back_populates="detailed_feedback")


class OBDRAGFeedback(_OBDFeedbackMixin, Base):
    """Expert feedback on OBD analysis RAG view."""

    __tablename__ = "obd_rag_feedback"

    session = relationship("OBDAnalysisSession", back_populates="rag_feedback")
