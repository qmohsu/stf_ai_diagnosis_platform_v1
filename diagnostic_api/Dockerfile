# ============================================================================
# STF AI Diagnosis Platform - Diagnostic API Dockerfile
# Author: Li-Ta Hsu
# Date: January 2026
# ============================================================================
# Multi-stage build for optimized image size
# Build context must be the repository root (../) so obd_agent is accessible.
# ============================================================================

FROM python:3.11-slim as builder

# Set working directory
WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python dependencies
COPY diagnostic_api/requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# Install obd_agent as a local package (log_summarizer + log_parser only).
# NOTE: obd_agent's pyproject.toml must NOT depend on python-OBD (GPL-2.0).
# Only the Pydantic-based summariser modules are used here.
COPY obd_agent/ /build/obd_agent/
RUN pip install --no-cache-dir --user /build/obd_agent/

# ============================================================================
# Runtime stage
# ============================================================================
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy Python dependencies from builder
COPY --from=builder /root/.local /root/.local

# Copy application code
COPY diagnostic_api/app/ /app/app/
COPY diagnostic_api/data/ /app/data/


# Create logs directory
RUN mkdir -p /app/logs

# Set environment variables
ENV PATH=/root/.local/bin:$PATH
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
