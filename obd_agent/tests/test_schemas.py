"""Tests for obd_agent.schemas -- OBDSnapshot Pydantic models."""

from __future__ import annotations

import json
from typing import Any, Dict

import pytest

from obd_agent.schemas import DTCEntry, OBDSnapshot, PIDValue


class TestDTCEntry:
    """DTC code validation."""

    @pytest.mark.parametrize(
        "code",
        ["P0301", "C0035", "B0100", "U0073", "P0ABF"],
    )
    def test_valid_dtc_codes(self, code: str) -> None:
        entry = DTCEntry(code=code, desc="test")
        assert entry.code == code

    @pytest.mark.parametrize(
        "code",
        ["X0301", "P030", "P03011", "p0301", "P03G1", ""],
    )
    def test_invalid_dtc_codes(self, code: str) -> None:
        with pytest.raises(ValueError, match="DTC code must match"):
            DTCEntry(code=code, desc="test")


class TestOBDSnapshot:
    """Snapshot-level validation."""

    def test_valid_fixture_parses(
        self, sample_snapshot_dict: Dict[str, Any]
    ) -> None:
        snap = OBDSnapshot.model_validate(sample_snapshot_dict)
        assert snap.vehicle_id == "V-SIM-001"
        assert len(snap.dtc) == 2
        assert snap.dtc[0].code == "P0301"

    def test_raw_vin_rejected(self) -> None:
        with pytest.raises(ValueError, match="raw VIN"):
            OBDSnapshot(
                vehicle_id="1HGBH41JXMN109186",
                adapter={"type": "ELM327", "port": "sim"},
            )

    def test_pseudonymous_id_accepted(self) -> None:
        snap = OBDSnapshot(
            vehicle_id="V-SIM-001",
            adapter={"type": "ELM327", "port": "sim"},
        )
        assert snap.vehicle_id == "V-SIM-001"

    def test_extra_fields_tolerated(
        self, sample_snapshot_dict: Dict[str, Any]
    ) -> None:
        sample_snapshot_dict["custom_field"] = "hello"
        snap = OBDSnapshot.model_validate(sample_snapshot_dict)
        assert snap.vehicle_id == "V-SIM-001"

    def test_empty_dtcs_valid(self) -> None:
        snap = OBDSnapshot(
            vehicle_id="V-001",
            adapter={"type": "ELM327", "port": "sim"},
            dtc=[],
        )
        assert snap.dtc == []

    def test_json_round_trip(
        self, sample_snapshot_dict: Dict[str, Any]
    ) -> None:
        snap = OBDSnapshot.model_validate(sample_snapshot_dict)
        json_str = snap.model_dump_json()
        restored = OBDSnapshot.model_validate_json(json_str)
        assert restored.vehicle_id == snap.vehicle_id
        assert len(restored.dtc) == len(snap.dtc)
        assert restored.baseline_pids.keys() == snap.baseline_pids.keys()

    def test_timestamp_auto_set(self) -> None:
        snap = OBDSnapshot(
            vehicle_id="V-001",
            adapter={"type": "ELM327", "port": "sim"},
        )
        assert snap.ts is not None


class TestPIDValue:
    def test_valid(self) -> None:
        pv = PIDValue(value=780.0, unit="rpm")
        assert pv.value == 780.0
        assert pv.unit == "rpm"
