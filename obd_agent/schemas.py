"""OBDSnapshot Pydantic v2 models.

Matches the contract defined in design_doc section 8.1.1.

``OBDSnapshot`` was originally designed as the payload for the
``/v1/telemetry/obd_snapshot`` edge ingestion endpoint, which was
never deployed (the snapshot transport was removed under APP-53
cleanup).  The schema lives on as the in-memory row model of the
log-parsing pipeline: ``log_parser.row_to_snapshot`` produces it
and ``log_summarizer.summarize_log_file`` consumes it on the
production ``POST /v2/obd/analyze`` path.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class AdapterInfo(BaseModel):
    """ELM327 adapter metadata."""

    type: str = Field(default="ELM327", description="Adapter chipset type")
    port: str = Field(..., description="Serial port or 'sim' for simulation")


class DTCEntry(BaseModel):
    """Single Diagnostic Trouble Code."""

    code: str = Field(
        ...,
        description="OBD-II DTC code, e.g. P0301",
        examples=["P0301", "C0035", "B0100", "U0073"],
    )
    desc: str = Field(default="", description="Human-readable description")

    @field_validator("code")
    @classmethod
    def validate_dtc_code(cls, v: str) -> str:
        if not re.match(r"^[PCBU][0-9A-F]{4}$", v):
            raise ValueError(
                f"DTC code must match ^[PCBU][0-9A-F]{{4}}$, got '{v}'"
            )
        return v


class PIDValue(BaseModel):
    """A single OBD-II PID reading."""

    value: float = Field(..., description="Numeric PID value")
    unit: str = Field(..., description="Engineering unit, e.g. 'rpm'")


# ---------------------------------------------------------------------------
# Top-level snapshot
# ---------------------------------------------------------------------------


class OBDSnapshot(BaseModel):
    """Edge-to-cloud OBD-II snapshot payload.

    Additive contract: new fields may be added; existing fields remain
    backward-compatible.  See design_doc 8.1.1.

    .. note:: APP-54
       Per the experimental-vehicle / internal-development policy,
       ``vehicle_id`` is now expected to be a raw VIN (17-char ISO 3779)
       or any free-form pseudonymous label.  The previous
       ``reject_raw_vin`` validator has been removed.
    """

    vehicle_id: str = Field(
        ...,
        description=(
            "Vehicle identifier: a raw 17-char VIN (ISO 3779) or any "
            "free-form label.  Examples: '1HGCM82633A123456', 'V-SIM-001'."
        ),
        examples=["1HGCM82633A123456", "V-SIM-001"],
    )
    ts: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Snapshot timestamp in UTC",
    )
    adapter: AdapterInfo
    dtc: List[DTCEntry] = Field(default_factory=list)
    freeze_frame: Dict[str, PIDValue] = Field(default_factory=dict)
    supported_pids: List[str] = Field(default_factory=list)
    baseline_pids: Dict[str, PIDValue] = Field(default_factory=dict)

    model_config = {"extra": "allow"}
