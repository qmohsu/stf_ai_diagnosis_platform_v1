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
# PROD-10: the golden eval scores verbosity in cl100k_base tokens; bake the
# vocabulary into the image so scoring never depends on a download (FM-24).
ENV TIKTOKEN_CACHE_DIR=/app/.tiktoken
RUN python -c "import tiktoken; tiktoken.get_encoding('cl100k_base')"

COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts
COPY tests ./tests
# PROD-10: golden sets + road-test fixture + manifest + thresholds (/app/evals).
COPY evals ./evals

# Build-time commit stamp, read by deploy_check.sh and reported by /health.
ARG GIT_COMMIT=unknown
ENV STF_V3_GIT_COMMIT=${GIT_COMMIT}
LABEL org.opencontainers.image.revision=${GIT_COMMIT} \
      org.opencontainers.image.title="stf-v3"

EXPOSE 8002
# API by default; the worker container overrides with `procrastinate ... worker`.
CMD ["uvicorn", "stf_v3.main:app", "--host", "0.0.0.0", "--port", "8002"]
