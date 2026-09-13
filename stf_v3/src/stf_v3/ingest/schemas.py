"""Pydantic schemas for the ingest endpoints.

Author: Xiangzhu Yan
"""

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ObdLogOut(BaseModel):
    """Metadata of one stored log (never the bytes)."""

    id: uuid.UUID
    vehicle_id: uuid.UUID
    format: str
    size_bytes: int
    sha256: str
    original_filename: Optional[str]
    source: str
    device_id: Optional[uuid.UUID]
    uploaded_by: Optional[uuid.UUID]
    vin_from_log: Optional[str]
    vin_mismatch: bool
    recorded_start: Optional[datetime]
    recorded_end: Optional[datetime]
    uploaded_at: datetime

    model_config = {"from_attributes": True}


class UploadOut(ObdLogOut):
    """Upload response: the stored record plus whether it already existed."""

    duplicate: bool = False
