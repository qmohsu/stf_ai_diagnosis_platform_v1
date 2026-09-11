"""Auth + workshop endpoints (§3.1, §3.2 of the code design).

Workshop endpoints live here because they need ``current_user`` and the
``workshops`` layer sits below ``auth`` (import-linter layers contract).

Author: Xiangzhu Yan
"""

import uuid
from typing import List

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stf_v3.auth import service as auth_service
from stf_v3.auth.manager import auth_backend, current_user, fastapi_users
from stf_v3.auth.models import User
from stf_v3.auth.schemas import (
    InviteCodeOut,
    InviteCodesIn,
    MembershipOut,
    RegisterIn,
    UserOut,
)
from stf_v3.db import get_session
from stf_v3.workshops import service as workshops

router = APIRouter(prefix="/v3", tags=["auth"])

router.include_router(fastapi_users.get_auth_router(auth_backend), prefix="/auth")


async def _user_out(session: AsyncSession, user: User) -> UserOut:
    """Builds the ``UserOut`` view incl. memberships."""
    pairs = await workshops.memberships_of(session, user.id)
    return UserOut(
        id=user.id,
        username=user.username,
        email=user.email,
        display_name=user.display_name,
        memberships=[
            MembershipOut(workshop_id=w.id, workshop_name=w.name, role=m.role)
            for m, w in pairs
        ],
    )


@router.post(
    "/auth/register", response_model=UserOut, status_code=status.HTTP_201_CREATED
)
async def register(
    body: RegisterIn, session: AsyncSession = Depends(get_session)
) -> UserOut:
    """Registers a user with an invite code; joins the code's workshop."""
    user = await auth_service.register_with_invite(session, body)
    return await _user_out(session, user)


@router.get("/users/me", response_model=UserOut)
async def me(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    """Returns the caller and their workshop memberships."""
    return await _user_out(session, user)


class WorkshopOut(MembershipOut):
    """A workshop the caller belongs to (same shape as a membership)."""


@router.get("/workshops", response_model=List[WorkshopOut], tags=["workshops"])
async def my_workshops(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> List[WorkshopOut]:
    """Lists the workshops the caller is a member of."""
    pairs = await workshops.memberships_of(session, user.id)
    return [
        WorkshopOut(workshop_id=w.id, workshop_name=w.name, role=m.role)
        for m, w in pairs
    ]


class MemberOut(MembershipOut):
    """A member of a workshop."""

    user_id: uuid.UUID
    username: str


@router.get(
    "/workshops/{workshop_id}/members",
    response_model=List[MemberOut],
    tags=["workshops"],
)
async def members(
    workshop_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> List[MemberOut]:
    """Lists members of a workshop (members only)."""
    await workshops.require_role(session, user.id, workshop_id)
    rows = await workshops.list_members(session, workshop_id)
    users = {
        u.id: u
        for u in (
            await session.execute(
                select(User).where(User.id.in_([m.user_id for m in rows]))
            )
        ).scalars()
    }
    name = (
        await session.execute(
            select(workshops.Workshop.name).where(
                workshops.Workshop.id == workshop_id
            )
        )
    ).scalar_one()
    return [
        MemberOut(
            workshop_id=workshop_id,
            workshop_name=name,
            role=m.role,
            user_id=m.user_id,
            username=users[m.user_id].username,
        )
        for m in rows
    ]


def _code_out(row: workshops.InviteCode, show_code: bool) -> InviteCodeOut:
    """Serialises an invite code (masking the value after creation)."""
    return InviteCodeOut(
        code=row.code if show_code else f"{row.code[:4]}…",
        role=row.role,
        used_by=row.used_by,
        used_at=row.used_at.isoformat() if row.used_at else None,
        expires_at=row.expires_at.isoformat() if row.expires_at else None,
    )


@router.post(
    "/workshops/{workshop_id}/invite-codes",
    response_model=List[InviteCodeOut],
    status_code=status.HTTP_201_CREATED,
    tags=["workshops"],
)
async def create_invite_codes(
    workshop_id: uuid.UUID,
    body: InviteCodesIn,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> List[InviteCodeOut]:
    """Issues invite codes (manager only).  Plaintext is returned once."""
    await workshops.require_role(session, user.id, workshop_id, ("manager",))
    rows = await workshops.issue_invite_codes(
        session, workshop_id, body.role, body.count, user.id, body.expires_in_days
    )
    return [_code_out(r, show_code=True) for r in rows]


@router.get(
    "/workshops/{workshop_id}/invite-codes",
    response_model=List[InviteCodeOut],
    tags=["workshops"],
)
async def get_invite_codes(
    workshop_id: uuid.UUID,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
) -> List[InviteCodeOut]:
    """Lists invite codes and their usage (manager only; values masked)."""
    await workshops.require_role(session, user.id, workshop_id, ("manager",))
    rows = await workshops.list_invite_codes(session, workshop_id)
    return [_code_out(r, show_code=False) for r in rows]
