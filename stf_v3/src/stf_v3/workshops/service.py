"""Workshop services: memberships and invite codes (no auth imports —
``workshops`` is the lowest business layer).

Author: Xiangzhu Yan
"""

import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stf_v3.errors import forbidden, not_found
from stf_v3.workshops.models import InviteCode, Membership, Workshop


def new_invite_code() -> str:
    """Returns a random, URL-safe 16-character invite code."""
    return secrets.token_urlsafe(12)


async def memberships_of(
    session: AsyncSession, user_id: uuid.UUID
) -> List[Tuple[Membership, Workshop]]:
    """Returns (membership, workshop) pairs for a user, by workshop name."""
    stmt = (
        select(Membership, Workshop)
        .join(Workshop, Workshop.id == Membership.workshop_id)
        .where(Membership.user_id == user_id)
        .order_by(Workshop.name)
    )
    return [(m, w) for m, w in (await session.execute(stmt)).all()]


async def role_in_workshop(
    session: AsyncSession, user_id: uuid.UUID, workshop_id: uuid.UUID
) -> Optional[str]:
    """Returns the user's role in the workshop, or None if not a member."""
    stmt = select(Membership.role).where(
        Membership.user_id == user_id, Membership.workshop_id == workshop_id
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def require_role(
    session: AsyncSession,
    user_id: uuid.UUID,
    workshop_id: uuid.UUID,
    roles: Tuple[str, ...] = ("manager", "technician"),
) -> str:
    """Asserts membership (and optionally role) in a workshop.

    Args:
        session: Async session.
        user_id: Caller.
        workshop_id: Target workshop.
        roles: Accepted roles.

    Returns:
        The caller's role.

    Raises:
        ApiError: 404 when not a member (existence is not leaked); 403 when
            a member but not in ``roles``.
    """
    role = await role_in_workshop(session, user_id, workshop_id)
    if role is None:
        raise not_found("workshop_not_found", "Workshop not found")
    if role not in roles:
        raise forbidden("role_required", f"Requires role in {roles}")
    return role


async def list_members(
    session: AsyncSession, workshop_id: uuid.UUID
) -> List[Membership]:
    """Returns memberships of a workshop."""
    stmt = select(Membership).where(Membership.workshop_id == workshop_id)
    return list((await session.execute(stmt)).scalars())


async def issue_invite_codes(
    session: AsyncSession,
    workshop_id: uuid.UUID,
    role: str,
    count: int,
    created_by: Optional[uuid.UUID],
    expires_in_days: Optional[int] = None,
) -> List[InviteCode]:
    """Creates ``count`` invite codes for a workshop (committed)."""
    expires_at = (
        datetime.now(timezone.utc) + timedelta(days=expires_in_days)
        if expires_in_days
        else None
    )
    rows = [
        InviteCode(
            code=new_invite_code(),
            workshop_id=workshop_id,
            role=role,
            created_by=created_by,
            expires_at=expires_at,
        )
        for _ in range(count)
    ]
    session.add_all(rows)
    await session.commit()
    return rows


async def list_invite_codes(
    session: AsyncSession, workshop_id: uuid.UUID
) -> List[InviteCode]:
    """Returns all invite codes of a workshop, newest first."""
    stmt = (
        select(InviteCode)
        .where(InviteCode.workshop_id == workshop_id)
        .order_by(InviteCode.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars())


async def lock_valid_invite_code(
    session: AsyncSession, code: str
) -> Optional[InviteCode]:
    """Locks and returns an unused, unexpired invite code, or None.

    The row lock (``FOR UPDATE``) serialises concurrent registrations with
    the same code inside the caller's transaction.
    """
    stmt = select(InviteCode).where(InviteCode.code == code).with_for_update()
    row = (await session.execute(stmt)).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if (
        row is None
        or row.used_by is not None
        or (row.expires_at is not None and row.expires_at < now)
    ):
        return None
    return row


async def create_workshop(session: AsyncSession, name: str) -> Workshop:
    """Creates a workshop (CLI use; Stage 1 has no admin UI)."""
    workshop = Workshop(name=name)
    session.add(workshop)
    await session.commit()
    await session.refresh(workshop)
    return workshop
