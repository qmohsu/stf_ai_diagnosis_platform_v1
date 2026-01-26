
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from app.expert.schemas import LLMDiagnosisResponse

class DiagnosisRequest(BaseModel):
    """
    Request payload for the end-to-end diagnosis endpoint.
    """
    vehicle_id: str = Field(..., description="Unique identifier for the vehicle (VIN or ID)")
    make: str = Field(..., description="Vehicle Make (e.g. Ford)")
    model: str = Field(..., description="Vehicle Model (e.g. F-150)")
    year: int = Field(..., description="Vehicle Year")
    mileage: Optional[int] = Field(None, description="Current Mileage")
    symptoms: str = Field(..., description="Description of the problem reported by the driver or technician")
    dtc_codes: Optional[List[str]] = Field(default_factory=list, description="List of Diagnostic Trouble Codes (e.g. P0300)")

    def to_vehicle_string(self) -> str:
        """Helper to format vehicle info for the prompt."""
        return f"{self.year} {self.make} {self.model}, Mileage: {self.mileage or 'Unknown'}"

class DiagnosisResponse(BaseModel):
    """
    Final response returned to the client.
    """
    diagnosis: LLMDiagnosisResponse
    redacted_symptoms: str = Field(..., description="The symptom description AFTER PII redaction")
    context_used: bool = Field(..., description="Whether RAG context was successfully retrieved and used")

class FeedbackRequest(BaseModel):
    """
    Payload for technician feedback.
    """
    session_id: str = Field(..., description="UUID of the diagnostic session")
    rating: int = Field(..., ge=1, le=5, description="Rating from 1 to 5")
    is_helpful: bool = Field(..., description="Whether the diagnosis was helpful")
    comments: Optional[str] = Field(None, description="Free text comments")
    corrected_diagnosis: Optional[str] = Field(None, description="Actual correct diagnosis if AI was wrong")

class RedactRequest(BaseModel):
    text: str = Field(..., description="Text to redact PII from")

class RedactResponse(BaseModel):
    original_text: str
    redacted_text: str
    redacted_count: int

class VinValidateRequest(BaseModel):
    vin: str = Field(..., description="Vehicle Identification Number")

class VinValidateResponse(BaseModel):
    vin: str
    is_valid: bool
    details: Optional[Dict[str, Any]] = None
