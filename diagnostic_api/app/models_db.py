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
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
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
            "provider IN ('local', 'premium', 'agent')",
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
    )  # e.g. "qwen3.5:27b-q8_0", "anthropic/claude-sonnet-4.6"
    diagnosis_text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=_utcnow)

    session = relationship(
        "OBDAnalysisSession",
        back_populates="diagnosis_history",
    )


class HarnessEventLog(Base):
    """Append-only event log for agent diagnosis sessions.

    Every tool call, result, and reasoning step during an agent
    diagnosis session is persisted here for auditability, debugging,
    and future training data extraction.  No UPDATE or DELETE
    operations should ever be performed on this table.
    """

    __tablename__ = "harness_event_log"
    __table_args__ = (
        Index(
            "ix_harness_event_session_time",
            "session_id",
            "created_at",
        ),
    )

    id = Column(
        UUID(as_uuid=True), primary_key=True, default=uuid4,
    )
    session_id = Column(
        UUID(as_uuid=True),
        ForeignKey(
            "obd_analysis_sessions.id", ondelete="CASCADE",
        ),
        nullable=False,
        index=True,
    )
    event_type = Column(
        String(50), nullable=False, index=True,
    )
    iteration = Column(Integer, nullable=False, default=0)
    payload = Column(JSONB, nullable=False)
    created_at = Column(
        DateTime, server_default=func.now(), nullable=False,
    )


class Manual(Base):
    """Uploaded service manual with conversion lifecycle.

    Tracks the full pipeline from PDF upload through marker-pdf
    conversion to RAG ingestion.  The ``file_hash`` unique
    constraint prevents duplicate uploads of the same PDF.
    """

    __tablename__ = "manuals"
    __table_args__ = (
        CheckConstraint(
            "status IN ("
            "'uploading', 'converting', 'chunking', "
            "'embedding', 'ingested', 'failed'"
            ")",
            name="ck_manual_status",
        ),
    )

    id = Column(
        UUID(as_uuid=True), primary_key=True, default=uuid4,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    filename = Column(String(500), nullable=False)
    file_hash = Column(
        String(64), unique=True, nullable=False, index=True,
    )
    vehicle_model = Column(String(100), nullable=True)
    status = Column(
        String(20), nullable=False, default="uploading",
    )
    file_size_bytes = Column(Integer, nullable=False)
    page_count = Column(Integer, nullable=True)
    section_count = Column(Integer, nullable=True)
    language = Column(String(20), nullable=True)
    converter = Column(String(100), nullable=True)
    error_message = Column(Text, nullable=True)
    md_file_path = Column(String(500), nullable=True)
    pdf_file_path = Column(String(500), nullable=True)
    chunk_count = Column(Integer, nullable=True)
    # Live progress while marker-pdf is converting.  Both are
    # nullable; populated by the worker via the .progress.json
    # protocol (see scripts/marker_worker.py).  ``pages_total``
    # is the running estimate; ``page_count`` is the final value
    # written from marker's result.json.
    pages_processed = Column(Integer, nullable=True)
    pages_total = Column(Integer, nullable=True)
    # Current marker-pdf pipeline stage label (e.g. "Layout",
    # "OCR", "Recognition") taken from the active ``tqdm.desc``.
    # Lets the UI render "OCR 283/434" instead of the bare
    # counter and explains when the total shifts between stages.
    pages_phase = Column(String(50), nullable=True)
    # Ingestion-quality warnings captured during marker-pdf
    # conversion (LLM retry / fallback events).  Schema:
    # ``[{"event": str, "page": int|None, "processor": str|None,
    #     "reason": str|None, "ts": iso8601}]``.  ``None`` means
    # no warnings (clean conversion).
    warnings = Column(JSONB, nullable=True)
    created_at = Column(DateTime, default=_utcnow)
    updated_at = Column(
        DateTime, default=_utcnow, onupdate=_utcnow,
    )

    user = relationship("User")


class GoldenEntry(Base):
    """Mirror of a golden Q&A entry from the eval JSONL files.

    The ``tests/harness/evals/golden/v2/*.jsonl`` files remain
    the canonical source of truth — this table is a queryable
    cache that gets refreshed on app startup (and any time the
    sync routine is invoked).  Reviews tie back to entries via
    ``id``, which is the same string identifier used in the
    JSONL (e.g. ``<manual_uuid>-dtc-001``).

    Bilingual fields (``question_zh``, ``golden_summary_zh``)
    are NULLABLE — entries authored before the bilingualisation
    push (HARNESS-17) carry only English text; the dashboard
    falls back to English when Chinese is missing.
    """

    __tablename__ = "golden_entries"
    __table_args__ = (
        CheckConstraint(
            "category IN ("
            "'dtc', 'symptom', 'component', "
            "'adversarial', 'image')",
            name="ck_golden_entry_category",
        ),
        CheckConstraint(
            "question_type IN ("
            "'lookup', 'procedural', 'cross-section', "
            "'image-required', 'adversarial')",
            name="ck_golden_entry_question_type",
        ),
        CheckConstraint(
            "difficulty IN ('easy', 'medium', 'hard')",
            name="ck_golden_entry_difficulty",
        ),
    )

    id = Column(String(255), primary_key=True)
    manual_id = Column(String(255), nullable=False, index=True)
    category = Column(String(50), nullable=False, index=True)
    question_type = Column(
        String(50), nullable=False, index=True,
    )
    difficulty = Column(String(20), nullable=False)
    question_en = Column(Text, nullable=False)
    question_zh = Column(Text, nullable=True)
    obd_context = Column(Text, nullable=True)
    golden_summary_en = Column(Text, nullable=False)
    golden_summary_zh = Column(Text, nullable=True)
    golden_citations = Column(JSONB, nullable=False)
    expected_recall_slugs = Column(JSONB, nullable=True)
    must_contain = Column(JSONB, nullable=True)
    pitfall_directives = Column(JSONB, nullable=True)
    requires_image = Column(
        Boolean, nullable=False, default=False,
    )
    notes = Column(Text, nullable=True)
    source_jsonl_path = Column(String(500), nullable=True)
    source_jsonl_line = Column(Integer, nullable=True)
    created_at = Column(
        DateTime, server_default=func.now(), nullable=False,
    )
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=_utcnow,
        nullable=False,
    )

    reviews = relationship(
        "GoldenReview",
        back_populates="entry",
        cascade="all, delete-orphan",
    )


