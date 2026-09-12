"""fastapi-users wiring: user database, manager, JWT backend, dependencies.

Login is by ``username`` (review default ②), so ``authenticate`` is
overridden to look users up by username instead of email.

Author: Xiangzhu Yan
"""

import uuid
from typing import AsyncIterator, Optional

from fastapi import Depends
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from stf_v3.auth.models import User
from stf_v3.db import get_session
from stf_v3.settings import settings


class UserDatabase(SQLAlchemyUserDatabase[User, uuid.UUID]):
    """SQLAlchemy user store with username lookup."""

    async def get_by_username(self, username: str) -> Optional[User]:
        """Returns the user with this username, or None.

        Args:
            username: Login name (case-sensitive).
        """
        stmt = select(User).where(User.username == username)
        return (await self.session.execute(stmt)).scalar_one_or_none()


async def get_user_db(
    session: AsyncSession = Depends(get_session),
) -> AsyncIterator[UserDatabase]:
    """Dependency yielding the user store bound to the request session."""
    yield UserDatabase(session, User)


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    """User manager with username-based authentication."""

    reset_password_token_secret = settings.jwt_secret
    verification_token_secret = settings.jwt_secret

    async def authenticate(
        self, credentials: OAuth2PasswordRequestForm
    ) -> Optional[User]:
        """Authenticates by username + password.

        Args:
            credentials: OAuth2 password form (``username`` field holds the
                login name).

        Returns:
            The user on success, else None.
        """
        user_db: UserDatabase = self.user_db  # type: ignore[assignment]
        user = await user_db.get_by_username(credentials.username)
        if user is None:
            # Burn the same time as a real check to avoid user enumeration.
            self.password_helper.hash(credentials.password)
            return None
        verified, updated_hash = self.password_helper.verify_and_update(
            credentials.password, user.hashed_password
        )
        if not verified:
            return None
        if updated_hash is not None:
            await self.user_db.update(user, {"hashed_password": updated_hash})
        return user


async def get_user_manager(
    user_db: UserDatabase = Depends(get_user_db),
) -> AsyncIterator[UserManager]:
    """Dependency yielding the user manager."""
    yield UserManager(user_db)


bearer_transport = BearerTransport(tokenUrl="/v3/auth/login")


def get_jwt_strategy() -> JWTStrategy[User, uuid.UUID]:
    """Builds the JWT strategy from settings."""
    return JWTStrategy(
        secret=settings.jwt_secret,
        lifetime_seconds=settings.jwt_lifetime_seconds,
    )


auth_backend = AuthenticationBackend(
    name="jwt", transport=bearer_transport, get_strategy=get_jwt_strategy
)

fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])

current_user = fastapi_users.current_user(active=True)
