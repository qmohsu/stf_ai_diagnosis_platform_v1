import re

from fastapi import APIRouter, HTTPException
from app.api.v1.schemas import RedactRequest, RedactResponse, VinValidateRequest, VinValidateResponse
from app.privacy.redaction import redactor

router = APIRouter()

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
    Phase 1: Simple format check.
    """
    vin = request.vin.upper().strip()
    
    if len(vin) != 17:
        return VinValidateResponse(
            vin=vin,
            is_valid=False,
            details={"error": f"Invalid length: {len(vin)}. Expected 17."}
        )
        
    # VINs must be alphanumeric, excluding I, O, Q (ISO 3779)
    if not re.fullmatch(r'[A-HJ-NPR-Z0-9]{17}', vin):
        return VinValidateResponse(
            vin=vin,
            is_valid=False,
            details={"error": "VIN must contain only alphanumeric characters (A-Z, 0-9, excluding I, O, Q)."}
        )

    return VinValidateResponse(
        vin=vin,
        is_valid=True,
        details={"standard": "ISO 3779"}
    )
