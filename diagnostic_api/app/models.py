"""Pydantic models for diagnostic API.

Author: Li-Ta Hsu
Date: January 2026

These models define shared response schemas for the diagnostic API.
"""

from datetime import datetime, timezone
from typing import Dict

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Health check response model.

    Returns:
        status: Health status ("healthy" or "unhealthy")
        timestamp: ISO timestamp of the health check
        version: API version string
    """

    status: str = Field(..., description="Health status")
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Check timestamp",
    )
    version: str = Field(..., description="API version")
    services: Dict[str, str] = Field(
        default_factory=dict, description="Service connectivity status"
    )
