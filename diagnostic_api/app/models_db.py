"""Database models for diagnostic API.

Author: Li-Ta Hsu
Date: January 2026
"""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.declarative import declared_attr
from sqlalchemy.orm import relationship

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """User table for local JWT authentication."""

    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    username = Column(String(50), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=_utcnow)

    sessions = relationship(
        "OBDAnalysisSession", back_populates="user",
    )


class OBDAnalysisSession(Base):
    """OBD analysis session records."""

    __tablename__ = "obd_analysis_sessions"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "input_text_hash",
            name="uq_user_input_hash",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    vehicle_id = Column(String(50), nullable=True, index=True)

    # Status tracking
    status = Column(String(20), default="PENDING", index=True)  # PENDING, COMPLETED, FAILED

    # Input metadata
    input_text_hash = Column(String(64), nullable=False, index=True)  # SHA-256
    input_size_bytes = Column(Integer, nullable=False)

    # Relative path to raw OBD log file on disk
    raw_input_file_path = Column(String(500), nullable=True)

    # Result stored as JSONB (full LogSummaryV2)
    result_payload = Column(JSONB, nullable=True)

    # Flat-string parsed summary (short JSON)
    parsed_summary_payload = Column(JSONB, nullable=True)

    error_message = Column(Text, nullable=True)

    # AI diagnosis (free-form markdown from LLM)
    diagnosis_text = Column(Text, nullable=True)

    # Premium AI diagnosis (cloud LLM, opt-in)
    premium_diagnosis_text = Column(Text, nullable=True)
    premium_diagnosis_model = Column(String(200), nullable=True)

    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow)

    # Relationships
    user = relationship("User", back_populates="sessions")
    summary_feedback = relationship("OBDSummaryFeedback", back_populates="session", uselist=True)
    detailed_feedback = relationship("OBDDetailedFeedback", back_populates="session", uselist=True)
    rag_feedback = relationship("OBDRAGFeedback", back_populates="session", uselist=True)
    ai_diagnosis_feedback = relationship("OBDAIDiagnosisFeedback", back_populates="session", uselist=True)
    premium_diagnosis_feedback = relationship("OBDPremiumDiagnosisFeedback", back_populates="session", uselist=True)
    diagnosis_history = relationship("DiagnosisHistory", back_populates="session", uselist=True)


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

    # Optional audio recording attached to feedback
    audio_file_path = Column(String(500), nullable=True)
    audio_duration_seconds = Column(Integer, nullable=True)
    audio_size_bytes = Column(Integer, nullable=True)

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

    # Snapshot of the RAG-retrieved text the user was rating
    retrieved_text = Column(Text, nullable=True)

    session = relationship("OBDAnalysisSession", back_populates="rag_feedback")


class OBDAIDiagnosisFeedback(_OBDFeedbackMixin, Base):
    """Expert feedback on OBD AI diagnosis view."""

    __tablename__ = "obd_ai_diagnosis_feedback"

    # Snapshot of the diagnosis text the user was rating
    diagnosis_text = Column(Text, nullable=True)

    # Optional link to the specific diagnosis generation
    diagnosis_history_id = Column(
        UUID(as_uuid=True),
        ForeignKey("diagnosis_history.id"),
        nullable=True,
        index=True,
    )

    session = relationship("OBDAnalysisSession", back_populates="ai_diagnosis_feedback")
    diagnosis_history = relationship(
        "DiagnosisHistory",
        foreign_keys=[diagnosis_history_id],
    )


class OBDPremiumDiagnosisFeedback(_OBDFeedbackMixin, Base):
    """Expert feedback on OBD premium AI diagnosis view."""

    __tablename__ = "obd_premium_diagnosis_feedback"

    # Snapshot of the premium diagnosis text the user was rating
    diagnosis_text = Column(Text, nullable=True)

    # Optional link to the specific diagnosis generation
    diagnosis_history_id = Column(
        UUID(as_uuid=True),
        ForeignKey("diagnosis_history.id"),
        nullable=True,
        index=True,
    )

    session = relationship("OBDAnalysisSession", back_populates="premium_diagnosis_feedback")
    diagnosis_history = relationship(
        "DiagnosisHistory",
        foreign_keys=[diagnosis_history_id],
    )


class DiagnosisHistory(Base):
    """Immutable log of every AI diagnosis generation.

    Covers both local (Ollama) and premium (OpenRouter) providers.
    Each row is an append-only record; the ``diagnosis_text`` and
    ``premium_diagnosis_text`` columns on ``OBDAnalysisSession``
    still hold the latest values for quick access.
    """

    __tablename__ = "diagnosis_history"
    __table_args__ = (
        CheckConstraint(
            "provider IN ('local', 'premium')",
            name="ck_diagnosis_history_provider",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey("obd_analysis_sessions.id"),
        nullable=False,
        index=True,
    )
    provider = Column(
        String(20), nullable=False,
    )  # "local" | "premium"
    model_name = Column(
        String(200), nullable=False,
    )  # e.g. "qwen3.5:9b", "anthropic/claude-sonnet-4.6"
    diagnosis_text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_utcnow)

    session = relationship(
        "OBDAnalysisSession",
        back_populates="diagnosis_history",
    )


class RagChunk(Base):
    """RAG knowledge chunk with pgvector embedding.

    Stores chunked document text alongside its vector embedding
    for semantic retrieval.  Replaces the former Weaviate
    ``KnowledgeChunk`` collection.
    """

    __tablename__ = "rag_chunks"

    id = Column(
        UUID(as_uuid=True), primary_key=True, default=uuid4,
    )
    text = Column(Text, nullable=False)
    doc_id = Column(String(255), nullable=False, index=True)
    source_type = Column(String(50), nullable=False)
    section_title = Column(String(500), nullable=True)
    vehicle_model = Column(
        String(100), nullable=True, index=True,
    )
    chunk_index = Column(Integer, nullable=False)
    checksum = Column(
        String(64), unique=True, nullable=False, index=True,
    )
    metadata_json = Column(JSONB, nullable=True)
    embedding = Column(Vector(768), nullable=False)
    created_at = Column(DateTime, default=_utcnow)
