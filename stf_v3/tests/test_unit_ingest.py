"""Offline unit tests for the ingest parsers and file storage (PROD-05).

Fixtures under ``tests/fixtures`` are trimmed copies of V2 sample logs with
fake VINs only (repo policy: no real VINs in Git).

Author: Xiangzhu Yan
"""

import pathlib
import uuid
from datetime import datetime, timezone

import pytest

from stf_v3.ingest.parsers import decode, sniff
from stf_v3.ingest.parsers.base import normalise_vin, parse_timestamp
from stf_v3.ingest.service import sha256_hex
from stf_v3.ingest.storage import LogStorage

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
TSV_OK = (FIXTURES / "jetson_tsv_ok.tsv").read_bytes()
TSV_OTHER = (FIXTURES / "jetson_tsv_other_vin.tsv").read_bytes()
YAMAHA = (FIXTURES / "yamaha_dual.csv").read_bytes()
NOT_OBD = (FIXTURES / "not_obd.csv").read_bytes()


def _utc(s: str) -> datetime:
    fmt = "%Y-%m-%d %H:%M:%S.%f" if "." in s else "%Y-%m-%d %H:%M:%S"
    return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)


def test_sniff_jetson_tsv_reads_vin_and_window() -> None:
    """A Jetson TSV is recognised; VIN comes from the bytearray column,
    start from ``Start Time:``, end from ``Log End Time:``."""
    meta = sniff(TSV_OK)
    assert meta is not None
    assert meta.format == "tsv" and meta.extension == "tsv"
    assert meta.vin == "JHMGK5830HX202404"
    assert meta.recorded_start == _utc("2025-07-23 14:42:16")
    assert meta.recorded_end == _utc("2025-07-23 14:42:35")


def test_sniff_jetson_tsv_other_vin_fixture() -> None:
    """The second TSV fixture carries a different fake VIN."""
    meta = sniff(TSV_OTHER)
    assert meta is not None and meta.vin == "1HGCM82633A123456"


def test_tsv_end_falls_back_to_last_row_without_footer() -> None:
    """Without ``Log End Time:`` the last data row's timestamp is used."""
    text = decode(TSV_OK)
    lines = [l for l in text.splitlines() if not l.startswith("Log End")]
    last_row = [l for l in lines if l[:4].isdigit()][-1]
    meta = sniff("\n".join(lines).encode())
    assert meta is not None
    assert meta.recorded_end == parse_timestamp(last_row.split("\t", 1)[0])
    assert meta.recorded_end >= meta.recorded_start  # type: ignore[operator]


def test_tsv_without_banner_is_not_recognised() -> None:
    """Banner AND header are both required (stricter than V2)."""
    lines = decode(TSV_OK).splitlines()[1:]   # drop "OBD Data Log"
    assert sniff("\n".join(lines).encode()) is None


def test_sniff_yamaha_reads_window_and_has_no_vin() -> None:
    """A Yamaha CSV is recognised via its first-line marker; VIN is None
    when no ``# vehicle_id:`` line exists; window from ``# Start``/``# End``."""
    meta = sniff(YAMAHA)
    assert meta is not None
    assert meta.format == "yamaha" and meta.extension == "csv"
    assert meta.vin is None
    assert meta.recorded_start == _utc("2026-05-08 11:20:39")
    assert meta.recorded_end == _utc("2026-05-08 11:21:00.508967")


def test_yamaha_vehicle_id_line_used_only_when_valid_vin() -> None:
    """``# vehicle_id:`` becomes ``vin`` only if it is a valid 17-char VIN."""
    text = decode(YAMAHA)
    first, rest = text.split("\n", 1)
    good = f"{first}\n# vehicle_id: JHMGK5830HX202404\n{rest}".encode()
    bad = f"{first}\n# vehicle_id: JYAMA00000XX000001\n{rest}".encode()  # 18 chars
    assert sniff(good).vin == "JHMGK5830HX202404"  # type: ignore[union-attr]
    assert sniff(bad).vin is None  # type: ignore[union-attr]


def test_yamaha_end_falls_back_to_last_row() -> None:
    """Without the ``# End:`` footer the last data row's timestamp is used."""
    lines = [l for l in decode(YAMAHA).splitlines() if not l.startswith("# End")]
    meta = sniff("\n".join(lines).encode())
    assert meta is not None
    assert meta.recorded_end is not None and meta.recorded_end > meta.recorded_start  # type: ignore[operator]


MAXLOG_HIACE = (FIXTURES / "obd_maxlog_hiace.csv").read_bytes()
MAXLOG_NO_VIN = (FIXTURES / "obd_maxlog_no_vin.csv").read_bytes()


