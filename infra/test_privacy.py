"""Verification script for APP-06 (Privacy & Redaction)."""

import sys
from app.privacy.redaction import redactor
from app.privacy.feature_boundary import FeatureBoundary

def test_pii_redaction():
    print("Testing PII Redaction/Boundary...")
    
    # Test 1: PII Redaction in strings
    # Verify 10-digit and 7-digit redaction
    input_text = "Email joe@example.com or call 555-0199 or 800-555-1234 please. Mileage 123456 is safe."
    # Both phones redacted, proper email redacted. Mileage NOT redacted (no separator).
    expected = "Email [EMAIL_REDACTED] or call [PHONE_REDACTED] or [PHONE_REDACTED] please. Mileage 123456 is safe."
    
    result = redactor.redact_text(input_text)
    if result == expected:
        print("✅ PII Redaction worked (Phones & Email).")
    else:
        print(f"❌ PII Redaction failed.\nGot: {result}\nExp: {expected}")
        sys.exit(1)

    # Test 2: Data Boundary (Field Filtering)
    payload = {
        "vehicle_id": "VIN123",
        "symptoms": "Strange noise from engine.",
        "raw_can_bus": "0xFF 0xAB ...", # Should be removed
        "untrusted_field": "some data", # Should be removed
        "nested": {                     # Should be processed if keys match allowlist?
             "symptoms": "nested symptom",
             "bad_key": "bad value"
        }
    }
    
    # Note: Our allowlist is flat for keys. Nested dicts are checked against allowlist keys too.
    # 'nested' is NOT in ALLOWED_FIELDS, so the entire dict should be dropped?
    # Let's check ALLOWED_FIELDS in redaction.py: "symptoms" is there. "nested" is NOT.
    
    result_payload = FeatureBoundary.enforce(payload)
    
    if "raw_can_bus" in result_payload:
        print("❌ Data Boundary failed. 'raw_can_bus' was not removed.")
        sys.exit(1)
        
    if "vehicle_id" in result_payload and "symptoms" in result_payload:
        print("✅ Allowed fields preserved.")
    else:
        print("❌ Data Boundary failed. Allowed fields were dropped.")
        sys.exit(1)
        
    print("All Privacy Tests Passed!")

if __name__ == "__main__":
    test_pii_redaction()
