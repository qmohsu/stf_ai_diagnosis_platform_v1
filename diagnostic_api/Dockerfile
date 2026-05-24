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

# Copy Alembic migrations + config so `alembic upgrade head` can
# run inside the container (HARNESS-17 needs this for the
# golden_entries / golden_reviews tables; future migrations also
# benefit).  Without these files the deploy procedure required a
# manual `podman cp` step before every migration.
COPY diagnostic_api/alembic/ /app/alembic/
COPY diagnostic_api/alembic.ini /app/alembic.ini

# Copy the canonical golden JSONL set so the startup sync hook in
# main.py (services/golden_sync.py) can populate the
# golden_entries table on every boot.  Surgical copy — we
# deliberately do NOT pull in the rest of tests/ (test_*.py files
# are not needed in the runtime image and would bloat it).  The
# candidates/, source/, exports/ subdirectories are sometimes
# gitignored author drafts; the sync skips candidates/ explicitly.
COPY diagnostic_api/tests/harness/evals/golden/v2/ /app/tests/harness/evals/golden/v2/

# HARNESS-21 [2b/4]: also copy the Yamaha reference-stats sidecar
# from the v1/ tree so the /v2/goldens/obd/reference-stats endpoint
# can read it.  The sidecar is a precomputed JSON of per-signal
# stats + event windows generated offline by
# scripts/compute_yamaha_reference.py; the /goldens/obd detail page
# uses it for the sparkline render.  v1/yamaha_road_test.jsonl
# itself is NOT copied — that's just author-side input to commit 4's
# v2/ seed.
COPY diagnostic_api/tests/harness/evals/golden/v1/yamaha_road_test_reference.json /app/tests/harness/evals/golden/v1/yamaha_road_test_reference.json

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
