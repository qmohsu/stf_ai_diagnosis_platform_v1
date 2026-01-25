"""Privacy and PII redaction utilities."""

import re
from typing import Dict, Any, List, Union

class PIIRedactor:
    """Handles PII detection and redaction."""

    # Regex patterns for common PII
    EMAIL_PATTERN = r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"
    # Matches 10-digit (555-555-5555) or 7-digit with separator (555-5555)
    PHONE_PATTERN = r"\b(?:\(?\d{3}\)?[-.\s]?)?\d{3}[-.\s]\d{4}\b"
    
    # Simple whitelist of fields allowed to reach the LLM
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

    @staticmethod
    def redact_text(text: str) -> str:
        """Redact PII from a string."""
        if not text:
            return ""
        
        # Redact emails
        text = re.sub(PIIRedactor.EMAIL_PATTERN, "[EMAIL_REDACTED]", text)
        
        # Redact phone numbers
        text = re.sub(PIIRedactor.PHONE_PATTERN, "[PHONE_REDACTED]", text)
        
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
