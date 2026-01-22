"""Main FastAPI application for diagnostic API.

Author: Li-Ta Hsu
Date: January 2026

This is a Phase 1 stub implementation that provides:
- Health check endpoint
- Placeholder diagnostic endpoint
- Placeholder RAG retrieval endpoint

Full implementation will be completed in subsequent phases.
"""

import logging
import uuid
from datetime import datetime
from typing import Dict

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.models import (
    DiagnosticRequest,
    DiagnosticResponse,
    HealthResponse,
    RAGChunk,
    RAGRetrieveRequest,
    RAGRetrieveResponse,
    SubsystemRisk,
)

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Initialize FastAPI app
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="STF AI Diagnosis Platform - Diagnostic API",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Configure CORS (localhost only for Phase 1)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3000", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup_event() -> None:
    """Application startup handler.

    Performs initialization tasks:
    - Log startup message
    - Verify connectivity to dependencies
    """
    logger.info(
        f"Starting {settings.app_name} v{settings.app_version}"
    )
    logger.info(f"Database: {settings.db_host}:{settings.db_port}")
    logger.info(f"LLM Endpoint: {settings.llm_endpoint}")
    logger.info(f"Weaviate: {settings.weaviate_url}")
    logger.info(f"Strict Mode: {settings.strict_mode}")
    logger.info(f"Redact PII: {settings.redact_pii}")


@app.on_event("shutdown")
async def shutdown_event() -> None:
    """Application shutdown handler.

    Performs cleanup tasks:
    - Log shutdown message
    - Close database connections
    """
    logger.info(f"Shutting down {settings.app_name}")


@app.get("/", tags=["Root"])
async def root() -> Dict[str, str]:
    """Root endpoint.

    Returns:
        Welcome message with API information
    """
    return {
        "message": "STF AI Diagnosis Platform - Diagnostic API",
        "version": settings.app_version,
        "docs": "/docs",
        "health": "/health",
    }


@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["Health"],
    status_code=status.HTTP_200_OK,
)
async def health_check() -> HealthResponse:
    """Health check endpoint.

    Returns:
        Health status of the API and its dependencies

    Raises:
        HTTPException: If critical services are unavailable
    """
    services_status = {
        "api": "healthy",
        "database": "healthy",  # TODO: Add real DB check
        "weaviate": "healthy",  # TODO: Add real Weaviate check
        "llm": "healthy",  # TODO: Add real LLM check
    }

    # Check if any critical service is unhealthy
    all_healthy = all(
        status == "healthy" for status in services_status.values()
    )

    return HealthResponse(
        status="healthy" if all_healthy else "degraded",
        timestamp=datetime.utcnow(),
        version=settings.app_version,
        services=services_status,
    )


@app.post(
    "/v1/vehicle/diagnose",
    response_model=DiagnosticResponse,
    tags=["Diagnostics"],
    status_code=status.HTTP_200_OK,
)
async def diagnose_vehicle(
    request: DiagnosticRequest,
) -> DiagnosticResponse:
    """Perform vehicle diagnostics.

    This is a Phase 1 stub implementation that returns mock data.
    Full implementation will integrate with:
    - Local LLM (Ollama)
    - RAG retrieval (Weaviate)
    - Sensor data processing

    Args:
        request: Diagnostic request with vehicle ID and time range

    Returns:
        Diagnostic response with risk assessments and recommendations

    Raises:
        HTTPException: If request validation fails or processing error
    """
    logger.info(
        f"Diagnostic request for vehicle: {request.vehicle_id}"
    )

    # Phase 1 stub: Return mock diagnostic response
    session_id = uuid.uuid4()

    # Mock subsystem risk assessment
    subsystem_risks = [
        SubsystemRisk(
            subsystem_name="powertrain",
            risk_level=0.25,
            confidence=0.80,
            predicted_faults=["P0171", "P0174"],
        ),
        SubsystemRisk(
            subsystem_name="braking",
            risk_level=0.10,
            confidence=0.90,
            predicted_faults=[],
        ),
    ]

    # Mock recommendations
    recommendations = [
        "Check fuel system for lean condition (P0171/P0174)",
        "Inspect MAF sensor and air intake system",
        "Schedule follow-up inspection in 1000 km",
    ]

    # Mock limitations
    limitations = [
        "Limited sensor data available for time range",
        "Phase 1 stub - using mock data",
    ]

    return DiagnosticResponse(
        session_id=session_id,
        vehicle_id=request.vehicle_id,
        timestamp=datetime.utcnow(),
        subsystem_risks=subsystem_risks,
        recommendations=recommendations,
        key_evidence=[],
        limitations=limitations,
        confidence=0.75,
    )


@app.post(
    "/v1/rag/retrieve",
    response_model=RAGRetrieveResponse,
    tags=["RAG"],
    status_code=status.HTTP_200_OK,
)
async def retrieve_rag_chunks(
    request: RAGRetrieveRequest,
) -> RAGRetrieveResponse:
    """Retrieve relevant chunks from RAG vector store.

    This is a Phase 1 stub implementation that returns mock data.
    Full implementation will query Weaviate for relevant chunks.

    Args:
        request: RAG retrieval request with query and parameters

    Returns:
        Retrieved chunks with doc_id#section metadata

    Raises:
        HTTPException: If retrieval fails
    """
    logger.info(f"RAG retrieval request: {request.query}")

    # Phase 1 stub: Return mock chunks
    mock_chunks = [
        RAGChunk(
            doc_id="SOP_2024_001",
            section="section_3_2",
            text="For P0171 fault code, verify MAF sensor operation...",
            score=0.92,
            metadata={"doc_type": "sop", "year": "2024"},
        ),
        RAGChunk(
            doc_id="MANUAL_V8_ENGINE",
            section="troubleshooting_fuel",
            text="Lean fuel mixture conditions may indicate...",
            score=0.87,
            metadata={"doc_type": "manual", "model": "V8"},
        ),
    ]

    return RAGRetrieveResponse(
        query=request.query,
        chunks=mock_chunks[: request.top_k],
        total_results=len(mock_chunks),
    )


@app.get("/v1/models", tags=["LLM"])
async def list_models() -> Dict[str, str]:
    """List available LLM models.

    Returns:
        Available models and current default model
    """
    return {
        "default_model": settings.llm_model,
        "endpoint": settings.llm_endpoint,
        "status": "stub - Phase 1",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level=settings.log_level.lower(),
    )
