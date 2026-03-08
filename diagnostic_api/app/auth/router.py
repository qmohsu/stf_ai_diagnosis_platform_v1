"""Authentication endpoints: register and login.

Author: Li-Ta Hsu
Date: March 2026
"""

from __future__ import annotations

import re

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.auth.security import (
    create_access_token,
    get_password_hash,
    verify_password,
)
from app.models_db import User

logger = structlog.get_logger()

router = APIRouter()

_USERNAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")


class RegisterRequest(BaseModel):
    """Registration request body."""

    username: str
    password: str

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        """Validate username format.

        Args:
            v: Raw username string.

        Returns:
            Validated username.

        Raises:
            ValueError: If username is too short, too long,
                or contains invalid characters.
        """
        if len(v) < 3:
            raise ValueError(
                "Username must be at least 3 characters."
            )
        if len(v) > 50:
            raise ValueError(
                "Username must be at most 50 characters."
            )
        if not _USERNAME_RE.match(v):
            raise ValueError(
                "Username may only contain letters, digits, "
                "underscores, and hyphens."
            )
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        """Validate password length.

        Args:
            v: Raw password string.

        Returns:
            Validated password.

        Raises:
            ValueError: If password is too short or too long.
        """
        if len(v) < 8:
            raise ValueError(
                "Password must be at least 8 characters."
            )
        if len(v) > 128:
            raise ValueError(
                "Password must be at most 128 characters."
            )
        return v


# TODO(APP-29): Add rate limiting to /auth/register and
# /auth/login to prevent brute-force and mass-registration
# attacks.  Acceptable for Phase 1 local-only deployment.


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
)
async def register(
    body: RegisterRequest,
    db: Session = Depends(get_db),
) -> dict:
    """Create a new user with hashed password.

    Args:
        body: Username and password.
        db: Database session.

    Returns:
        Confirmation dict with username.

    Raises:
        HTTPException: 409 if username already exists.
    """
    user = User(
        username=body.username,
        hashed_password=get_password_hash(body.password),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Username already exists.",
        )

    logger.info("user_registered", username=body.username)
    return {
        "message": "User registered successfully",
        "username": body.username,
    }


@router.post(
    "/login",
    summary="Authenticate and receive a JWT token",
)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> dict:
    """Authenticate a user and return a JWT access token.

    Uses OAuth2 password form (``username`` + ``password``
    as ``application/x-www-form-urlencoded``).

    Args:
        form_data: OAuth2 password form with username and
            password fields.
        db: Database session.

    Returns:
        Dict with ``access_token`` and ``token_type``.

    Raises:
        HTTPException: 401 if credentials are invalid.
    """
    user = (
        db.query(User)
        .filter(User.username == form_data.username)
        .first()
    )
    if not user or not verify_password(
        form_data.password, user.hashed_password,
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(
        data={"sub": user.username},
    )
    logger.info("user_logged_in", username=user.username)
    return {
        "access_token": access_token,
        "token_type": "bearer",
    }
