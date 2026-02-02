"""OBDSnapshot Pydantic v2 models.

Matches the contract defined in design_doc section 8.1.1.
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

_RAW_VIN_PATTERN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")


class OBDSnapshot(BaseModel):
    """Edge-to-cloud OBD-II snapshot payload.

    Additive contract: new fields may be added; existing fields remain
    backward-compatible.  See design_doc 8.1.1.
    """

    vehicle_id: str = Field(
        ...,
        description="Pseudonymous vehicle identifier (NOT a raw VIN)",
        examples=["V-SIM-001"],
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

    # --- validators --------------------------------------------------------

    @field_validator("vehicle_id")
    @classmethod
    def reject_raw_vin(cls, v: str) -> str:
        """Reject 17-char alphanumeric strings that look like a raw VIN."""
        if _RAW_VIN_PATTERN.match(v):
            raise ValueError(
                "vehicle_id looks like a raw VIN (ISO 3779). "
                "Use a pseudonymous identifier instead."
            )
        return v
