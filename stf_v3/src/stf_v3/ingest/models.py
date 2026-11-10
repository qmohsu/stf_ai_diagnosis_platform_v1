"""Ingest table: obd_logs (upload only, never diagnoses).

The data-ownership invariant (dev plan D8) is enforced here at the database
level: ``vehicle_id`` is NOT NULL, so a raw log can never exist without a
vehicle record.

Recording times are stored as true instants and read back in the
logger's zone (:class:`LoggerWallClock`).

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
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from stf_v3.db import Base
from stf_v3.ingest.parsers.base import LOGGER_TZ


class LoggerWallClock(TypeDecorator):
    """``timestamptz`` that loads in the logger's zone (``LOGGER_TZ``).

    The database keeps the true instant.  Loading it as Hong Kong time keeps
    the wall-clock digits equal to the log's own ``Timestamp`` column, which
    the diagnosis tools read as written, so the time range in the agent's
    case context lines up with the rows it inspects (issue #257).  The API
    shows the same value with a ``+08:00`` offset.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(
        self, value: Optional[datetime], dialect: Dialect
    ) -> Optional[datetime]:
        """Converts a loaded instant to ``LOGGER_TZ``.

        Args:
            value: Aware datetime from the driver, or ``None``.
            dialect: SQLAlchemy dialect in use (unused).

        Returns:
            The same instant in ``LOGGER_TZ``, or ``None``.
        """
        return value.astimezone(LOGGER_TZ) if value is not None else None


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
        LoggerWallClock()
    )
    recorded_end: Mapped[Optional[datetime]] = mapped_column(
        LoggerWallClock()
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
