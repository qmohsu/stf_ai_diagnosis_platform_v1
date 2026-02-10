"""Main FastAPI application for diagnostic API.

Author: Li-Ta Hsu
Date: January 2026

This is a Phase 1 stub implementation that provides:
- Health check endpoint
- Placeholder diagnostic endpoint (with Postgres persistence)
- Placeholder RAG retrieval endpoint

Full implementation will be completed in subsequent phases.
"""

import logging
import uuid
from datetime import datetime
from typing import Dict

from fastapi import FastAPI, HTTPException, status, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import SessionLocal, engine
from app.db import base
from app import crud
from app.models import (
    DiagnosticRequest,
    DiagnosticResponse,
    HealthResponse,
    RAGChunk,
    RAGRetrieveRequest,
    RAGRetrieveResponse,
    SubsystemRisk,
)
from app import models_db

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


# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


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
    db: Session = Depends(get_db),
) -> DiagnosticResponse:
    """Perform vehicle diagnostics.

    This is a Phase 1.5 implementation that:
    1. Persists the request to Postgres (PENDING)
    2. Runs stub logic (returning mock data)
    3. Persists the result to Postgres (COMPLETED)

    Args:
        request: Diagnostic request with vehicle ID and time range
        db: Database session

    Returns:
        Diagnostic response with risk assessments and recommendations
    """
    logger.info(
        f"Diagnostic request for vehicle: {request.vehicle_id}"
    )

    # 1. Persist Request (PENDING)
    db_session = crud.create_diagnostic_session(db, request)

    # 2. Run Diagnosis (Logic Placeholder)
    # Phase 2 will integrate LLM/RAG here.
    
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
    
    response = DiagnosticResponse(
        session_id=db_session.id,
        vehicle_id=request.vehicle_id,
        timestamp=datetime.utcnow(),
        subsystem_risks=subsystem_risks,
        recommendations=recommendations,
        key_evidence=[],
        limitations=limitations,
        confidence=0.75,
    )

    # 3. Persist Result (COMPLETED)
    crud.update_diagnostic_result(db, db_session.id, response)

    return response


@app.get(
    "/v1/vehicle/diagnose/{session_id}",
    response_model=DiagnosticResponse,
    tags=["Diagnostics"],
    status_code=status.HTTP_200_OK,
)
async def get_diagnosis_result(
    session_id: uuid.UUID,
    db: Session = Depends(get_db),
) -> DiagnosticResponse:
    """Retrieve a past diagnosis result by session ID."""
    db_session = crud.get_diagnostic_session(db, session_id)
    if not db_session:
        raise HTTPException(status_code=404, detail="Diagnosis session not found")
        
    if db_session.status != "COMPLETED" or not db_session.result_payload:
         raise HTTPException(status_code=400, detail="Diagnosis processing not complete")
         
    # Reconstruct Pydantic model from stored JSON
    try:
        return DiagnosticResponse(**db_session.result_payload)
    except Exception as e:
        logger.error(f"Failed to parse stored result: {e}")
        raise HTTPException(status_code=500, detail="Corrupted session data")


from app.api.v1.endpoints import rag, diagnose, feedback, tools, log_summary

# Include routers
app.include_router(rag.router, prefix="/v1/rag", tags=["RAG"])
app.include_router(diagnose.router, prefix="/v1/diagnose", tags=["Diagnostics"])
app.include_router(feedback.router, prefix="/v1/feedback", tags=["Feedback"])
# /v1/tools owns: tools → /redact, /validate-vin; log_summary → /summarize-log
app.include_router(tools.router, prefix="/v1/tools", tags=["Tools"])
app.include_router(log_summary.router, prefix="/v1/tools", tags=["Tools"])

from app.api.v2.endpoints import log_summary as log_summary_v2
app.include_router(log_summary_v2.router, prefix="/v2/tools", tags=["Tools v2"])


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
