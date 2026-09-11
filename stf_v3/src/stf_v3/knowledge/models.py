"""Knowledge table: manuals (metadata for the Markdown manual library).

Stage 1 keeps no vector store: the agent reads manual Markdown files from
disk (manual_fs), so ``rag_chunks`` / pgvector from V2 are intentionally
NOT carried over (decision 2026-09-11).  The ``manuals`` row records
identity, conversion status and progress.

Author: Xiangzhu Yan
"""

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from stf_v3.db import Base

MANUAL_STATUSES = (
    "uploading",
    "queued",
    "converting",
    "ingested",
    "failed",
)


class Manual(Base):
    """One service manual PDF and its converted Markdown."""

    __tablename__ = "manuals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('uploading','queued','converting','ingested','failed')",
            name="status_values",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    uploaded_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    manufacturer: Mapped[str] = mapped_column(String(100), nullable=False)
    vehicle_model: Mapped[str] = mapped_column(String(100), nullable=False)
    factory_code: Mapped[Optional[str]] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="uploading"
    )
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    page_count: Mapped[Optional[int]] = mapped_column(Integer)
    section_count: Mapped[Optional[int]] = mapped_column(Integer)
    language: Mapped[Optional[str]] = mapped_column(String(20))
    converter: Mapped[Optional[str]] = mapped_column(String(100))
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    md_file_path: Mapped[Optional[str]] = mapped_column(String(500))
    pdf_file_path: Mapped[Optional[str]] = mapped_column(String(500))
    pages_processed: Mapped[Optional[int]] = mapped_column(Integer)
    pages_total: Mapped[Optional[int]] = mapped_column(Integer)
    pages_phase: Mapped[Optional[str]] = mapped_column(String(50))
    warnings: Mapped[Optional[Any]] = mapped_column(JSONB)
    job_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
