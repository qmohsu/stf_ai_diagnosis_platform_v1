#!/usr/bin/env python3
"""End-to-end smoke test against a running V3 API (reusable, grows per ticket).

Walks the Stage 1 user flow as far as it exists and prints PASS/FAIL per
step.  Needs one unused MANAGER invite code (from
``scripts/create_workshop.py``).  Creates users named ``smoke_<ts>_*`` —
run it against a throwaway database or accept the residue.

Usage::

    python scripts/smoke_e2e.py --base-url http://127.0.0.1:8002 \
        --invite-code <manager code>

Author: Xiangzhu Yan
"""

import argparse
import sys
import time
from typing import Any, Dict, List, Optional

import httpx

_FAILS: List[str] = []


def _step(name: str, ok: bool, detail: str = "") -> None:
    """Records and prints one step outcome."""
    print(f"{'PASS' if ok else 'FAIL'}  {name}{('  -- ' + detail) if detail else ''}")
    if not ok:
        _FAILS.append(name)


def _login(client: httpx.Client, username: str, password: str) -> Optional[str]:
    """Logs in and returns the bearer token (or None)."""
    r = client.post(
        "/v3/auth/login", data={"username": username, "password": password}
    )
    return r.json().get("access_token") if r.status_code == 200 else None


def _auth(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def run(base_url: str, invite_code: str) -> int:
    """Runs the smoke flow.  Returns 0 if every step passed."""
    ts = int(time.time())
    pw = "SmokeTest-Pa55word"
    manager = f"smoke_{ts}_manager"
    tech = f"smoke_{ts}_tech"
    with httpx.Client(base_url=base_url, timeout=30) as c:
        r = c.get("/health")
        _step("health", r.status_code == 200 and r.json().get("db") == "ok", r.text[:80])

        r = c.post("/v3/auth/register", json={
            "username": manager, "password": pw, "invite_code": invite_code,
            "display_name": "Smoke Manager"})
        _step("register manager with invite code", r.status_code == 201, r.text[:120])
        if r.status_code != 201:
            return 1
        workshop_id = r.json()["memberships"][0]["workshop_id"]

        r = c.post("/v3/auth/register", json={
            "username": manager + "x", "password": pw, "invite_code": invite_code})
        _step("reused invite code rejected (422)", r.status_code == 422, r.text[:80])

        token_m = _login(c, manager, pw)
        _step("login manager by username", token_m is not None)
        if not token_m:
            return 1
        _step("wrong password rejected", _login(c, manager, "nope-nope-nope") is None)

        r = c.get("/v3/users/me", headers=_auth(token_m))
        _step("GET /users/me shows membership", r.status_code == 200
              and r.json()["memberships"][0]["role"] == "manager", r.text[:120])

        r = c.post(f"/v3/workshops/{workshop_id}/vehicles", headers=_auth(token_m),
                   json={"vin": "JHMGK5830HX202404", "manufacturer": "Honda",
                         "model": "Jazz", "plate": f"SM{ts % 10000}"})
        _step("create vehicle (VIN identity)", r.status_code == 201, r.text[:120])
        if r.status_code != 201:
            return 1
        vehicle_id = r.json()["id"]

        r = c.post(f"/v3/workshops/{workshop_id}/vehicles", headers=_auth(token_m),
                   json={"vin": "JHMGK5830HX202404", "manufacturer": "Honda", "model": "Jazz"})
        _step("duplicate VIN rejected (409)", r.status_code == 409, r.text[:80])

        r = c.post(f"/v3/workshops/{workshop_id}/vehicles", headers=_auth(token_m),
                   json={"vin": "BADVIN0000000000I", "manufacturer": "X", "model": "Y"})
        _step("invalid VIN rejected (422)", r.status_code == 422)

        r = c.patch(f"/v3/vehicles/{vehicle_id}", headers=_auth(token_m),
                    json={"plate": "NEWPLATE"})
        _step("change plate (label, not identity)", r.status_code == 200
              and r.json()["plate"] == "NEWPLATE")

        r = c.post(f"/v3/workshops/{workshop_id}/invite-codes", headers=_auth(token_m),
                   json={"role": "technician", "count": 1})
        _step("manager issues technician invite code", r.status_code == 201)
        tech_code = r.json()[0]["code"] if r.status_code == 201 else ""

        r = c.post("/v3/auth/register", json={
            "username": tech, "password": pw, "invite_code": tech_code})
        _step("register technician", r.status_code == 201, r.text[:80])
        token_t = _login(c, tech, pw) or ""
        _step("login technician", bool(token_t))

        r = c.get(f"/v3/workshops/{workshop_id}/vehicles", headers=_auth(token_t))
        _step("technician sees workshop vehicles", r.status_code == 200
              and any(v["id"] == vehicle_id for v in r.json()))

        r = c.post(f"/v3/vehicles/{vehicle_id}/devices", headers=_auth(token_t),
                   json={"label": "Jetson"})
        _step("technician cannot issue device credential (403)", r.status_code == 403)

        r = c.post(f"/v3/vehicles/{vehicle_id}/devices", headers=_auth(token_m),
                   json={"label": "Jetson #1"})
        _step("manager issues device credential (token shown once)",
              r.status_code == 201 and len(r.json().get("token", "")) > 20)
        device_id = r.json().get("id") if r.status_code == 201 else None

        r = c.get(f"/v3/vehicles/{vehicle_id}/devices", headers=_auth(token_t))
        _step("device list hides token", r.status_code == 200
              and "token" not in r.json()[0])

        r = c.delete(f"/v3/vehicles/{vehicle_id}", headers=_auth(token_t))
        _step("technician cannot delete vehicle (403)", r.status_code == 403)

        r = c.post(f"/v3/workshops/{workshop_id}/invite-codes", headers=_auth(token_t),
                   json={"role": "technician", "count": 1})
        _step("technician cannot issue invite codes (403)", r.status_code == 403)

        if device_id:
            r = c.delete(f"/v3/devices/{device_id}", headers=_auth(token_m))
            _step("manager revokes device", r.status_code == 204)

        r = c.delete(f"/v3/vehicles/{vehicle_id}", headers=_auth(token_m))
        _step("manager soft-deletes vehicle", r.status_code == 204)
        r = c.get(f"/v3/vehicles/{vehicle_id}", headers=_auth(token_m))
        _step("deleted vehicle is gone (404)", r.status_code == 404)

        r = c.get("/v3/openapi.json")
        _step("OpenAPI served", r.status_code == 200 and "/v3/auth/register" in r.text)

    print("SMOKE ALL PASS" if not _FAILS else f"SMOKE FAILED: {_FAILS}")
    return 0 if not _FAILS else 1


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--invite-code", required=True, help="unused manager code")
    args = parser.parse_args(argv)
    return run(args.base_url.rstrip("/"), args.invite_code)


if __name__ == "__main__":
    sys.exit(main())
