"""Pydantic schemas for vehicle and device endpoints.

Author: Xiangzhu Yan
"""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator

VIN_PATTERN = r"^[A-HJ-NPR-Z0-9]{17}$"


class VehicleIn(BaseModel):
    """Body of ``POST /v3/workshops/{wid}/vehicles``."""

    vin: str = Field(min_length=17, max_length=17)
    manufacturer: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=100)
    plate: Optional[str] = Field(default=None, max_length=20)
    nickname: Optional[str] = Field(default=None, max_length=100)

    @field_validator("vin")
    @classmethod
    def _vin_upper(cls, value: str) -> str:
        """Normalises and validates the VIN (ISO 3779: no I, O, Q)."""
        import re

        value = value.strip().upper()
        if not re.fullmatch(VIN_PATTERN, value):
            raise ValueError("VIN must be 17 chars A-Z/0-9 without I, O, Q")
        return value


class VehiclePatch(BaseModel):
    """Body of ``PATCH /v3/vehicles/{id}`` (VIN is immutable)."""

    manufacturer: Optional[str] = Field(default=None, min_length=1, max_length=100)
    model: Optional[str] = Field(default=None, min_length=1, max_length=100)
    plate: Optional[str] = Field(default=None, max_length=20)
    nickname: Optional[str] = Field(default=None, max_length=100)


class VehicleOut(BaseModel):
    """A vehicle record."""

    id: uuid.UUID
    workshop_id: uuid.UUID
    vin: str
    plate: Optional[str]
    manufacturer: str
    model: str
    nickname: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DeviceIn(BaseModel):
    """Body of ``POST /v3/vehicles/{id}/devices``."""

    label: str = Field(min_length=1, max_length=100)


class DeviceOut(BaseModel):
    """A device credential (no secret)."""

    id: uuid.UUID
    vehicle_id: uuid.UUID
    label: str
    created_at: datetime
    last_seen_at: Optional[datetime]
    revoked_at: Optional[datetime]

    model_config = {"from_attributes": True}


class DeviceCreatedOut(DeviceOut):
    """Device credential with the plaintext token (returned exactly once)."""

    token: str