class GoldenReview(Base):
    """One reviewer's grade of one golden entry.

    Star ratings are 1-5, all nullable so a reviewer can save
    a draft with notes-only and return to fill in stars later.
    Three per-dimension scores match the Markdown export's
    grading rubric (question realism / answer correctness /
    citation faithfulness); a fourth ``star_rating`` is the
    overall holistic rating.

    Audio columns mirror the existing ``_OBDFeedbackMixin``
    pattern so the same ``AudioRecorder`` two-step token-upload
    flow can be reused without code changes.

    The ``(golden_entry_id, reviewer_id)`` unique constraint
    means each user has at most one review per entry — updates
    are upserts.  Cross-rater agreement analysis is computed at
    query time from the multiple reviews a single entry collects.
    """

    __tablename__ = "golden_reviews"
    __table_args__ = (
        UniqueConstraint(
            "golden_entry_id", "reviewer_id",
            name="uq_golden_review_entry_reviewer",
        ),
        CheckConstraint(
            "star_rating IS NULL OR "
            "(star_rating BETWEEN 1 AND 5)",
            name="ck_golden_review_star_rating",
        ),
        CheckConstraint(
            "question_realism_score IS NULL OR "
            "(question_realism_score BETWEEN 1 AND 5)",
            name="ck_golden_review_q_realism_score",
        ),
        CheckConstraint(
            "answer_correctness_score IS NULL OR "
            "(answer_correctness_score BETWEEN 1 AND 5)",
            name="ck_golden_review_a_correctness_score",
        ),
        CheckConstraint(
            "citation_faithfulness_score IS NULL OR "
            "(citation_faithfulness_score BETWEEN 1 AND 5)",
            name="ck_golden_review_c_faithfulness_score",
        ),
        CheckConstraint(
            "status IN ("
            "'draft', 'accept', 'needs_revision', 'reject')",
            name="ck_golden_review_status",
        ),
    )

    id = Column(
        UUID(as_uuid=True), primary_key=True, default=uuid4,
    )
    golden_entry_id = Column(
        String(255),
        ForeignKey("golden_entries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reviewer_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    star_rating = Column(Integer, nullable=True)
    question_realism_score = Column(Integer, nullable=True)
    answer_correctness_score = Column(Integer, nullable=True)
    citation_faithfulness_score = Column(Integer, nullable=True)
    status = Column(
        String(20), nullable=False, default="draft",
    )
    notes = Column(Text, nullable=True)
    audio_file_path = Column(String(500), nullable=True)
    audio_duration_seconds = Column(Integer, nullable=True)
    audio_size_bytes = Column(Integer, nullable=True)
    # Frozen copy of the entry's text at submit time.  Set by
    # the submit endpoint so reviews remain reproducible even
    # after the live entry is edited (Phase 3 feature).  Pre-
    # Phase-2 reviews have NULL snapshots; the dashboard falls
    # back to live entry text in that case.
    snapshot_question_en = Column(Text, nullable=True)
    snapshot_question_zh = Column(Text, nullable=True)
    snapshot_summary_en = Column(Text, nullable=True)
    snapshot_summary_zh = Column(Text, nullable=True)
    snapshot_citations = Column(JSONB, nullable=True)
    created_at = Column(
        DateTime, server_default=func.now(), nullable=False,
    )
    updated_at = Column(
        DateTime,
        server_default=func.now(),
        onupdate=_utcnow,
        nullable=False,
    )

    entry = relationship(
        "GoldenEntry", back_populates="reviews",
    )
    reviewer = relationship("User")


class RagChunk(Base):
    """RAG knowledge chunk with pgvector embedding.

    Stores chunked document text alongside its vector embedding
    for semantic retrieval.  Every chunk belongs to a ``Manual``
    via ``manual_id``; cascade delete removes chunks when the
    parent manual is deleted.  ``source_type`` is currently
    locked to ``'manual'``; future ingestion sources will require
    a migration that relaxes the CHECK constraint.
    """

    __tablename__ = "rag_chunks"
    __table_args__ = (
        CheckConstraint(
            "source_type = 'manual'",
            name="ck_rag_chunk_source_type",
        ),
    )

    id = Column(
        UUID(as_uuid=True), primary_key=True, default=uuid4,
    )
    manual_id = Column(
        UUID(as_uuid=True),
        ForeignKey("manuals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    text = Column(Text, nullable=False)
    doc_id = Column(String(255), nullable=False, index=True)
    source_type = Column(String(50), nullable=False)
    section_title = Column(Text, nullable=True)
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

    manual = relationship("Manual")
