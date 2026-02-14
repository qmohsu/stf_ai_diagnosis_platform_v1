"""Privacy and PII redaction utilities."""

import re
from dataclasses import dataclass
from typing import Dict, Any, List

import structlog

logger = structlog.get_logger()


@dataclass
class RedactionResult:
    """Result of a PII redaction operation."""
    text: str
    total_count: int
    email_count: int
    phone_count: int
    name_count: int
    location_count: int
    plate_count: int
    id_count: int

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


    # Max characters allow per string field (prevent ReDoS and context overflow)
    MAX_TEXT_LENGTH = 10000

    @staticmethod
    def redact_text(text: str) -> str:
        """Redact PII from a string. Returns only the redacted text."""
        return PIIRedactor.redact_text_with_stats(text).text

    @staticmethod
    def redact_text_with_stats(text: str) -> RedactionResult:
        """Redact PII from a string and return stats on what was redacted."""
        empty = RedactionResult(
            text="", total_count=0, email_count=0,
            phone_count=0, name_count=0, location_count=0,
            plate_count=0, id_count=0,
        )
        if not text:
            return empty

        # Prevent ReDoS / resource exhaustion
        if len(text) > PIIRedactor.MAX_TEXT_LENGTH:
            text = text[:PIIRedactor.MAX_TEXT_LENGTH] + " ... [TRUNCATED_DUE_TO_SIZE]"

        # Redact emails
        text, email_count = re.subn(
            PIIRedactor.EMAIL_PATTERN, "[EMAIL_REDACTED]", text
        )

        # Redact GPS coordinates (before phone — coords like 121.5654
        # would otherwise match the phone pattern)
        text, gps_count = re.subn(
            PIIRedactor.GPS_PATTERN, "[LOCATION_REDACTED]", text
        )

        # Redact Taiwan National IDs (before phone — 10-char
        # alphanumeric IDs could partially overlap digit patterns)
        text, id_count = re.subn(
            PIIRedactor.TAIWAN_ID_PATTERN, "[ID_REDACTED]", text
        )

        # Redact phone numbers
        text, phone_count = re.subn(
            PIIRedactor.PHONE_PATTERN, "[PHONE_REDACTED]", text
        )

        # Redact street addresses
        text, address_count = re.subn(
            PIIRedactor.ADDRESS_PATTERN, "[ADDRESS_REDACTED]", text
        )

        # Redact plate numbers
        text, plate_count = re.subn(
            PIIRedactor.PLATE_PATTERN, "[PLATE_REDACTED]", text
        )

        # Redact keyword-triggered names (replace only the name part)
        # re.subn with a callable replacer still returns the count
        text, name_count = re.subn(
            PIIRedactor.NAME_PATTERN,
            lambda m: m.group(0).replace(m.group(1), "[NAME_REDACTED]"),
            text,
            flags=re.IGNORECASE,
        )

        location_count = gps_count + address_count
        total = (
            email_count + phone_count + name_count
            + location_count + plate_count + id_count
        )

        # Log redaction event (counts only, never raw PII)
        if total > 0:
            logger.info(
                "pii_redaction_applied",
                total_redacted=total,
                email_count=email_count,
                phone_count=phone_count,
                name_count=name_count,
                location_count=location_count,
                plate_count=plate_count,
                id_count=id_count,
            )

        return RedactionResult(
            text=text,
            total_count=total,
            email_count=email_count,
            phone_count=phone_count,
            name_count=name_count,
            location_count=location_count,
            plate_count=plate_count,
            id_count=id_count,
        )

    @staticmethod
    def enforce_data_boundary(payload: Dict[str, Any]) -> Dict[str, Any]:
        """Backward-compat wrapper. Use FeatureBoundary.enforce()."""
        from app.privacy.feature_boundary import FeatureBoundary
        return FeatureBoundary.enforce(payload)

# Singleton for easy use
redactor = PIIRedactor()
