"""Vehicle tables: vehicles (anchor) and vehicle_devices (D8 mechanism).

Author: Xiangzhu Yan
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import CHAR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from stf_v3.db import Base


class Vehicle(Base):
    """A vehicle record.  VIN is the identity; plate is a mutable label."""

    __tablename__ = "vehicles"
    __table_args__ = (
        CheckConstraint(
            "vin ~ '^[A-HJ-NPR-Z0-9]{17}$'", name="vin_format"
        ),
        Index(
            "ux_vehicles_workshop_vin",
            "workshop_id",
            "vin",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_vehicles_workshop", "workshop_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    workshop_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workshops.id"), nullable=False
    )
    vin: Mapped[str] = mapped_column(String(17), nullable=False)
    plate: Mapped[Optional[str]] = mapped_column(String(20))
    manufacturer: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    nickname: Mapped[Optional[str]] = mapped_column(String(100))
    deleted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class VehicleDevice(Base):
    """A per-vehicle upload credential held by an edge device (Jetson).

    Only ``sha256(token)`` is stored; the plaintext is shown once at
    creation.  Presenting the token on ``POST /v3/ingest/device`` binds the
    upload to this vehicle (data-ownership invariant, D8).
    """

    __tablename__ = "vehicle_devices"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid()
    )
    vehicle_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("vehicles.id", ondelete="CASCADE"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    token_hash: Mapped[str] = mapped_column(CHAR(64), unique=True, nullable=False)
    created_by: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
    revoked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True)
    )
