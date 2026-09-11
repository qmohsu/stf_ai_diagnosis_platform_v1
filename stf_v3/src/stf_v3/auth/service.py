"""Auth service: invite-code registration.

One transaction: lock the invite code row, create the user, create the
membership, mark the code used.  Concurrent reuse of a code is blocked by
the row lock.

Author: Xiangzhu Yan
"""

from datetime import datetime, timezone

from fastapi_users.password import PasswordHelper
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from stf_v3.auth.models import User
from stf_v3.auth.schemas import RegisterIn
from stf_v3.errors import ApiError
from stf_v3.workshops import service as workshops
from stf_v3.workshops.models import Membership

password_helper = PasswordHelper()


async def register_with_invite(session: AsyncSession, body: RegisterIn) -> User:
    """Creates a user + membership from a valid invite code.

    Args:
        session: Request-scoped async session.
        body: Registration payload.

    Returns:
        The created ``User`` (committed).

    Raises:
        ApiError: 422 ``invite_code_invalid`` when the code is unknown, used
            or expired; 409 ``username_exists`` / ``email_exists``.
    """
    code = await workshops.lock_valid_invite_code(session, body.invite_code)
    if code is None:
        raise ApiError(422, "invite_code_invalid", "Invite code invalid or used")

    user = User(
        username=body.username,
        email=body.email,
        hashed_password=password_helper.hash(body.password),
        display_name=body.display_name,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        if "uq_users_email" in str(exc.orig):
            raise ApiError(409, "email_exists", "Email already registered")
        raise ApiError(409, "username_exists", "Username already taken")

    session.add(
        Membership(user_id=user.id, workshop_id=code.workshop_id, role=code.role)
    )
    code.used_by = user.id
    code.used_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(user)
    return user
