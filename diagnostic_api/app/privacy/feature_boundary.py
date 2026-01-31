"""Feature boundary enforcement for LLM data safety.

Ensures only allowlisted fields reach the LLM context.
Recursively filters payloads and redacts string values.
"""

from typing import Dict, Any

from app.privacy.redaction import PIIRedactor


class FeatureBoundary:
    """
    Enforces data boundary: only allowlisted fields reach the LLM.

    IMPORTANT: FAIL CLOSED SECURITY
    If you add a new field to the data model, you MUST add it here.
    Any field not in this list will be silently dropped to prevent
    leakage.
    """

    ALLOWED_FIELDS = {
        "vehicle_id",
        "make",
        "model",
        "year",
        "mileage",
        "symptoms",
        "dtc_codes",
        "description",
        "session_id",
        "query",
        "fluids",   # e.g. fluid levels
        "logs",     # e.g. maintenance logs strings
    }

    @staticmethod
    def enforce(payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Recursively filter payload to only include allowed fields
        and redact strings within them.
        """
        safe_payload = {}
        for key, value in payload.items():
            if key in FeatureBoundary.ALLOWED_FIELDS:
                safe_payload[key] = FeatureBoundary._sanitize_value(
                    value
                )
        return safe_payload

    @staticmethod
    def _sanitize_value(value: Any) -> Any:
        """Helper to sanitize values recursively."""
        if isinstance(value, str):
            return PIIRedactor.redact_text(value)
        elif isinstance(value, dict):
            return FeatureBoundary.enforce(value)
        elif isinstance(value, list):
            return [FeatureBoundary._sanitize_value(v) for v in value]
        else:
            return value
