"""Comprehensive unit tests for /v1/tools/validate-vin endpoint.

Uses FastAPI TestClient (no live server required).
"""

import sys

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)

ENDPOINT = "/v1/tools/validate-vin"
passed = 0
failed = 0


def run_test(name, fn):
    global passed, failed
    try:
        fn()
        print(f"  PASS: {name}")
        passed += 1
    except AssertionError as e:
        print(f"  FAIL: {name} - {e}")
        failed += 1
    except Exception as e:
        print(f"  ERROR: {name} - {e}")
        failed += 1


def test_valid_vin():
    """Known valid VIN with correct check digit."""
    resp = client.post(ENDPOINT, json={"vin": "1HGBH41JXMN109186"})
    assert resp.status_code == 200, f"status={resp.status_code}"
    data = resp.json()
    assert data["is_valid"] is True, f"is_valid={data['is_valid']}"
    assert data["details"]["validation_level"] == "full"


def test_invalid_length_short():
    """Short VIN (5 chars) should be rejected."""
    resp = client.post(ENDPOINT, json={"vin": "ABCDE"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_valid"] is False
    assert "length" in data["details"]["error"].lower()


def test_invalid_length_long():
    """Long VIN (20 chars) should be rejected."""
    resp = client.post(ENDPOINT, json={"vin": "A" * 20})
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_valid"] is False
    assert "length" in data["details"]["error"].lower()


def test_invalid_chars_ioq():
    """VIN containing I, O, or Q should be rejected."""
    # 17 chars with 'I' at position 1
    resp = client.post(ENDPOINT, json={"vin": "1IGBH41JXMN109186"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_valid"] is False
    assert "alphanumeric" in data["details"]["error"].lower()


def test_invalid_special_chars():
    """VIN with special characters should be rejected."""
    resp = client.post(ENDPOINT, json={"vin": "!@#$%^&*()_+-=<>~"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_valid"] is False
    assert "alphanumeric" in data["details"]["error"].lower()


def test_invalid_check_digit():
    """Valid format but wrong check digit should be rejected."""
    # Change last digit: 1HGBH41JXMN109187 (check digit mismatch)
    resp = client.post(ENDPOINT, json={"vin": "1HGBH41JXMN109187"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_valid"] is False
    assert "check digit" in data["details"]["error"].lower()


def test_empty_string():
    """Empty input should be rejected by Pydantic (422)."""
    resp = client.post(ENDPOINT, json={"vin": ""})
    assert resp.status_code == 422, f"status={resp.status_code}"


def test_lowercase_normalization():
    """Lowercase VIN should be uppercased and validated."""
    resp = client.post(ENDPOINT, json={"vin": "1hgbh41jxmn109186"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_valid"] is True
    assert data["vin"] == "1HGBH41JXMN109186"


def test_whitespace_trimmed():
    """VIN with leading/trailing spaces should be trimmed."""
    resp = client.post(ENDPOINT, json={"vin": "  1HGBH41JXMN109186  "})
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_valid"] is True
    assert data["vin"] == "1HGBH41JXMN109186"


def test_response_structure():
    """Response should always have vin, is_valid, details fields."""
    resp = client.post(ENDPOINT, json={"vin": "1HGBH41JXMN109186"})
    assert resp.status_code == 200
    data = resp.json()
    assert "vin" in data, "missing 'vin' field"
    assert "is_valid" in data, "missing 'is_valid' field"
    assert "details" in data, "missing 'details' field"


if __name__ == "__main__":
    print("Running validate-vin unit tests...\n")

    tests = [
        ("test_valid_vin", test_valid_vin),
        ("test_invalid_length_short", test_invalid_length_short),
        ("test_invalid_length_long", test_invalid_length_long),
        ("test_invalid_chars_ioq", test_invalid_chars_ioq),
        ("test_invalid_special_chars", test_invalid_special_chars),
        ("test_invalid_check_digit", test_invalid_check_digit),
        ("test_empty_string", test_empty_string),
        ("test_lowercase_normalization", test_lowercase_normalization),
        ("test_whitespace_trimmed", test_whitespace_trimmed),
        ("test_response_structure", test_response_structure),
    ]

    for name, fn in tests:
        run_test(name, fn)

    print(f"\nResults: {passed} passed, {failed} failed out of {len(tests)}")

    if failed > 0:
        sys.exit(1)
    else:
        print("All validate-vin tests passed!")
