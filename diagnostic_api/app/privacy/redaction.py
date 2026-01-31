"""Privacy and PII redaction utilities."""

import re
from typing import Dict, Any, List, Union

class PIIRedactor:
    """Handles PII detection and redaction."""

    # Regex patterns for common PII
    EMAIL_PATTERN = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
    # Matches 10-digit (555-555-5555) or 7-digit with separator (555-5555)
    PHONE_PATTERN = r"\b(?:\(?\d{3}\)?[-.\s]?)?\d{3}[-.\s]\d{4}\b"

    # Taiwan National ID: one uppercase letter + 1 or 2 + 8 digits
    TAIWAN_ID_PATTERN = r"\b[A-Z][12]\d{8}\b"

    # GPS coordinates: decimal degrees with 4-8 decimal places
    GPS_PATTERN = r"-?\d{1,3}\.\d{4,8}\s*,\s*-?\d{1,3}\.\d{4,8}"

    # Street address: number + words + common street suffix
    ADDRESS_PATTERN = (
        r"\b\d{1,6}\s+[A-Za-z\u4e00-\u9fff]+"
        r"(?:\s+[A-Za-z\u4e00-\u9fff]+){0,4}"
        r"\s+(?:St(?:reet)?|Rd|Road|Ave(?:nue)?|Blvd|Boulevard"
        r"|Dr(?:ive)?|Ln|Lane|Way|Ct|Court|Pl(?:ace)?"
        r"|Section|Sec\.?"
        r"|\u8def|\u8857|\u5df7|\u6bb5)\b"
    )

    # Taiwan vehicle plate: ABC-1234, XX-1234, or 1234-XX
    PLATE_PATTERN = r"\b(?:[A-Z]{2,3}-\d{4}|\d{4}-[A-Z]{2})\b"

    # Keyword-triggered name pattern: capitalized name pair preceded
    # by an indicator word (driver, owner, customer, technician,
    # Mr., Mrs., Ms., mechanic, operator, contact)
    NAME_PATTERN = (
        r"(?:(?:driver|owner|customer|technician|mechanic"
        r"|operator|contact|Mr\.|Mrs\.|Ms\.)\s+)"
        r"([A-Z][a-z]{1,20}\s+[A-Z][a-z]{1,20})"
    )
    
    # Simple whitelist of fields allowed to reach the LLM
    # IMPORTANT: FAIL CLOSED SECURITY
    # If you add a new field to the data model, you MUST add it here.
    # Any field not in this list will be silently dropped to prevent leakage.
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
        "fluids", # e.g. fluid levels
        "logs"    # e.g. maintenance logs strings
    }

    # Max characters allow per string field (prevent ReDoS and context overflow)
    MAX_TEXT_LENGTH = 10000

    @staticmethod
    def redact_text(text: str) -> str:
        """Redact PII from a string."""
        if not text:
            return ""
        
        # Prevent ReDoS / resource exhaustion
        if len(text) > PIIRedactor.MAX_TEXT_LENGTH:
            text = text[:PIIRedactor.MAX_TEXT_LENGTH] + " ... [TRUNCATED_DUE_TO_SIZE]"
        
        # Redact emails
        text = re.sub(PIIRedactor.EMAIL_PATTERN, "[EMAIL_REDACTED]", text)

        # Redact GPS coordinates (before phone — coords like 121.5654
        # would otherwise match the phone pattern)
        text = re.sub(
            PIIRedactor.GPS_PATTERN, "[LOCATION_REDACTED]", text
        )

        # Redact Taiwan National IDs (before phone — 10-char
        # alphanumeric IDs could partially overlap digit patterns)
        text = re.sub(
            PIIRedactor.TAIWAN_ID_PATTERN, "[ID_REDACTED]", text
        )

        # Redact phone numbers
        text = re.sub(PIIRedactor.PHONE_PATTERN, "[PHONE_REDACTED]", text)

        # Redact street addresses
        text = re.sub(
            PIIRedactor.ADDRESS_PATTERN, "[ADDRESS_REDACTED]", text
        )

        # Redact plate numbers
        text = re.sub(
            PIIRedactor.PLATE_PATTERN, "[PLATE_REDACTED]", text
        )

        # Redact keyword-triggered names (replace only the name part)
        text = re.sub(
            PIIRedactor.NAME_PATTERN,
            lambda m: m.group(0).replace(m.group(1), "[NAME_REDACTED]"),
            text,
            flags=re.IGNORECASE,
        )

        return text

    @staticmethod
    def enforce_data_boundary(payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Recursively filter payload to only include allowed fields
        and redact strings within them.
        """
        safe_payload = {}
        
        for key, value in payload.items():
            if key in PIIRedactor.ALLOWED_FIELDS:
                # Recursively handle nested dicts or lists
                safe_payload[key] = PIIRedactor._sanitize_value(value)
            # Else: Drop the field (Data Boundary)
            
        return safe_payload

    @staticmethod
    def _sanitize_value(value: Any) -> Any:
        """Helper to sanitize values recursively."""
        if isinstance(value, str):
            return PIIRedactor.redact_text(value)
        elif isinstance(value, dict):
             # Recursively filter nested dicts? 
             # For strictness, maybe we don't want arbitrary nested dicts unless checked.
             # But let's allow nested dicts but filter their keys too.
             return PIIRedactor.enforce_data_boundary(value)
        elif isinstance(value, list):
            return [PIIRedactor._sanitize_value(v) for v in value]
        else:
            # Numbers, bools are safe as-is
            return value

# Singleton for easy use
redactor = PIIRedactor()
