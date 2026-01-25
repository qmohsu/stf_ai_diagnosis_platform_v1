"""Pydantic schemas for Expert Model outputs."""

from typing import List, Optional
from pydantic import BaseModel, Field

class Citation(BaseModel):
    """Reference to a retrieved document chunk."""
    doc_id: str
    section: str
    text_snippet: str

class SubsystemRisk(BaseModel):
    """Risk assessment for a specific vehicle subsystem."""
    subsystem_name: str
    risk_level: float = Field(..., ge=0.0, le=1.0, description="Risk score 0.0-1.0")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence in this assessment 0.0-1.0")
    predicted_faults: List[str] = Field(default_factory=list, description="List of potential fault codes or issue types")
    reasoning: str = Field(..., description="Brief explanation of why this risk level was assigned")

class LLMDiagnosisResponse(BaseModel):
    """Structured output expected from the LLM."""
    summary: str = Field(..., description="High-level diagnostic summary")
    subsystem_risks: List[SubsystemRisk]
    recommendations: List[str] = Field(..., description="Actionable next steps for the technician")
    citations: List[Citation] = Field(default_factory=list, description="References to manual/log chunks used")
    requires_human_review: bool = Field(default=False, description="Flag if the AI is unsure or detects high-risk unknowns")
