"""API-level tests for log upload, device upload, VIN check, listing and
download (PROD-05).  Real Postgres + a temp directory as the storage root.

Author: Xiangzhu Yan
"""

import hashlib
import pathlib
import uuid
from typing import Dict, Tuple

import pytest

from tests.conftest import register_and_login, requires_db

pytestmark = requires_db

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
TSV_OK = (FIXTURES / "jetson_tsv_ok.tsv").read_bytes()
TSV_OTHER = (FIXTURES / "jetson_tsv_other_vin.tsv").read_bytes()
YAMAHA = (FIXTURES / "yamaha_dual.csv").read_bytes()
MAXLOG_HIACE = (FIXTURES / "obd_maxlog_hiace.csv").read_bytes()
MAXLOG_NO_VIN = (FIXTURES / "obd_maxlog_no_vin.csv").read_bytes()
_VEHICLE = {"vin": "JHMGK5830HX202404", "manufacturer": "Honda", "model": "Jazz"}
_OTHER = {"vin": "1HGCM82633A123456", "manufacturer": "Honda", "model": "Accord", "plate": "ZZ9999"}


@pytest.fixture(autouse=True)
def _storage_in_tmp(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Points the storage root at a fresh temp dir for every test."""
    from stf_v3.settings import settings

    monkeypatch.setattr(settings, "obd_log_storage_path", str(tmp_path))
    return tmp_path


def _file(name: str, data: bytes) -> Dict[str, Tuple[str, bytes, str]]:
    return {"file": (name, data, "application/octet-stream")}


async def _setup(client, workshop_with_codes):  # type: ignore[no-untyped-def]
    """Manager + technician + one vehicle; returns (wid, mgr, tech, vid)."""
    wid, codes = workshop_with_codes
    manager = await register_and_login(client, "alice", codes["manager"])
    tech = await register_and_login(client, "bob", codes["technician"])
    r = await client.post(f"/v3/workshops/{wid}/vehicles", headers=manager, json=_VEHICLE)
    assert r.status_code == 201, r.text
    return wid, manager, tech, r.json()["id"]


async def _conversations_count() -> int:
    from sqlalchemy import text

    from stf_v3.db import SessionLocal

    async with SessionLocal() as s:
        return (await s.execute(text("SELECT count(*) FROM diagnosis_conversations"))).scalar_one()


async def test_upload_tsv_and_yamaha_store_metadata_and_bytes(
    client, workshop_with_codes, _storage_in_tmp: pathlib.Path  # type: ignore[no-untyped-def]
) -> None:
    """Both formats → 201 with format/VIN/window; file lands under
    <root>/<vehicle_id>/<log_id>.<ext>; no diagnosis is created."""
    _, _, tech, vid = await _setup(client, workshop_with_codes)
    r = await client.post(f"/v3/vehicles/{vid}/logs", headers=tech, files=_file("trip.tsv", TSV_OK))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["format"] == "tsv" and body["vin_from_log"] == "JHMGK5830HX202404"
    assert body["vin_mismatch"] is False and body["duplicate"] is False
    assert body["source"] == "web" and body["size_bytes"] == len(TSV_OK)
    assert body["recorded_start"].startswith("2025-07-23T14:42:16")
    stored = _storage_in_tmp / vid / f"{body['id']}.tsv"
    assert stored.read_bytes() == TSV_OK

    r = await client.post(f"/v3/vehicles/{vid}/logs", headers=tech, files=_file("y.csv", YAMAHA))
    assert r.status_code == 201, r.text
    assert r.json()["format"] == "yamaha" and r.json()["vin_from_log"] is None
    assert (_storage_in_tmp / vid / f"{r.json()['id']}.csv").exists()
    assert await _conversations_count() == 0


async def test_upload_maxlog_stores_and_checks_vin(
    client, workshop_with_codes, _storage_in_tmp: pathlib.Path  # type: ignore[no-untyped-def]
) -> None:
    """PROD-07 D3: the real Jetson format (OBD Maximum Data Log) is accepted
    (format ``maxlog``, VIN + window read, bytes stored verbatim, CHECK
    constraint admits it, download is text/csv); its VIN is cross-checked
    like a TSV's; the no-VIN Corolla shape is stored without a check."""
    _, manager, tech, vid = await _setup(client, workshop_with_codes)
    r = await client.post(f"/v3/vehicles/{vid}/logs", headers=tech, files=_file("hiace.csv", MAXLOG_HIACE))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["format"] == "maxlog" and body["vin_from_log"] == "JHMGK5830HX202404"
    assert body["recorded_start"].startswith("2026-06-22T15:39:54")
    assert body["recorded_end"].startswith("2026-06-22T15:41:19")
    assert (_storage_in_tmp / vid / f"{body['id']}.csv").read_bytes() == MAXLOG_HIACE
    r = await client.get(f"/v3/logs/{body['id']}/raw", headers=tech)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.content == MAXLOG_HIACE

    r = await client.post(f"/v3/vehicles/{vid}/logs", headers=tech, files=_file("nv.csv", MAXLOG_NO_VIN))
    assert r.status_code == 201 and r.json()["format"] == "maxlog" and r.json()["vin_from_log"] is None

    # same maxlog carrying the Hiace VIN under another car → 422 vin_mismatch
    r = await client.post(f"/v3/workshops/{workshop_with_codes[0]}/vehicles", headers=manager, json=_OTHER)
    other = r.json()["id"]
    r = await client.post(f"/v3/vehicles/{other}/logs", headers=tech, files=_file("hiace.csv", MAXLOG_HIACE))
    assert r.status_code == 422 and r.json()["code"] == "vin_mismatch"


async def test_unsupported_format_is_422_and_nothing_stored(
    client, workshop_with_codes, _storage_in_tmp: pathlib.Path  # type: ignore[no-untyped-def]
) -> None:
    """A JSON file → 422 unsupported_format; disk and table stay empty."""
    _, _, tech, vid = await _setup(client, workshop_with_codes)
    r = await client.post(f"/v3/vehicles/{vid}/logs", headers=tech, files=_file("x.json", b'{"a":1}'))
    assert r.status_code == 422 and r.json()["code"] == "unsupported_format"
    assert not list(_storage_in_tmp.rglob("*"))
    r = await client.get(f"/v3/vehicles/{vid}/logs", headers=tech)
    assert r.json() == []


async def test_duplicate_upload_returns_200_existing_and_writes_nothing(
    client, workshop_with_codes, _storage_in_tmp: pathlib.Path  # type: ignore[no-untyped-def]
) -> None:
    """Same bytes twice for one vehicle → 200 + duplicate=true, same id,
    exactly one file on disk."""
    _, _, tech, vid = await _setup(client, workshop_with_codes)
    first = await client.post(f"/v3/vehicles/{vid}/logs", headers=tech, files=_file("a.tsv", TSV_OK))
    second = await client.post(f"/v3/vehicles/{vid}/logs", headers=tech, files=_file("b.tsv", TSV_OK))
    assert second.status_code == 200, second.text
    assert second.json()["duplicate"] is True
    assert second.json()["id"] == first.json()["id"]
    assert len(list(_storage_in_tmp.rglob("*.tsv"))) == 1


async def test_file_too_large_is_413(
    client, workshop_with_codes, monkeypatch: pytest.MonkeyPatch  # type: ignore[no-untyped-def]
) -> None:
    """Uploads above the configured ceiling are refused before sniffing."""
    from stf_v3.settings import settings

    monkeypatch.setattr(settings, "max_upload_bytes", 1000)
    _, _, tech, vid = await _setup(client, workshop_with_codes)
    r = await client.post(f"/v3/vehicles/{vid}/logs", headers=tech, files=_file("a.tsv", TSV_OK))
    assert r.status_code == 413 and r.json()["code"] == "file_too_large"


async def test_non_member_cannot_upload_list_or_download(
    client, workshop_with_codes  # type: ignore[no-untyped-def]
) -> None:
    """Outside the workshop every log endpoint answers 404 (never 403)."""
    _, _, tech, vid = await _setup(client, workshop_with_codes)
    log_id = (await client.post(f"/v3/vehicles/{vid}/logs", headers=tech, files=_file("a.tsv", TSV_OK))).json()["id"]
    from stf_v3.db import SessionLocal
    from stf_v3.workshops import service

    async with SessionLocal() as s:
        other = await service.create_workshop(s, "Other")
        code = (await service.issue_invite_codes(s, other.id, "manager", 1, None))[0].code
    outsider = await register_and_login(client, "eve", code)
    assert (await client.post(f"/v3/vehicles/{vid}/logs", headers=outsider, files=_file("a.tsv", TSV_OK))).status_code == 404
    assert (await client.get(f"/v3/vehicles/{vid}/logs", headers=outsider)).status_code == 404
    assert (await client.get(f"/v3/logs/{log_id}", headers=outsider)).status_code == 404
    assert (await client.get(f"/v3/logs/{log_id}/raw", headers=outsider)).status_code == 404
    assert (await client.get(f"/v3/logs/{uuid.uuid4()}", headers=tech)).status_code == 404


async def test_vin_mismatch_is_rejected_and_names_the_other_vehicle(
    client, workshop_with_codes, _storage_in_tmp: pathlib.Path  # type: ignore[no-untyped-def]
) -> None:
    """Decision D2: a log whose VIN differs from the vehicle's is refused
    (422 vin_mismatch), nothing stored; the detail names the registered
    vehicle that carries the file's VIN, if any."""
    wid, manager, tech, vid = await _setup(client, workshop_with_codes)
    r = await client.post(f"/v3/vehicles/{vid}/logs", headers=tech, files=_file("o.tsv", TSV_OTHER))
    assert r.status_code == 422 and r.json()["code"] == "vin_mismatch", r.text
    assert "not registered" in r.json()["detail"]
    assert not list(_storage_in_tmp.rglob("*.tsv"))

    other_id = (await client.post(f"/v3/workshops/{wid}/vehicles", headers=manager, json=_OTHER)).json()["id"]
    r = await client.post(f"/v3/vehicles/{vid}/logs", headers=tech, files=_file("o.tsv", TSV_OTHER))
    assert r.status_code == 422 and other_id in r.json()["detail"] and "ZZ9999" in r.json()["detail"]
    # The same file under the right vehicle is accepted.
    r = await client.post(f"/v3/vehicles/{other_id}/logs", headers=tech, files=_file("o.tsv", TSV_OTHER))
    assert r.status_code == 201 and r.json()["vin_mismatch"] is False
    assert (await client.get(f"/v3/vehicles/{vid}/logs", headers=tech)).json() == []


async def test_device_upload_binds_vehicle_by_token(
    client, workshop_with_codes  # type: ignore[no-untyped-def]
) -> None:
    """A valid device token uploads without login (source=device,
    device_id set, uploaded_by null); missing/bogus/revoked → 401."""
    _, manager, tech, vid = await _setup(client, workshop_with_codes)
    r = await client.post(f"/v3/vehicles/{vid}/devices", headers=manager, json={"label": "Jetson"})
    device_id, token = r.json()["id"], r.json()["token"]

    r = await client.post("/v3/ingest/device", headers={"X-Device-Token": token}, files=_file("trip.tsv", TSV_OK))
    assert r.status_code == 201, r.text
    assert r.json()["source"] == "device" and r.json()["device_id"] == device_id
    assert r.json()["uploaded_by"] is None and r.json()["vehicle_id"] == vid
    r = await client.post("/v3/ingest/device", headers={"X-Device-Token": token}, files=_file("trip.tsv", TSV_OK))
    assert r.status_code == 200 and r.json()["duplicate"] is True

    assert (await client.post("/v3/ingest/device", files=_file("t.tsv", TSV_OK))).status_code == 401
    r = await client.post("/v3/ingest/device", headers={"X-Device-Token": "bogus"}, files=_file("t.tsv", TSV_OK))
    assert r.status_code == 401 and r.json()["code"] == "device_token_invalid"
    assert (await client.delete(f"/v3/devices/{device_id}", headers=manager)).status_code == 204
    r = await client.post("/v3/ingest/device", headers={"X-Device-Token": token}, files=_file("y.csv", YAMAHA))
    assert r.status_code == 401
    listed = (await client.get(f"/v3/vehicles/{vid}/logs", headers=tech)).json()
    assert len(listed) == 1 and listed[0]["source"] == "device"


async def test_device_upload_rejects_vin_mismatch_too(
    client, workshop_with_codes  # type: ignore[no-untyped-def]
) -> None:
    """A device moved to another car without a new token cannot pollute
    the original vehicle's history (D2 applies to both entry points)."""
    _, manager, _, vid = await _setup(client, workshop_with_codes)
    token = (await client.post(f"/v3/vehicles/{vid}/devices", headers=manager, json={"label": "J"})).json()["token"]
    r = await client.post("/v3/ingest/device", headers={"X-Device-Token": token}, files=_file("o.tsv", TSV_OTHER))
    assert r.status_code == 422 and r.json()["code"] == "vin_mismatch"


async def test_list_metadata_and_download_round_trip(
    client, workshop_with_codes  # type: ignore[no-untyped-def]
) -> None:
    """List is newest-first, metadata matches, raw download returns the
    exact bytes with the original filename."""
    _, _, tech, vid = await _setup(client, workshop_with_codes)
    a = (await client.post(f"/v3/vehicles/{vid}/logs", headers=tech, files=_file("first.tsv", TSV_OK))).json()
    b = (await client.post(f"/v3/vehicles/{vid}/logs", headers=tech, files=_file("second.csv", YAMAHA))).json()
    r = await client.get(f"/v3/vehicles/{vid}/logs", headers=tech)
    assert [x["id"] for x in r.json()] == [b["id"], a["id"]]
    r = await client.get(f"/v3/vehicles/{vid}/logs", headers=tech, params={"limit": 1, "offset": 1})
    assert [x["id"] for x in r.json()] == [a["id"]]
    r = await client.get(f"/v3/logs/{a['id']}", headers=tech)
    assert r.status_code == 200 and r.json()["original_filename"] == "first.tsv"
    assert r.json()["sha256"] == hashlib.sha256(TSV_OK).hexdigest()
    r = await client.get(f"/v3/logs/{a['id']}/raw", headers=tech)
    assert r.status_code == 200 and r.content == TSV_OK
    assert 'filename="first.tsv"' in r.headers["content-disposition"]
    r = await client.get(f"/v3/logs/{b['id']}/raw", headers=tech)
    assert r.content == YAMAHA and r.headers["content-type"].startswith("text/csv")


async def test_logs_of_deleted_vehicle_are_hidden_but_files_kept(
    client, workshop_with_codes, _storage_in_tmp: pathlib.Path  # type: ignore[no-untyped-def]
) -> None:
    """Soft-deleting the vehicle makes its logs 404; bytes stay on disk."""
    _, manager, tech, vid = await _setup(client, workshop_with_codes)
    log_id = (await client.post(f"/v3/vehicles/{vid}/logs", headers=tech, files=_file("a.tsv", TSV_OK))).json()["id"]
    assert (await client.delete(f"/v3/vehicles/{vid}", headers=manager)).status_code == 204
    assert (await client.get(f"/v3/vehicles/{vid}/logs", headers=tech)).status_code == 404
    assert (await client.get(f"/v3/logs/{log_id}", headers=tech)).status_code == 404
    assert (await client.get(f"/v3/logs/{log_id}/raw", headers=tech)).status_code == 404
    assert (_storage_in_tmp / vid / f"{log_id}.tsv").exists()


async def test_every_log_joins_to_a_vehicle(
    client, workshop_with_codes  # type: ignore[no-untyped-def]
) -> None:
    """Data-ownership invariant (D8): no obd_logs row without a vehicle."""
    _, _, tech, vid = await _setup(client, workshop_with_codes)
    await client.post(f"/v3/vehicles/{vid}/logs", headers=tech, files=_file("a.tsv", TSV_OK))
    await client.post(f"/v3/vehicles/{vid}/logs", headers=tech, files=_file("b.csv", YAMAHA))
    from sqlalchemy import text

    from stf_v3.db import SessionLocal

    async with SessionLocal() as s:
        orphans = (await s.execute(text(
            "SELECT count(*) FROM obd_logs l LEFT JOIN vehicles v ON v.id = l.vehicle_id WHERE v.id IS NULL"
        ))).scalar_one()
        total = (await s.execute(text("SELECT count(*) FROM obd_logs"))).scalar_one()
    assert total == 2 and orphans == 0


async def test_device_rejections_are_logged_and_refresh_last_seen(
    client, workshop_with_codes  # type: ignore[no-untyped-def]
) -> None:
    """PROD-07 T-9 (FM-19, FM-3, FM-29): every refused device upload emits
    one ``ingest.rejected`` event naming reason, device, filename, size and
    the first line; and ``last_seen_at`` moves even when the file is
    refused — so "recently active" proves the token was used, not that a
    log was stored (the install guide says so)."""
    import structlog
    from stf_v3.settings import settings

    _, manager, _, vid = await _setup(client, workshop_with_codes)
    r = await client.post(f"/v3/vehicles/{vid}/devices", headers=manager, json={"label": "J"})
    device_id, token = r.json()["id"], r.json()["token"]
    hdr = {"X-Device-Token": token}
    seen_before = (await client.get(f"/v3/vehicles/{vid}/devices", headers=manager)).json()[0]["last_seen_at"]

    big = b"OBD Data Log\n" + b"x" * (settings.max_upload_bytes + 1)
    cases = [("o.tsv", TSV_OTHER, 422, "vin_mismatch"),
             ("x.json", b'{"a": 1}\nmore', 422, "unsupported_format"),
             ("big.tsv", big, 413, "file_too_large")]
    with structlog.testing.capture_logs() as logs:
        for name, data, status, code in cases:
            r = await client.post("/v3/ingest/device", headers=hdr, files=_file(name, data))
            assert r.status_code == status and r.json()["code"] == code, r.text
    rejected = [e for e in logs if e["event"] == "ingest.rejected"]
    assert [(e["reason"], e["status"]) for e in rejected] == [(c[3], c[2]) for c in cases]
    for event, (name, data, _, _) in zip(rejected, cases):
        assert event["source"] == "device" and event["device_id"] == device_id
        assert event["vehicle_id"] == vid and event["filename"] == name
        assert event["size_bytes"] == len(data)
        assert event["first_line"] == data[:120].split(b"\n", 1)[0].decode()
        assert token not in str(event)

    seen_after = (await client.get(f"/v3/vehicles/{vid}/devices", headers=manager)).json()[0]["last_seen_at"]
    assert seen_before is None and seen_after is not None
    listed = (await client.get(f"/v3/vehicles/{vid}/logs", headers=manager)).json()
    assert listed == []   # nothing stored despite three "active" attempts
