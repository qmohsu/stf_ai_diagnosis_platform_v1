"""API-level tests for vehicles, devices and the authorization entry point.

Author: Xiangzhu Yan
"""

import uuid
from typing import Dict, Tuple

from tests.conftest import register_and_login, requires_db

pytestmark = requires_db

_VEHICLE = {"vin": "JHMGK5830HX202404", "manufacturer": "Honda", "model": "Jazz"}


async def _setup(client, workshop_with_codes):  # type: ignore[no-untyped-def]
    """Registers a manager and a technician; returns (wid, mgr, tech)."""
    workshop_id, codes = workshop_with_codes
    manager = await register_and_login(client, "alice", codes["manager"])
    tech = await register_and_login(client, "bob", codes["technician"])
    return workshop_id, manager, tech


async def test_create_list_get_vehicle(
    client, workshop_with_codes: Tuple[str, Dict[str, str]]  # type: ignore[no-untyped-def]
) -> None:
    """A technician can create a vehicle; it is listed and fetchable."""
    wid, _, tech = await _setup(client, workshop_with_codes)
    r = await client.post(f"/v3/workshops/{wid}/vehicles", headers=tech, json=_VEHICLE)
    assert r.status_code == 201, r.text
    vid = r.json()["id"]
    r = await client.get(f"/v3/workshops/{wid}/vehicles", headers=tech, params={"q": "JHMGK"})
    assert [v["id"] for v in r.json()] == [vid]
    r = await client.get(f"/v3/vehicles/{vid}", headers=tech)
    assert r.status_code == 200 and r.json()["vin"] == "JHMGK5830HX202404"


async def test_vin_unique_per_workshop_and_immutable(
    client, workshop_with_codes: Tuple[str, Dict[str, str]]  # type: ignore[no-untyped-def]
) -> None:
    """Duplicate VIN → 409; PATCH cannot change VIN (field ignored)."""
    wid, manager, _ = await _setup(client, workshop_with_codes)
    r = await client.post(f"/v3/workshops/{wid}/vehicles", headers=manager, json=_VEHICLE)
    vid = r.json()["id"]
    r = await client.post(f"/v3/workshops/{wid}/vehicles", headers=manager, json=_VEHICLE)
    assert r.status_code == 409 and r.json()["code"] == "vin_exists"
    r = await client.patch(f"/v3/vehicles/{vid}", headers=manager, json={"vin": "1HGCM82633A123456", "plate": "AB1234"})
    assert r.status_code == 200
    assert r.json()["vin"] == "JHMGK5830HX202404" and r.json()["plate"] == "AB1234"


async def test_non_member_gets_404_not_403(
    client, workshop_with_codes: Tuple[str, Dict[str, str]]  # type: ignore[no-untyped-def]
) -> None:
    """can_access_vehicle hides vehicles of other workshops entirely."""
    wid, manager, _ = await _setup(client, workshop_with_codes)
    vid = (await client.post(f"/v3/workshops/{wid}/vehicles", headers=manager, json=_VEHICLE)).json()["id"]
    from stf_v3.db import SessionLocal
    from stf_v3.workshops import service

    async with SessionLocal() as session:
        other = await service.create_workshop(session, "Other Workshop")
        code = (await service.issue_invite_codes(session, other.id, "manager", 1, None))[0].code
    outsider = await register_and_login(client, "carol", code)
    for method, url in (("get", f"/v3/vehicles/{vid}"), ("delete", f"/v3/vehicles/{vid}")):
        r = await getattr(client, method)(url, headers=outsider)
        assert r.status_code == 404, (method, r.text)
    r = await client.get(f"/v3/workshops/{wid}/vehicles", headers=outsider)
    assert r.status_code == 404


async def test_manager_only_delete_and_devices(
    client, workshop_with_codes: Tuple[str, Dict[str, str]]  # type: ignore[no-untyped-def]
) -> None:
    """Technicians cannot delete vehicles or issue device credentials;
    managers can; the token is shown once and never listed."""
    wid, manager, tech = await _setup(client, workshop_with_codes)
    vid = (await client.post(f"/v3/workshops/{wid}/vehicles", headers=manager, json=_VEHICLE)).json()["id"]
    assert (await client.post(f"/v3/vehicles/{vid}/devices", headers=tech, json={"label": "J"})).status_code == 403
    r = await client.post(f"/v3/vehicles/{vid}/devices", headers=manager, json={"label": "Jetson #1"})
    assert r.status_code == 201 and len(r.json()["token"]) >= 40
    device_id = r.json()["id"]
    listed = (await client.get(f"/v3/vehicles/{vid}/devices", headers=tech)).json()
    assert listed[0]["id"] == device_id and "token" not in listed[0]
    assert (await client.delete(f"/v3/vehicles/{vid}", headers=tech)).status_code == 403
    assert (await client.delete(f"/v3/devices/{device_id}", headers=manager)).status_code == 204
    assert (await client.delete(f"/v3/vehicles/{vid}", headers=manager)).status_code == 204
    assert (await client.get(f"/v3/vehicles/{vid}", headers=manager)).status_code == 404
    assert (await client.delete(f"/v3/devices/{uuid.uuid4()}", headers=manager)).status_code == 404


async def test_device_token_resolves_to_vehicle_until_revoked(
    client, workshop_with_codes: Tuple[str, Dict[str, str]]  # type: ignore[no-untyped-def]
) -> None:
    """resolve_device_token (used by PROD-05 ingest) maps token → vehicle
    and stops after revocation."""
    wid, manager, _ = await _setup(client, workshop_with_codes)
    vid = (await client.post(f"/v3/workshops/{wid}/vehicles", headers=manager, json=_VEHICLE)).json()["id"]
    r = await client.post(f"/v3/vehicles/{vid}/devices", headers=manager, json={"label": "J"})
    token, device_id = r.json()["token"], r.json()["id"]
    from stf_v3.db import SessionLocal
    from stf_v3.vehicles import service

    async with SessionLocal() as session:
        resolved = await service.resolve_device_token(session, token)
        assert resolved is not None and str(resolved[1].id) == vid
        assert await service.resolve_device_token(session, "not-a-token") is None
    await client.delete(f"/v3/devices/{device_id}", headers=manager)
    async with SessionLocal() as session:
        assert await service.resolve_device_token(session, token) is None
