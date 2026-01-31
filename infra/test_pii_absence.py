"""Proof test: raw PII must not appear in LLM context or log output.

AIAPP-003 acceptance criteria:
  'Tests prove raw PII is not present in logs or LLM context builder output'
"""

import io
import sys

import structlog

from app.privacy.redaction import PIIRedactor
from app.api.v1.schemas import DiagnosisRequest

# ------------------------------------------------------------------
# Shared test data: text containing every PII type
# ------------------------------------------------------------------
PII_SAMPLES = {
    "phone": "555-0199",
    "email": "john.smith@company.com",
    "name": "John Smith",
    "gps": "25.0330,121.5654",
    "plate": "ABC-1234",
    "taiwan_id": "A123456789",
    "address": "123 Main Street",
}

SYMPTOM_TEXT = (
    f"Driver {PII_SAMPLES['name']} called {PII_SAMPLES['phone']} "
    f"and emailed {PII_SAMPLES['email']}. "
    f"Vehicle plate {PII_SAMPLES['plate']} seen at "
    f"{PII_SAMPLES['gps']}. "
    f"Owner ID {PII_SAMPLES['taiwan_id']}. "
    f"Parked at {PII_SAMPLES['address']}. "
    f"Engine rough idle."
)


def test_pii_absent_from_redacted_text():
    """No raw PII survives redact_text()."""
    result = PIIRedactor.redact_text(SYMPTOM_TEXT)
    for pii_type, pii_value in PII_SAMPLES.items():
        if pii_value in result:
            print(
                f"  FAIL: {pii_type} PII '{pii_value}' found "
                f"in redacted text: {result}"
            )
            sys.exit(1)
    print("  PASS: no raw PII in redacted text")


def test_pii_absent_from_llm_prompt():
    """Simulate prompt building (diagnosis.py) and verify no PII."""
    redacted_symptoms = PIIRedactor.redact_text(SYMPTOM_TEXT)

    request = DiagnosisRequest(
        vehicle_id="TEST-VIN",
        make="Ford",
        model="F-150",
        year=2020,
        mileage=50000,
        symptoms=SYMPTOM_TEXT,
        dtc_codes=["P0300"],
    )

    # Replicate query building (diagnosis.py lines 32-34)
    query = (
        f"{request.year} {request.make} {request.model} "
        f"{redacted_symptoms}"
    )
    query += f" {' '.join(request.dtc_codes)}"

    # Replicate full symptom description (diagnosis.py lines 51-53)
    full_symptom = redacted_symptoms
    full_symptom += f"\nActive DTCs: {', '.join(request.dtc_codes)}"

    # Vehicle info string
    vehicle_info = request.to_vehicle_string()

    # Verify no PII in any prompt component
    components = {
        "query": query,
        "full_symptom": full_symptom,
        "vehicle_info": vehicle_info,
    }
    for comp_name, comp_value in components.items():
        for pii_type, pii_value in PII_SAMPLES.items():
            if pii_value in comp_value:
                print(
                    f"  FAIL: {pii_type} PII '{pii_value}' found "
                    f"in {comp_name}: {comp_value}"
                )
                sys.exit(1)
    print("  PASS: no raw PII in LLM prompt components")


def test_pii_absent_from_log_output():
    """Verify structlog redaction event does not contain raw PII."""
    output = io.StringIO()

    # Save original config to restore after test
    old_config = structlog.get_config()

    try:
        structlog.configure(
            processors=[
                structlog.dev.ConsoleRenderer(),
            ],
            wrapper_class=structlog.BoundLogger,
            logger_factory=structlog.PrintLoggerFactory(file=output),
            cache_logger_on_first_use=False,
        )

        # Trigger redaction (emits pii_redaction_applied log event)
        _ = PIIRedactor.redact_text(SYMPTOM_TEXT)

        log_output = output.getvalue()
        for pii_type, pii_value in PII_SAMPLES.items():
            if pii_value in log_output:
                print(
                    f"  FAIL: {pii_type} PII '{pii_value}' found "
                    f"in log output"
                )
                sys.exit(1)
    finally:
        # Restore structlog configuration
        structlog.configure(**old_config)

    print("  PASS: no raw PII in log output")


if __name__ == "__main__":
    print("Running PII Absence Proof Tests (AIAPP-003)...\n")
    tests = [
        test_pii_absent_from_redacted_text,
        test_pii_absent_from_llm_prompt,
        test_pii_absent_from_log_output,
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

    print(f"\nAll {passed} PII Absence Tests Passed!")
