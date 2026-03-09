"""Main FastAPI application for diagnostic API.

Author: Li-Ta Hsu
Date: January 2026

Provides:
- Health check endpoint
- V1 RAG retrieval endpoint
- V2 OBD analysis, diagnosis, feedback, and premium endpoints
- JWT authentication
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Dict

from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.models import HealthResponse
from app.cache import obd_cache

# Configure logging
logging.basicConfig(
    level=getattr(logging, settings.log_level),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan handler.

    Manages startup and shutdown tasks:
    - Startup: log config, validate JWT secret, start cache cleanup
    - Shutdown: stop cache cleanup, log shutdown
    """
    # --- Startup ---
    logger.info(
        f"Starting {settings.app_name} v{settings.app_version}"
    )
    logger.info(f"Database: {settings.db_host}:{settings.db_port}")
    logger.info(f"LLM Endpoint: {settings.llm_endpoint}")
    logger.info(f"Weaviate: {settings.weaviate_url}")
    logger.info(f"Strict Mode: {settings.strict_mode}")
    settings.validate_jwt_secret()
    await obd_cache.start_cleanup_loop()

    yield

    # --- Shutdown ---
    await obd_cache.stop_cleanup_loop()
    logger.info(f"Shutting down {settings.app_name}")


# Initialize FastAPI app
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="STF AI Diagnosis Platform - Diagnostic API",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# Configure CORS (localhost only for Phase 1)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:3000",
        "http://localhost:3000",
        "http://127.0.0.1:3001",
        "http://localhost:3001",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
        "database": "healthy",  # TODO(APP-29): real DB check
        "weaviate": "healthy",  # TODO(APP-29): real Weaviate check
        "llm": "healthy",  # TODO(APP-29): real LLM check
    }

    # Check if any critical service is unhealthy
    all_healthy = all(
        s == "healthy" for s in services_status.values()
    )

    return HealthResponse(
        status="healthy" if all_healthy else "degraded",
        timestamp=datetime.now(timezone.utc),
        version=settings.app_version,
        services=services_status,
    )


# --- Authentication ---
from app.auth.router import router as auth_router

app.include_router(
    auth_router, prefix="/auth", tags=["Authentication"],
)

# --- V1 Endpoints (RAG only) ---
from app.api.v1.endpoints import rag

app.include_router(rag.router, prefix="/v1/rag", tags=["RAG"])

# --- V2 Endpoints ---
from app.api.v2.endpoints import log_summary as log_summary_v2
app.include_router(
    log_summary_v2.router, prefix="/v2/tools", tags=["Tools v2"],
)

from app.api.v2.endpoints import obd_analysis as obd_analysis_v2
app.include_router(
    obd_analysis_v2.router, prefix="/v2/obd", tags=["OBD Analysis"],
)

from app.api.v2.endpoints import obd_premium as obd_premium_v2
app.include_router(
    obd_premium_v2.router, prefix="/v2/obd", tags=["OBD Premium"],
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=8000,
        log_level=settings.log_level.lower(),
    )
