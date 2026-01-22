"""Pydantic models for diagnostic API.

Author: Li-Ta Hsu
Date: January 2026

These models define the JSON schema v1.0 for the diagnostic API.
All models follow Google Python Style Guide naming conventions.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, validator


class HealthResponse(BaseModel):
    """Health check response model.

    Returns:
        status: Health status ("healthy" or "unhealthy")
        timestamp: ISO timestamp of the health check
        version: API version string
    """

    status: str = Field(..., description="Health status")
    timestamp: datetime = Field(
        default_factory=datetime.utcnow, description="Check timestamp"
    )
    version: str = Field(..., description="API version")
    services: Dict[str, str] = Field(
        default_factory=dict, description="Service connectivity status"
    )


class DiagnosticRequest(BaseModel):
    """Request model for vehicle diagnostics.

    Args:
        vehicle_id: Pseudonymous vehicle identifier (e.g., 'V12345')
        time_range: Dict with 'start' and 'end' ISO timestamp strings
        subsystems: Optional list of subsystems to diagnose
        include_evidence: Whether to include detailed evidence
    """

    vehicle_id: str = Field(..., description="Vehicle identifier")
    time_range: Dict[str, str] = Field(
        ..., description="Time range for diagnosis"
    )
    subsystems: Optional[List[str]] = Field(
        None, description="Specific subsystems to diagnose"
    )
    include_evidence: bool = Field(
        True, description="Include evidence in response"
    )

    @validator("vehicle_id")
    def validate_vehicle_id(cls, v: str) -> str:
        """Validate vehicle ID format.

        Args:
            v: Vehicle ID string to validate

        Returns:
            Validated vehicle ID

        Raises:
            ValueError: If vehicle ID format is invalid
        """
        if not v or len(v) < 3:
            raise ValueError("Vehicle ID must be at least 3 characters")
        return v


class SubsystemRisk(BaseModel):
    """Risk assessment for a vehicle subsystem.

    Args:
        subsystem_name: Name of the subsystem
        risk_level: Risk level (0.0 to 1.0)
        confidence: Confidence score (0.0 to 1.0)
        predicted_faults: List of predicted fault codes
    """

    subsystem_name: str = Field(..., description="Subsystem identifier")
    risk_level: float = Field(
        ..., ge=0.0, le=1.0, description="Risk level"
    )
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Confidence score"
    )
    predicted_faults: List[str] = Field(
        default_factory=list, description="Predicted fault codes"
    )


class Evidence(BaseModel):
    """Evidence supporting a diagnostic finding.

    Args:
        doc_id: Source document ID
        section: Section identifier within document
        text_snippet: Relevant text excerpt
        relevance_score: Relevance score (0.0 to 1.0)
    """

    doc_id: str = Field(..., description="Source document ID")
    section: str = Field(..., description="Section identifier")
    text_snippet: str = Field(..., description="Text excerpt")
    relevance_score: float = Field(
        ..., ge=0.0, le=1.0, description="Relevance score"
    )


class DiagnosticResponse(BaseModel):
    """Response model for vehicle diagnostics (JSON Schema v1.0).

    Returns:
        session_id: Unique session identifier
        vehicle_id: Vehicle identifier from request
        timestamp: ISO timestamp of diagnosis
        subsystem_risks: List of subsystem risk assessments
        recommendations: List of recommended actions
        key_evidence: Supporting evidence with citations
        limitations: Known limitations or uncertainties
        confidence: Overall confidence score
    """

    session_id: UUID = Field(..., description="Session identifier")
    vehicle_id: str = Field(..., description="Vehicle identifier")
    timestamp: datetime = Field(
        default_factory=datetime.utcnow, description="Diagnosis timestamp"
    )
    subsystem_risks: List[SubsystemRisk] = Field(
        default_factory=list, description="Subsystem risk assessments"
    )
    recommendations: List[str] = Field(
        default_factory=list, description="Recommended actions"
    )
    key_evidence: List[Evidence] = Field(
        default_factory=list, description="Supporting evidence"
    )
    limitations: List[str] = Field(
        default_factory=list, description="Known limitations"
    )
    confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Overall confidence"
    )


class RAGRetrieveRequest(BaseModel):
    """Request model for RAG retrieval.

    Args:
        query: Natural language query
        top_k: Number of results to return
        filters: Optional filters for retrieval
    """

    query: str = Field(..., description="Search query")
    top_k: int = Field(5, ge=1, le=20, description="Number of results")
    filters: Optional[Dict[str, Any]] = Field(
        None, description="Optional filters"
    )


class RAGChunk(BaseModel):
    """A single chunk from RAG retrieval.

    Returns:
        doc_id: Source document ID
        section: Section identifier
        text: Chunk text content
        score: Relevance score
        metadata: Additional metadata
    """

    doc_id: str = Field(..., description="Document ID")
    section: str = Field(..., description="Section identifier")
    text: str = Field(..., description="Chunk text")
    score: float = Field(..., description="Relevance score")
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata"
    )


class RAGRetrieveResponse(BaseModel):
    """Response model for RAG retrieval.

    Returns:
        query: Original query
        chunks: List of retrieved chunks with citations
        total_results: Total number of results found
    """

    query: str = Field(..., description="Original query")
    chunks: List[RAGChunk] = Field(
        default_factory=list, description="Retrieved chunks"
    )
    total_results: int = Field(..., description="Total results count")
