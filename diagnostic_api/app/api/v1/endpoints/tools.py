import re

import structlog
from fastapi import APIRouter, HTTPException
from app.api.v1.schemas import RedactRequest, RedactResponse, VinValidateRequest, VinValidateResponse
from app.privacy.redaction import redactor

logger = structlog.get_logger()

router = APIRouter()

# ISO 3779 / FMVSS 115 check-digit transliteration values
_TRANSLITERATION = {
    'A': 1, 'B': 2, 'C': 3, 'D': 4, 'E': 5, 'F': 6, 'G': 7, 'H': 8,
    'J': 1, 'K': 2, 'L': 3, 'M': 4, 'N': 5, 'P': 7, 'R': 9,
    'S': 2, 'T': 3, 'U': 4, 'V': 5, 'W': 6, 'X': 7, 'Y': 8, 'Z': 9,
}

_POSITIONAL_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]


def _vin_check_digit(vin: str) -> bool:
    """Validate VIN check digit (position 9) per ISO 3779 / FMVSS 115."""
    total = 0
    for char, weight in zip(vin, _POSITIONAL_WEIGHTS):
        value = int(char) if char.isdigit() else _TRANSLITERATION.get(char, 0)
        total += value * weight
    remainder = total % 11
    expected = 'X' if remainder == 10 else str(remainder)
    return vin[8] == expected


@router.post("/redact", response_model=RedactResponse)
def redact_pii(request: RedactRequest):
    """
    Tool: Redact personal information from text.
    """
    original = request.text
    result = redactor.redact_text_with_stats(original)

    return RedactResponse(
        original_text=original,
        redacted_text=result.text,
        redacted_count=result.total_count,
    )

@router.post("/validate-vin", response_model=VinValidateResponse)
def validate_vin(request: VinValidateRequest):
    """
    Tool: Validate a Vehicle Identification Number (VIN).
    Performs format, character, and check-digit validation per ISO 3779.
    """
    vin = request.vin.upper().strip()

    # VIN is classified as PII (SECURITY_BASELINE.md).
    # We do NOT add VIN to PIIRedactor (high false-positive risk on
    # arbitrary 17-char alphanumeric strings). Instead, we mask VIN
    # in all log output from this endpoint.
    masked = vin[:3] + "***********" + vin[-4:] if len(vin) >= 7 else "***"

    if len(vin) != 17:
        logger.info("vin_validation_performed", vin_masked=masked, is_valid=False, reason="invalid_length")
        return VinValidateResponse(
            vin=vin,
            is_valid=False,
            details={"error": f"Invalid length: {len(vin)}. Expected 17."}
        )

    # VINs must be alphanumeric, excluding I, O, Q (ISO 3779)
    if not re.fullmatch(r'[A-HJ-NPR-Z0-9]{17}', vin):
        logger.info("vin_validation_performed", vin_masked=masked, is_valid=False, reason="invalid_characters")
        return VinValidateResponse(
            vin=vin,
            is_valid=False,
            details={"error": "VIN must contain only alphanumeric characters (A-Z, 0-9, excluding I, O, Q)."}
        )

    if not _vin_check_digit(vin):
        logger.info("vin_validation_performed", vin_masked=masked, is_valid=False, reason="invalid_check_digit")
        return VinValidateResponse(
            vin=vin,
            is_valid=False,
            details={"error": "Check digit (position 9) is invalid."}
        )

    logger.info("vin_validation_performed", vin_masked=masked, is_valid=True, validation_level="full")
    return VinValidateResponse(
        vin=vin,
        is_valid=True,
        details={"standard": "ISO 3779", "validation_level": "full"}
    )
