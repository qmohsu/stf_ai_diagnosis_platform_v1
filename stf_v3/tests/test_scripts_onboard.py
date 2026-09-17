"""Tests for ``scripts/onboard_first_workshop.py`` (PROD-07 T-10).

The VIN double-entry guard is offline; the database part runs against the
throwaway test database and proves the issued token works end to end.

Author: Xiangzhu Yan
"""

import importlib.util
import pathlib
from typing import Dict, List

import pytest

from tests.conftest import requires_db

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "onboard_first_workshop.py"
_spec = importlib.util.spec_from_file_location("onboard_first_workshop", _SCRIPT)
onboard = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
assert _spec is not None and _spec.loader is not None
_spec.loader.exec_module(onboard)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
VIN_A = "JHMGK5830HX202404"   # fake VIN used by every fixture
VIN_B = "1HGCM82633A123456"


def _reader(lines: List[str]):  # type: ignore[no-untyped-def]
    it = iter(lines)
    return lambda prompt: next(it)


def test_vin_must_be_typed_twice_and_match() -> None:
    """A mismatch or an invalid VIN raises before anything is written;
    matching entries are normalised to upper case."""
    spec = onboard.VehicleSpec.parse("Toyota|Hiace||")
    with pytest.raises(onboard.OnboardError, match="do not match"):
        onboard.collect_vins([spec], _reader([VIN_A, VIN_B]))
    with pytest.raises(onboard.OnboardError, match="not 17 chars"):
        onboard.collect_vins([spec], _reader(["ABC", "ABC"]))
    assert onboard.collect_vins([spec], _reader([VIN_A.lower(), VIN_A])) == [VIN_A]


def test_vehicle_spec_parsing() -> None:
    """``manufacturer|model`` is enough; plate / nickname optional."""
    s = onboard.VehicleSpec.parse("Toyota|Corolla")
    assert (s.manufacturer, s.model, s.plate, s.nickname) == ("Toyota", "Corolla", None, None)
    with pytest.raises(onboard.OnboardError):
        onboard.VehicleSpec.parse("Toyota")
    assert onboard.VehicleSpec.parse("Toyota|Hiace|AB 1234|van").label() == "Toyota Hiace (AB 1234)"


def test_env_file_text_has_the_three_keys() -> None:
    """The generated env file is exactly what the uploader reads."""
    text = onboard.env_file_text("https://x", "vid", "tok", "Toyota Hiace")
    assert "STF_V3_BASE_URL=https://x\n" in text
    assert "STF_V3_DEVICE_TOKEN=tok\n" in text and "STF_V3_VEHICLE_ID=vid\n" in text


@requires_db
async def test_onboard_creates_rows_and_token_uploads(
    client, clean_db, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str],  # type: ignore[no-untyped-def]
) -> None:
    """Workshop + codes + 2 vehicles + 2 tokens; VIN readback OK; the token
    uploads a fixture log via the device endpoint; rerun reuses rows and the
    report never prints a VIN; env files are written mode 600."""
    from stf_v3.db import SessionLocal

    specs = [onboard.VehicleSpec.parse("Toyota|Hiace|AB1234|"),
             onboard.VehicleSpec.parse("Toyota|Corolla||")]
    async with SessionLocal() as session:
        result = await onboard.onboard(session, "Onboard Test WS", 1, 1, specs, [VIN_A, VIN_B], "Jetson")
    assert result["created"] is True and len(result["codes"]) == 2
    vehicles: List[Dict[str, object]] = result["vehicles"]  # type: ignore[assignment]
    assert [v["vin_ok"] for v in vehicles] == [True, True]
    assert [v["existing"] for v in vehicles] == [False, False]

    # the Hiace token uploads the fixture log that carries VIN_A
    tsv = (FIXTURES / "jetson_tsv_ok.tsv").read_bytes()
    r = await client.post("/v3/ingest/device", headers={"X-Device-Token": vehicles[0]["token"]},
                          files={"file": ("trip.tsv", tsv, "application/octet-stream")})
    assert r.status_code == 201 and r.json()["vehicle_id"] == vehicles[0]["id"], r.text
    # ... and is refused under the Corolla token (VIN mismatch) — FM-4 evidence
    r = await client.post("/v3/ingest/device", headers={"X-Device-Token": vehicles[1]["token"]},
                          files={"file": ("trip.tsv", tsv, "application/octet-stream")})
    assert r.status_code == 422 and r.json()["code"] == "vin_mismatch"

    capsys.readouterr()   # drop the API's own structlog lines (they name the VIN)
    rc = onboard.report(result, "https://v3.test", str(tmp_path / "tokens"))
    out = capsys.readouterr().out
    assert rc == 0 and VIN_A not in out and VIN_B not in out
    files = sorted(p.name for p in (tmp_path / "tokens").iterdir())
    assert files == ["v3_uploader_toyota_corolla.env", "v3_uploader_toyota_hiace_ab1234.env"]
    hiace = (tmp_path / "tokens" / "v3_uploader_toyota_hiace_ab1234.env").read_text()
    assert f"STF_V3_DEVICE_TOKEN={vehicles[0]['token']}" in hiace
    assert f"STF_V3_VEHICLE_ID={vehicles[0]['id']}" in hiace
    import os
    import stat
    if os.name == "posix":
        mode = stat.S_IMODE((tmp_path / "tokens" / files[0]).stat().st_mode)
        assert mode == 0o600

    # rerun: workshop and vehicles reused, fresh tokens only
    async with SessionLocal() as session:
        again = await onboard.onboard(session, "Onboard Test WS", 0, 0, specs, [VIN_A, VIN_B], "Jetson")
    assert again["created"] is False and again["workshop_id"] == result["workshop_id"]
    assert [v["existing"] for v in again["vehicles"]] == [True, True]  # type: ignore[index]
    assert [v["id"] for v in again["vehicles"]] == [v["id"] for v in vehicles]  # type: ignore[index]
    assert again["vehicles"][0]["token"] != vehicles[0]["token"]  # type: ignore[index]
