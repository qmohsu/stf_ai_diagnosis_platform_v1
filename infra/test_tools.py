
import requests
import json
import sys
import time

BASE_URL = "http://localhost:8000"

def test_tools():
    print(f"Testing Dify Tools against {BASE_URL}...")
    failures = 0

    # 1. Test Redaction
    print("\n[1] Testing /v1/tools/redact...")
    redact_payload = {
        "text": "Call me at 555-0199 or email user@example.com about VIN 123."
    }

    try:
        resp = requests.post(f"{BASE_URL}/v1/tools/redact", json=redact_payload)
        if resp.status_code == 200:
            data = resp.json()
            print("[SUCCESS] Redaction:")
            print(f"  Input: {data['original_text']}")
            print(f"  Output: {data['redacted_text']}")
            print(f"  Count: {data['redacted_count']}")
            if "[PHONE_REDACTED]" not in data['redacted_text']:
                print("[FAILED] Phone not redacted")
                failures += 1
            if "[EMAIL_REDACTED]" not in data['redacted_text']:
                print("[FAILED] Email not redacted")
                failures += 1
        else:
            print(f"[FAILED] {resp.status_code} - {resp.text}")
            failures += 1

    except Exception as e:
        print(f"[ERROR] {e}")
        failures += 1

    # 2. Test VIN Validation (Valid)
    print("\n[2] Testing /v1/tools/validate-vin (Valid)...")
    valid_vin_payload = {"vin": "1HGBH41JXMN109186"}  # Valid check digit
    try:
        resp = requests.post(f"{BASE_URL}/v1/tools/validate-vin", json=valid_vin_payload)
        if resp.status_code == 200:
            data = resp.json()
            if data['is_valid']:
                print(f"[SUCCESS] Valid VIN accepted: {data['vin']}")
            else:
                print(f"[FAILED] Valid VIN rejected: {data}")
                failures += 1
        else:
            print(f"[FAILED] {resp.status_code} - {resp.text}")
            failures += 1
    except Exception as e:
        print(f"[ERROR] {e}")
        failures += 1

    # 3. Test VIN Validation (Invalid)
    print("\n[3] Testing /v1/tools/validate-vin (Invalid)...")
    invalid_vin_payload = {"vin": "1ABCDEFGHJKLMNPQQ"}  # Contains Q
    try:
        resp = requests.post(f"{BASE_URL}/v1/tools/validate-vin", json=invalid_vin_payload)
        if resp.status_code == 200:
            data = resp.json()
            if not data['is_valid']:
                print(f"[SUCCESS] Invalid VIN rejected: {data['details']['error']}")
            else:
                print(f"[FAILED] Invalid VIN accepted: {data}")
                failures += 1
        else:
            print(f"[FAILED] {resp.status_code} - {resp.text}")
            failures += 1
    except Exception as e:
        print(f"[ERROR] {e}")
        failures += 1

    # Summary
    if failures > 0:
        print(f"\n{failures} test(s) FAILED.")
        sys.exit(1)
    else:
        print("\nAll integration tests passed!")

if __name__ == "__main__":
    time.sleep(1)
    test_tools()
