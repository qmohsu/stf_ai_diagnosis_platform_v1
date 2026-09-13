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
import hashlib
import pathlib
import sys
import time
from typing import Any, Dict, List, Optional

import httpx

_FAILS: List[str] = []
# Trimmed sample logs with fake VINs (also baked into the image under
# /app/tests/fixtures, so the script works inside the container).
_FIXTURES = pathlib.Path(__file__).resolve().parents[1] / "tests" / "fixtures"


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
        r = c.get("/v3/health")   # /v3/ prefix so the same script works through nginx
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
        device_token = r.json().get("token", "") if r.status_code == 201 else ""

        r = c.get(f"/v3/vehicles/{vehicle_id}/devices", headers=_auth(token_t))
        _step("device list hides token", r.status_code == 200
              and "token" not in r.json()[0])

        # ---- PROD-05: upload / VIN check / device upload / download ----
        tsv_ok = (_FIXTURES / "jetson_tsv_ok.tsv").read_bytes()
        tsv_other = (_FIXTURES / "jetson_tsv_other_vin.tsv").read_bytes()
        yamaha = (_FIXTURES / "yamaha_dual.csv").read_bytes()
        r = c.post(f"/v3/vehicles/{vehicle_id}/logs", headers=_auth(token_t),
                   files={"file": ("trip.tsv", tsv_ok)})
        _step("technician uploads Jetson TSV (201, VIN read)", r.status_code == 201
              and r.json().get("vin_from_log") == "JHMGK5830HX202404", r.text[:120])
        log_id = r.json().get("id") if r.status_code == 201 else None
        r = c.post(f"/v3/vehicles/{vehicle_id}/logs", headers=_auth(token_t),
                   files={"file": ("y.csv", yamaha)})
        _step("upload Yamaha CSV (201, no VIN)", r.status_code == 201
              and r.json().get("format") == "yamaha", r.text[:120])
        r = c.post(f"/v3/vehicles/{vehicle_id}/logs", headers=_auth(token_t),
                   files={"file": ("x.json", b'{"a": 1}')})
        _step("unsupported format rejected (422)", r.status_code == 422
              and r.json().get("code") == "unsupported_format")
        r = c.post(f"/v3/vehicles/{vehicle_id}/logs", headers=_auth(token_t),
                   files={"file": ("again.tsv", tsv_ok)})
        _step("same file again → 200 duplicate", r.status_code == 200
              and r.json().get("duplicate") is True and r.json().get("id") == log_id)
        r = c.post(f"/v3/vehicles/{vehicle_id}/logs", headers=_auth(token_t),
                   files={"file": ("other.tsv", tsv_other)})
        _step("VIN mismatch rejected (422, D2)", r.status_code == 422
              and r.json().get("code") == "vin_mismatch", r.text[:120])
        r = c.post("/v3/ingest/device", headers={"X-Device-Token": device_token},
                   files={"file": ("device.csv", yamaha + b"# smoke device copy\n")})
        _step("device upload with token (201, source=device)", r.status_code == 201
              and r.json().get("source") == "device", r.text[:120])
        r = c.get(f"/v3/vehicles/{vehicle_id}/logs", headers=_auth(token_t))
        _step("log list shows 3 uploads", r.status_code == 200 and len(r.json()) == 3)
        if log_id:
            r = c.get(f"/v3/logs/{log_id}/raw", headers=_auth(token_t))
            _step("download returns identical bytes", r.status_code == 200
                  and hashlib.sha256(r.content).hexdigest()
                  == hashlib.sha256(tsv_ok).hexdigest())

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
        r = c.get(f"/v3/vehicles/{vehicle_id}/logs", headers=_auth(token_m))
        _step("deleted vehicle's logs hidden (404)", r.status_code == 404)

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
