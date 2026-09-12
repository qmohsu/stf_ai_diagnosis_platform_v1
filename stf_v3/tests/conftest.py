"""Shared fixtures.

API-level tests need ``STF_V3_TEST_DATABASE_URL`` (a migrated or empty
throwaway database); without it they are skipped and only offline tests run.
The env var is exported as ``STF_V3_DATABASE_URL`` *before* ``stf_v3`` is
imported so the app engine binds to the test database.

Author: Xiangzhu Yan
"""

import os
import pathlib
from typing import AsyncIterator, Dict, Tuple

import pytest

_TEST_URL = os.environ.get("STF_V3_TEST_DATABASE_URL")
if _TEST_URL:
    os.environ["STF_V3_DATABASE_URL"] = _TEST_URL
    os.environ.setdefault("STF_V3_ENVIRONMENT", "test")

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_BUSINESS_TABLES = (
    "audit_events", "reports", "messages", "diagnosis_conversations",
    "obd_logs", "vehicle_devices", "vehicles", "invite_codes",
    "memberships", "workshops", "manuals", "users",
)

requires_db = pytest.mark.skipif(
    not _TEST_URL, reason="STF_V3_TEST_DATABASE_URL not set"
)


@pytest.fixture(scope="session")
def migrated_db() -> str:
    """Ensures the test database is at Alembic head once per session."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_ROOT / "alembic"))
    command.upgrade(cfg, "head")
    return _TEST_URL or ""


@pytest.fixture()
async def clean_db(migrated_db: str) -> AsyncIterator[None]:
    """Truncates every business table before a test (procrastinate kept)."""
    from sqlalchemy import text

    from stf_v3.db import engine

    async with engine.begin() as conn:
        await conn.execute(
            text("TRUNCATE " + ", ".join(_BUSINESS_TABLES) + " RESTART IDENTITY CASCADE")
        )
    yield


@pytest.fixture()
async def client(clean_db: None) -> AsyncIterator["httpx.AsyncClient"]:  # noqa: F821
    """ASGI test client bound to the app (no network)."""
    import httpx

    from stf_v3.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture()
async def workshop_with_codes(clean_db: None) -> Tuple[str, Dict[str, str]]:
    """Creates one workshop with one manager + one technician invite code.

    Returns:
        (workshop_id, {"manager": code, "technician": code}).
    """
    from stf_v3.db import SessionLocal
    from stf_v3.workshops import service

    async with SessionLocal() as session:
        workshop = await service.create_workshop(session, "Test Workshop")
        m = await service.issue_invite_codes(session, workshop.id, "manager", 1, None)
        t = await service.issue_invite_codes(session, workshop.id, "technician", 1, None)
        return str(workshop.id), {"manager": m[0].code, "technician": t[0].code}


async def register_and_login(
    client: "httpx.AsyncClient", username: str, code: str  # noqa: F821
) -> Dict[str, str]:
    """Registers with an invite code, logs in, returns auth headers."""
    pw = "Test-Pa55word-long"
    r = await client.post(
        "/v3/auth/register",
        json={"username": username, "password": pw, "invite_code": code},
    )
    assert r.status_code == 201, r.text
    r = await client.post("/v3/auth/login", data={"username": username, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
