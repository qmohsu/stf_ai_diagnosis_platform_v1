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
    redacted = redactor.redact_text(original)
    
    # Calculate simple stats (count of redacted tokens)
    # This is a rough proxy since redact_text doesn't return count
    count = redacted.count("[EMAIL_REDACTED]") + redacted.count("[PHONE_REDACTED]")
    
    return RedactResponse(
        original_text=original,
        redacted_text=redacted,
        redacted_count=count
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
        
    invalid_chars = set("IOQ") # VINs cannot contain I, O, or Q
    found_invalid = [c for c in vin if c in invalid_chars]
    
    if found_invalid:
         return VinValidateResponse(
            vin=vin,
            is_valid=False,
            details={"error": f"Invalid characters found: {found_invalid} (VINs cannot contain I, O, Q)."}
        )

    return VinValidateResponse(
        vin=vin,
        is_valid=True,
        details={"standard": "ISO 3779"}
    )
