"""Pydantic schemas for auth endpoints.

Author: Xiangzhu Yan
"""

import uuid
from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field


class RegisterIn(BaseModel):
    """Body of ``POST /v3/auth/register``."""

    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9._-]+$")
    password: str = Field(min_length=10, max_length=256)
    invite_code: str = Field(min_length=1, max_length=32)
    email: Optional[EmailStr] = None
    display_name: Optional[str] = Field(default=None, max_length=100)


class MembershipOut(BaseModel):
    """One workshop membership of the current user."""

    workshop_id: uuid.UUID
    workshop_name: str
    role: str


class UserOut(BaseModel):
    """Response of ``GET /v3/users/me`` and registration."""

    id: uuid.UUID
    username: str
    email: Optional[str] = None
    display_name: Optional[str] = None
    memberships: List[MembershipOut]


class InviteCodesIn(BaseModel):
    """Body of ``POST /v3/workshops/{wid}/invite-codes``."""

    role: str = Field(pattern=r"^(manager|technician)$")
    count: int = Field(default=1, ge=1, le=50)
    expires_in_days: Optional[int] = Field(default=None, ge=1, le=365)


class InviteCodeOut(BaseModel):
    """One invite code (plaintext shown only at creation)."""

    code: str
    role: str
    used_by: Optional[uuid.UUID] = None
    used_at: Optional[str] = None
    expires_at: Optional[str] = None
