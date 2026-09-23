"""PROD-10 T-2: the golden sets and the road-test log are the locked bytes
(FM-8 / FM-46 / FM-56).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import json
import pathlib
import shutil

import pytest

from stf_v3.evals import data as evdata
from stf_v3.evals.lanes import LANE_MANUAL, LANE_OBD
from stf_v3.ingest.loader import load_obd_data

_V3 = pathlib.Path(__file__).resolve().parents[1]
ROOT = evdata.data_dir(str(_V3 / "evals"))


def test_manifest_hashes_match_and_record_the_v2_source() -> None:
    """Every file matches its sha256; the manifest names the V2 source file,
    its hash and the copy date (FM-8 / FM-56)."""
    hashes = evdata.verify(ROOT)
    manifest = evdata.load_manifest(ROOT)
    assert set(hashes) == set(manifest["files"]) == {
        "golden/manual_mws150a.jsonl", "golden/obd_yamaha_road_test.jsonl", "fixtures/yamaha_road_test.csv"}
    for rec in manifest["files"].values():
        assert rec["source"] and rec["source_sha256"] == rec["sha256"]
    assert manifest["copied_on"] and manifest["source_commit"]


def test_golden_counts_and_no_vehicle_overrides() -> None:
    """30 manual + 15 OBD goldens, all valid; none uses a per-entry vehicle
    override (V3 always pins the vehicle record)."""
    manual = evdata.load_golden(ROOT, LANE_MANUAL)
    obd = evdata.load_golden(ROOT, LANE_OBD)
    assert len(manual) == 30 and len(obd) == 15
    assert all(e.vehicle is None for e in manual + obd)
    assert sum(e.requires_image for e in manual) == 6


def test_the_road_test_fixture_parses_as_yamaha_dual() -> None:
    """The copied log is what the OBD lane reads through the V3 loader."""
    log = load_obd_data(ROOT / evdata.FIXTURE_FILE)
    assert log.format == "yamaha_dual" and len(log.rows) > 200
    assert {d.code for d in log.metadata_dtcs}


@pytest.mark.parametrize("change", ["byte", "crlf"])
def test_a_changed_byte_or_line_ending_refuses(tmp_path: pathlib.Path, change: str) -> None:
    """One edited character or a CRLF conversion → the run refuses (FM-8)."""
    copy = tmp_path / "evals"
    shutil.copytree(ROOT, copy)
    target = copy / "golden" / "obd_yamaha_road_test.jsonl"
    raw = target.read_bytes()
    target.write_bytes(raw.replace(b"RPM", b"rpm", 1) if change == "byte" else raw.replace(b"\n", b"\r\n"))
    with pytest.raises(evdata.EvalDataError, match="changed"):
        evdata.verify(copy)


def test_data_dir_resolution_order(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit path → STF_V3_EVAL_DATA_DIR → ./evals → repo copy."""
    copy = tmp_path / "x"
    shutil.copytree(ROOT, copy)
    monkeypatch.setenv("STF_V3_EVAL_DATA_DIR", str(copy))
    assert evdata.data_dir() == copy
    assert evdata.data_dir(str(ROOT)) == ROOT


def test_image_build_copies_the_eval_data_and_bakes_the_tokenizer() -> None:
    """The Dockerfile copies ``evals/`` and pre-downloads cl100k_base (FM-46 / FM-24)."""
    if not (_V3 / "Dockerfile").is_file():
        pytest.skip("Dockerfile not in the image")
    dockerfile = (_V3 / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY evals ./evals" in dockerfile
    assert "TIKTOKEN_CACHE_DIR" in dockerfile and "cl100k_base" in dockerfile


def test_git_never_converts_the_eval_data() -> None:
    """``.gitattributes`` marks the data ``-text`` so Windows checkouts keep
    the hashed bytes (skipped in the portable copy)."""
    attrs = _V3.parent / ".gitattributes"
    if not attrs.is_file():
        pytest.skip("repo root not present (portable copy)")
    text = attrs.read_text(encoding="utf-8")
    assert "stf_v3/evals/golden/* -text" in text and "stf_v3/evals/fixtures/* -text" in text


def test_manual_hashes_find_files_by_id(tmp_path: pathlib.Path) -> None:
    """Library hashes are looked up by manual id anywhere under the root."""
    d = tmp_path / "Some Manual" / "index"
    d.mkdir(parents=True)
    (tmp_path / "Some Manual" / "m1.md").write_text("# x", encoding="utf-8")
    (d / "m1.index.yaml").write_text("nodes: []", encoding="utf-8")
    out = evdata.manual_hashes(tmp_path, ["m1", "m2"])
    assert len(out["m1"]["md"]) == 64 and len(out["m1"]["index"]) == 64
    assert out["m2"] == {"md": "missing", "index": "missing"}
    json.dumps(out)


def test_no_reference_to_the_v2_eval_package() -> None:
    """T-12 / FM-39: the copied modules and tests reference neither V2's
    ``tests.harness`` package nor its ``app`` package."""
    files = list((_V3 / "src" / "stf_v3" / "evals").glob("*.py")) + list((_V3 / "tests").glob("test_evals*.py"))
    assert len(files) > 15
    for f in files:
        text = f.read_text(encoding="utf-8")
        for bad in ("from tests." + "harness", "import tests." + "harness", "from " + "app.", "import " + "app."):
            assert bad not in text, f"{f.name}: {bad}"
