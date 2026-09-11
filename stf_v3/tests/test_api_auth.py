"""API-level tests for registration, login and workshop endpoints.

Author: Xiangzhu Yan
"""

from typing import Dict, Tuple

import pytest

from tests.conftest import register_and_login, requires_db

pytestmark = requires_db


async def test_register_with_invalid_code_is_422(client) -> None:  # type: ignore[no-untyped-def]
    """An unknown invite code is rejected with code invite_code_invalid."""
    r = await client.post(
        "/v3/auth/register",
        json={"username": "nobody", "password": "Test-Pa55word-long", "invite_code": "nope"},
    )
    assert r.status_code == 422
    assert r.json()["code"] == "invite_code_invalid"


async def test_register_creates_membership_and_burns_code(
    client, workshop_with_codes: Tuple[str, Dict[str, str]]  # type: ignore[no-untyped-def]
) -> None:
    """Registering joins the code's workshop with its role; the code is
    single-use."""
    workshop_id, codes = workshop_with_codes
    headers = await register_and_login(client, "alice", codes["manager"])
    me = (await client.get("/v3/users/me", headers=headers)).json()
    assert me["memberships"] == [
        {"workshop_id": workshop_id, "workshop_name": "Test Workshop", "role": "manager"}
    ]
    r = await client.post(
        "/v3/auth/register",
        json={"username": "bob", "password": "Test-Pa55word-long", "invite_code": codes["manager"]},
    )
    assert r.status_code == 422


async def test_duplicate_username_is_409(
    client, workshop_with_codes: Tuple[str, Dict[str, str]]  # type: ignore[no-untyped-def]
) -> None:
    """The same username cannot be registered twice."""
    _, codes = workshop_with_codes
    await register_and_login(client, "alice", codes["manager"])
    r = await client.post(
        "/v3/auth/register",
        json={"username": "alice", "password": "Test-Pa55word-long", "invite_code": codes["technician"]},
    )
    assert r.status_code == 409
    assert r.json()["code"] == "username_exists"


async def test_login_by_username_and_wrong_password(
    client, workshop_with_codes: Tuple[str, Dict[str, str]]  # type: ignore[no-untyped-def]
) -> None:
    """Login uses the username field; a wrong password yields 400."""
    _, codes = workshop_with_codes
    await register_and_login(client, "alice", codes["manager"])
    r = await client.post("/v3/auth/login", data={"username": "alice", "password": "wrong-wrong-1"})
    assert r.status_code == 400
    r = await client.get("/v3/users/me")
    assert r.status_code == 401


async def test_invite_codes_manager_only_and_masked_on_list(
    client, workshop_with_codes: Tuple[str, Dict[str, str]]  # type: ignore[no-untyped-def]
) -> None:
    """Only managers issue/list codes; plaintext appears only at creation."""
    workshop_id, codes = workshop_with_codes
    manager = await register_and_login(client, "alice", codes["manager"])
    tech = await register_and_login(client, "bob", codes["technician"])
    r = await client.post(
        f"/v3/workshops/{workshop_id}/invite-codes", headers=tech,
        json={"role": "technician", "count": 2},
    )
    assert r.status_code == 403
    r = await client.post(
        f"/v3/workshops/{workshop_id}/invite-codes", headers=manager,
        json={"role": "technician", "count": 2},
    )
    assert r.status_code == 201 and len(r.json()) == 2
    plain = r.json()[0]["code"]
    listed = (await client.get(f"/v3/workshops/{workshop_id}/invite-codes", headers=manager)).json()
    assert all(c["code"].endswith("…") for c in listed)
    assert plain not in [c["code"] for c in listed]


async def test_members_visible_to_members_only(
    client, workshop_with_codes: Tuple[str, Dict[str, str]]  # type: ignore[no-untyped-def]
) -> None:
    """A non-member gets 404 (existence hidden); members see the roster."""
    workshop_id, codes = workshop_with_codes
    manager = await register_and_login(client, "alice", codes["manager"])
    r = await client.get(f"/v3/workshops/{workshop_id}/members", headers=manager)
    assert r.status_code == 200 and r.json()[0]["username"] == "alice"
    import uuid
    r = await client.get(f"/v3/workshops/{uuid.uuid4()}/members", headers=manager)
    assert r.status_code == 404
