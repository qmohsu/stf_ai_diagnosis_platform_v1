# ============================================================================
# STF AI Diagnosis Platform - OBD Agent Dockerfile
# ============================================================================
# Multi-stage build matching diagnostic_api pattern.
# Uses full requirements.txt (includes GPL python-obd for live mode).
# ============================================================================

FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# ============================================================================
# Runtime stage
# ============================================================================
FROM python:3.11-slim

WORKDIR /app

# Copy Python dependencies from builder
COPY --from=builder /root/.local /root/.local

# Copy application code into a proper package directory
COPY __init__.py __main__.py config.py schemas.py \
     snapshot_builder.py api_poster.py agent_loop.py ./obd_agent/
COPY reader/ ./obd_agent/reader/
COPY fixtures/ ./obd_agent/fixtures/

ENV PATH=/root/.local/bin:$PATH
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# No port to expose -- agent is a client, not a server.

# Health check: verify the package is importable
HEALTHCHECK --interval=60s --timeout=10s --start-period=10s --retries=3 \
    CMD python -c "import obd_agent; print('ok')" || exit 1

# Run in continuous polling mode
CMD ["python", "-m", "obd_agent"]
