# STF V3 backend image (self-contained: builds from stf_v3/ only).
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install ".[dev]"

COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts
COPY tests ./tests

# PROD-02: the image only carries the schema tooling.  PROD-03 switches the
# default command to uvicorn; the worker uses `procrastinate ... worker`.
CMD ["alembic", "current"]
