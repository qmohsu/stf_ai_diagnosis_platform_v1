"""Ingest table: obd_logs (upload only, never diagnoses).

The data-ownership invariant (dev plan D8) is enforced here at the database
level: ``vehicle_id`` is NOT NULL, so a raw log can never exist without a
vehicle record.

Author: Xiangzhu Yan
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import CHAR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from stf_v3.db import Base


class ObdLog(Base):
    """One uploaded raw OBD log file bound to exactly one vehicle."""

    __tablename__ = "obd_logs"
    __table_args__ = (
        UniqueConstraint("vehicle_id", "sha256", name="uq_obd_logs_vehicle_sha"),
        CheckConstraint("source IN ('web','device')", name="source_values"),
        CheckConstraint("format IN ('tsv','yamaha','maxlog')", name="format_values"),
        Index("ix_obd_logs_vehicle_time", "vehicle_id", "uploaded_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicles.id"), nullable=False
    )
    sha256: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    raw_path: Mapped[str] = mapped_column(String(500), nullable=False)
    original_filename: Mapped[Optional[str]] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    device_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("vehicle_devices.id")
    )
    uploaded_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    format: Mapped[str] = mapped_column(String(10), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    vin_from_log: Mapped[Optional[str]] = mapped_column(String(17))
    vin_mismatch: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    recorded_start: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    recorded_end: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
