"""Extended verification script for AIAPP-003 (PII redaction patterns).

Tests all new PII redaction patterns: names, locations, IDs, plates,
plus the stats API and feature boundary module.
"""

import sys
from app.privacy.redaction import redactor, RedactionResult
from app.privacy.feature_boundary import FeatureBoundary


def test_email_redaction():
    """Existing email pattern still works."""
    text = "Contact admin@stf.com for details."
    result = redactor.redact_text(text)
    assert "[EMAIL_REDACTED]" in result, result
    assert "admin@stf.com" not in result, result
    print("  PASS: email redaction")


def test_phone_redaction():
    """Existing phone pattern still works."""
    text = "Call 555-0199 or 800-555-1234."
    result = redactor.redact_text(text)
    assert "[PHONE_REDACTED]" in result, result
    assert "555-0199" not in result, result
    assert "800-555-1234" not in result, result
    print("  PASS: phone redaction")


def test_name_redaction():
    """AIAPP-003: keyword-triggered name detection."""
    cases = [
        ("Driver John Smith reported the issue.", "John Smith"),
        ("Owner Jane Doe was contacted.", "Jane Doe"),
        ("Customer Bob Wilson called us.", "Bob Wilson"),
        ("Technician Mike Chen inspected.", "Mike Chen"),
        ("Mr. David Lee signed off.", "David Lee"),
        ("Mrs. Sarah Wang was notified.", "Sarah Wang"),
        ("Ms. Amy Lin confirmed.", "Amy Lin"),
        ("Contact Alex Brown for details.", "Alex Brown"),
    ]
    for text, name in cases:
        result = redactor.redact_text(text)
        assert name not in result, f"Name '{name}' not redacted in: {result}"
        assert "[NAME_REDACTED]" in result, f"No token in: {result}"
    print("  PASS: name redaction (keyword-triggered)")


def test_name_without_keyword_preserved():
    """Names without indicator keywords should NOT be redacted."""
    texts = [
        "Check the Fuel System and Spark Plug.",
        "Ford Explorer has a known issue.",
        "The Idle Speed is unstable.",
        "Power Steering fluid is low.",
    ]
    for text in texts:
        result = redactor.redact_text(text)
        assert "[NAME_REDACTED]" not in result, (
            f"False positive: {result}"
        )
    print("  PASS: non-keyword names preserved")


def test_gps_coordinate_redaction():
    """AIAPP-003: overly precise location strings (GPS coords)."""
    text = "Vehicle last seen at 25.0330,121.5654 near the depot."
    result = redactor.redact_text(text)
    assert "25.0330" not in result, result
    assert "121.5654" not in result, result
    assert "[LOCATION_REDACTED]" in result, result
    print("  PASS: GPS coordinate redaction")


def test_street_address_redaction():
    """AIAPP-003: overly precise location strings (street addresses)."""
    cases = [
        "Vehicle parked at 123 Main Street.",
        "Serviced at 456 Oak Road yesterday.",
        "Found near 789 Elm Avenue after hours.",
    ]
    for text in cases:
        result = redactor.redact_text(text)
        assert "[ADDRESS_REDACTED]" in result, (
            f"Address not redacted: {result}"
        )
    print("  PASS: street address redaction")


def test_plate_number_redaction():
    """APP-06/design_doc: plate number redaction (Taiwan formats)."""
    cases = [
        ("Vehicle plate: ABC-1234 was involved.", "ABC-1234"),
        ("Plate number AB-5678 on record.", "AB-5678"),
        ("Old format plate 1234-AB seen.", "1234-AB"),
    ]
    for text, plate in cases:
        result = redactor.redact_text(text)
        assert plate not in result, f"Plate '{plate}' not redacted: {result}"
        assert "[PLATE_REDACTED]" in result, f"No token: {result}"
    print("  PASS: plate number redaction")


def test_taiwan_national_id_redaction():
    """APP-06: Taiwan National ID pattern."""
    cases = [
        ("Owner ID: A123456789 on file.", "A123456789"),
        ("Registered to B298765432.", "B298765432"),
    ]
    for text, tid in cases:
        result = redactor.redact_text(text)
        assert tid not in result, f"ID '{tid}' not redacted: {result}"
        assert "[ID_REDACTED]" in result, f"No token: {result}"
    print("  PASS: Taiwan national ID redaction")


def test_redact_text_with_stats():
    """Verify redact_text_with_stats returns accurate counts."""
    text = (
        "Call 555-0199, email joe@test.com, "
        "driver John Smith, plate ABC-1234."
    )
    result = redactor.redact_text_with_stats(text)
    assert isinstance(result, RedactionResult), type(result)
    assert result.phone_count == 1, f"phone: {result.phone_count}"
    assert result.email_count == 1, f"email: {result.email_count}"
    assert result.name_count == 1, f"name: {result.name_count}"
    assert result.plate_count == 1, f"plate: {result.plate_count}"
    assert result.total_count >= 4, f"total: {result.total_count}"
    assert "555-0199" not in result.text, result.text
    assert "joe@test.com" not in result.text, result.text
    print("  PASS: redact_text_with_stats counts")


def test_feature_boundary_drops_unknown_fields():
    """Feature boundary drops fields not in allowlist."""
    payload = {
        "vehicle_id": "V123",
        "symptoms": "Noise from engine",
        "raw_can_bus": "0xFF 0xAB",
        "owner_name": "John Smith",
        "gps_track": "25.0330,121.5654",
    }
    safe = FeatureBoundary.enforce(payload)
    assert "raw_can_bus" not in safe, safe
    assert "owner_name" not in safe, safe
    assert "gps_track" not in safe, safe
    assert "vehicle_id" in safe, safe
    assert "symptoms" in safe, safe
    print("  PASS: feature boundary field filtering")


def test_feature_boundary_redacts_strings():
    """Feature boundary also redacts PII within allowed fields."""
    payload = {
        "symptoms": "Driver John Smith called 555-0199.",
    }
    safe = FeatureBoundary.enforce(payload)
    assert "John Smith" not in safe["symptoms"], safe
    assert "555-0199" not in safe["symptoms"], safe
    assert "[NAME_REDACTED]" in safe["symptoms"], safe
    assert "[PHONE_REDACTED]" in safe["symptoms"], safe
    print("  PASS: feature boundary string redaction")


if __name__ == "__main__":
    print("Running Extended Privacy Tests (AIAPP-003)...\n")
    tests = [
        test_email_redaction,
        test_phone_redaction,
        test_name_redaction,
        test_name_without_keyword_preserved,
        test_gps_coordinate_redaction,
        test_street_address_redaction,
        test_plate_number_redaction,
        test_taiwan_national_id_redaction,
        test_redact_text_with_stats,
        test_feature_boundary_drops_unknown_fields,
        test_feature_boundary_redacts_strings,
    ]
    passed = 0
    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except AssertionError as e:
            print(f"  FAIL: {test_fn.__name__} - {e}")
            sys.exit(1)
        except Exception as e:
            print(f"  ERROR: {test_fn.__name__} - {e}")
            sys.exit(1)

    print(f"\nAll {passed} Extended Privacy Tests Passed!")
