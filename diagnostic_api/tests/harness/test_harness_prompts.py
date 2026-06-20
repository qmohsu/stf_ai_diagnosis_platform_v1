"""Tests for the harness user-message vehicle grounding (HARNESS-26).

``build_user_message`` must surface the make/model the uploader stated
(APP-60) so the agent grounds on the real vehicle and matches the right
service manual, instead of reverse-reasoning the model from the only
same-make manual.  Offline-safe: ``harness_prompts`` is a pure module
(no tiktoken import).
"""

from app.harness.harness_prompts import (
    _format_vehicle,
    build_user_message,
)


class TestFormatVehicle:
    """Tests for the _format_vehicle helper."""

    def test_make_model_with_vin(self):
        """Make/model render with the VIN appended."""
        out = _format_vehicle({
            "manufacturer": "Toyota",
            "vehicle_model": "Hiace",
            "vehicle_id": "JTFHT02P500072677",
        })
        assert out == "Toyota Hiace (VIN JTFHT02P500072677)"

    def test_make_model_without_usable_vin(self):
        """A V-UNKNOWN / blank VIN is omitted, not shown."""
        assert (
            _format_vehicle({
                "manufacturer": "Toyota",
                "vehicle_model": "Hiace",
                "vehicle_id": "V-UNKNOWN",
            })
            == "Toyota Hiace"
        )
        assert (
            _format_vehicle({
                "manufacturer": "Yamaha",
                "vehicle_model": "TRICITY155",
            })
            == "Yamaha TRICITY155"
        )

    def test_falls_back_to_vehicle_id(self):
        """Historical sessions with no make/model use vehicle_id."""
        assert _format_vehicle({"vehicle_id": "ABC123"}) == "ABC123"

    def test_unknown_when_nothing_present(self):
        """Empty summary yields 'unknown'."""
        assert _format_vehicle({}) == "unknown"


class TestBuildUserMessage:
    """Tests for build_user_message vehicle grounding."""

    def test_message_grounds_on_make_model(self):
        """The Vehicle line carries make/model + VIN, not just the VIN."""
        msg = build_user_message(
            "sid",
            {
                "manufacturer": "Toyota",
                "vehicle_model": "Hiace",
                "vehicle_id": "JTFHT02P500072677",
                "dtc_codes": "P00AF",
                "time_range": "15s",
            },
        )
        assert "Vehicle: Toyota Hiace (VIN JTFHT02P500072677)" in msg
        assert "P00AF" in msg

    def test_message_falls_back_for_legacy_session(self):
        """No make/model → the Vehicle line shows the bare vehicle_id."""
        msg = build_user_message(
            "sid", {"vehicle_id": "legacy-vin", "dtc_codes": "none"},
        )
        assert "Vehicle: legacy-vin" in msg

    def test_locale_suffix_appended(self):
        """A known locale appends a response-language instruction."""
        msg = build_user_message(
            "sid",
            {"manufacturer": "Toyota", "vehicle_model": "Hiace"},
            locale="zh-TW",
        )
        assert "Chinese (Traditional)" in msg