def test_sniff_maxlog_reads_vin_and_window() -> None:
    """PROD-07 D3: an OBD Maximum Data Log is recognised by its banner; VIN
    from ``# vehicle_id:``, start from ``# Start Time:``, end from the
    ``# Log End Time:`` trailer; stored as ``.csv`` with format ``maxlog``."""
    meta = sniff(MAXLOG_HIACE)
    assert meta is not None
    assert meta.format == "maxlog" and meta.extension == "csv"
    assert meta.vin == "JHMGK5830HX202404"
    assert meta.recorded_start == _utc("2026-06-22 15:39:54")
    assert meta.recorded_end == _utc("2026-06-22 15:41:19")


def test_sniff_maxlog_without_vin_or_trailer() -> None:
    """The Corolla shape: no ``# vehicle_id:``, no Mode 09 VIN, no trailer →
    vin None (token decides the car), end = last data row's timestamp
    (millisecond precision)."""
    meta = sniff(MAXLOG_NO_VIN)
    assert meta is not None
    assert meta.format == "maxlog" and meta.vin is None
    assert meta.recorded_start == _utc("2026-08-19 16:38:32")
    assert meta.recorded_end == _utc("2026-08-19 16:39:14.103")


def test_maxlog_vin_falls_back_to_mode09_line_and_rejects_garbage() -> None:
    """Without ``# vehicle_id:`` the Mode 09 ``#   VIN:`` line is used; a
    non-VIN value in either line yields None rather than a false mismatch."""
    text = decode(MAXLOG_HIACE)
    no_vehicle_id = "\n".join(l for l in text.splitlines() if not l.startswith("# vehicle_id:"))
    assert sniff(no_vehicle_id.encode()).vin == "JHMGK5830HX202404"  # type: ignore[union-attr]
    garbage = text.replace("# vehicle_id: JHMGK5830HX202404", "# vehicle_id: hiace-01")
    garbage = garbage.replace("#   VIN: JHMGK5830HX202404", "#   VIN: N/A")
    assert sniff(garbage.encode()).vin is None  # type: ignore[union-attr]


def test_maxlog_banner_required_not_just_hash_metadata() -> None:
    """A ``#``-commented CSV without the banner is not maxlog (nor anything)."""
    lines = decode(MAXLOG_NO_VIN).splitlines()[1:]
    assert sniff("\n".join(lines).encode()) is None


@pytest.mark.parametrize(
    "payload",
    [NOT_OBD, b"{\"a\": 1}", b"", b"Timestamp\tRPM\n1\t2\n", b"\xff\xfe\x00binary"],
)
def test_unrecognised_payloads_return_none(payload: bytes) -> None:
    """Decoys (incl. a CSV mentioning ``Ch.A:``) are not accepted."""
    assert sniff(payload) is None


def test_normalise_vin_rejects_garbage() -> None:
    """Empty, N/A, wrong length and I/O/Q characters all yield None."""
    assert normalise_vin("jhmgk5830hx202404") == "JHMGK5830HX202404"
    for bad in (None, "", "N/A", "JHMGK5830HX20240", "JHMGK5830HX2024040", "IHMGK5830HX202404"):
        assert normalise_vin(bad) is None


def test_parse_timestamp_accepts_both_precisions_and_never_guesses() -> None:
    """Whole-second and fractional formats parse as UTC; junk → None."""
    assert parse_timestamp("2026-05-08 11:20:39") == _utc("2026-05-08 11:20:39")
    assert parse_timestamp("2026-05-08 11:20:39.5") == _utc("2026-05-08 11:20:39.5")
    assert parse_timestamp("yesterday") is None and parse_timestamp(None) is None


def test_storage_layout_and_atomic_write(tmp_path: pathlib.Path) -> None:
    """Files land at <root>/<vehicle_id>/<log_id>.<ext>; no .part remains."""
    store = LogStorage(str(tmp_path))
    vid, lid = uuid.uuid4(), uuid.uuid4()
    rel = store.relative_path(vid, lid, "tsv")
    assert rel == f"{vid}/{lid}.tsv"
    written = store.write(rel, TSV_OK)
    assert written == tmp_path.resolve() / str(vid) / f"{lid}.tsv"
    assert written.read_bytes() == TSV_OK
    assert not list(tmp_path.rglob("*.part"))
    assert store.exists(rel)
    store.delete(rel)
    assert not store.exists(rel)
    store.delete(rel)   # idempotent


def test_storage_refuses_paths_outside_root(tmp_path: pathlib.Path) -> None:
    """A raw_path that escapes the root is refused (defence in depth)."""
    store = LogStorage(str(tmp_path))
    with pytest.raises(ValueError):
        store.absolute_path("../../etc/passwd")
    assert store.exists("../x") is False


def test_sha256_hex_matches_stdlib() -> None:
    """Dedupe key is the plain hex sha256 of the bytes."""
    import hashlib

    assert sha256_hex(TSV_OK) == hashlib.sha256(TSV_OK).hexdigest()
    assert len(sha256_hex(b"")) == 64
