# STF AI Diagnosis Platform — Project Rules

## Project Overview

Phase 1 local-first pilot for AI-assisted vehicle diagnosis.

- **Stack**: FastAPI + Pydantic backend (`diagnostic_api/`), Weaviate vector store, Ollama/vLLM local LLM, Dify workflow orchestration (upstream — do NOT fork), Docker Compose infrastructure
- **Author field**: Li-Ta Hsu
- **Runtime**: No public internet access. All services run locally (127.0.0.1 only)

## Privacy & Data Boundaries (Non-Negotiable)

- NEVER send raw sensor data to the LLM (no vibration waveforms, audio frames, video frames, full GNSS tracks)
- LLM context may contain ONLY summaries, risk scores, and text snippets
- Redact PII (names, phone numbers, unredacted location details) before passing to the LLM or writing training-ready logs
- All raw sensor data stays in the backend; only derived features and summaries are LLM-safe

## Python Coding Standards (Google Style)

Follow the [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html).

**Naming**:
- `lower_with_under` for functions, methods, variables, modules
- `CapWords` for classes
- `UPPER_WITH_UNDER` for constants
- Leading underscore `_` for private attributes/methods

**Formatting**:
- 4 spaces indentation, no tabs
- Line length <= 80 characters
- 2 blank lines between top-level definitions, 1 between methods

**Imports**:
- Absolute imports only (no `from module import *`)
- Group in order: stdlib, third-party, local — separated by blank lines, alphabetical within groups

**Type Hints**:
- Mandatory for all function parameters and return values
- Use `typing` module for complex types
- All Pydantic models must have explicit type annotations

**Docstrings** (Google style, mandatory for all public functions/classes):
```python
def diagnose_vehicle(vehicle_id: str, time_range: dict) -> dict:
    """Retrieves diagnostic summary for a specific vehicle.

    Args:
        vehicle_id: Pseudonymous vehicle identifier (e.g., 'V12345').
        time_range: Dict with 'start' and 'end' ISO timestamp strings.

    Returns:
        Dict containing subsystem_risk, predicted_faults, confidence,
        key_evidence, and limitations fields per JSON schema v1.0.

    Raises:
        ValueError: If vehicle_id format is invalid.
        DataMissingError: If no sensor data exists for time_range.
    """
```

**Error Handling**:
- Use specific exception types, never bare `except:`
- Use `with` statements for all file/resource handling
- Define custom exceptions for domain errors: `DataMissingError`, `SchemaValidationError`, `CitationMissingError`
- Log errors with context (vehicle_id, timestamp, error type)
- Never allow silent failures

## Schema & Validation

- Use Pydantic models for all API input/output validation
- All data exchange between diagnostic_api, LLM, and UI must adhere to JSON schema v1.0
- When producing expert output, return JSON only (no markdown)
- Recommendations must include citations (`doc_id#section`) or explicitly indicate `NO_SOURCE`
- If schema validation fails, repair and retry once; do not loop

## Testing Standards

- Framework: pytest
- Tests live under `diagnostic_api/tests/` mirroring source structure
- Descriptive names: `test_diagnostic_api_returns_valid_json`
- Every test function must have a docstring explaining intent
- Arrange / Act / Assert pattern
- No external network calls in unit tests
- Validate: schema correctness, redaction behavior, error handling

## Repo Structure

```
diagnostic_api/   # FastAPI backend
  app/rag/        # RAG ingestion, chunking, retrieval
  app/            # API endpoints, models
  tests/          # Unit/integration tests
dify/             # Dify workflow specs
infra/            # Docker Compose, env configs, scripts
docs/             # Architecture docs, setup guides
obd_agent/        # OBD-II edge agent
```

## RAG & Citation Logic

- Every text chunk must retain source metadata (`doc_id`, `section_anchor`)
- Fail gracefully or flag a warning if LLM output lacks a valid citation
- All retrieved chunks must include traceable references

## OBD Pipeline Rules

- OBD streams go through a two-pass reduction before reaching the LLM:
  1. **Subsystem mapping**: DTC family + symptoms → candidate PIDs (10-25)
  2. **Ranking**: Compute features (robust z-score, trend, volatility), keep Top-K (K=15) only
- Evidence Pack is a strictly validated Pydantic model — no raw arrays, no per-sample data
- Every selected signal must include a `why_selected` string from deterministic rules
- If baseline is missing, fall back to window-only scoring and add a `limitations[]` entry
- Mode 06 failures rank above ordinary PID anomalies when aligned with suspected subsystem

## Infrastructure & DevOps

- Local-only deployment (bind ports to 127.0.0.1)
- Pin all versions in Docker Compose and configs (no `latest`)
- DO NOT fork Dify — use official Docker deployment pinned to a release tag
- Use dedicated internal Docker network for app-to-app traffic
- Only Nginx (or Dify web) handles ingress; do not expose Weaviate/Postgres/Redis/diagnostic_api to LAN
- Named Docker volumes for persistence (Postgres, Weaviate, Dify storage)

## Structured Logging

- All workflow nodes and API endpoints must include structured logging
- Log: `user_input`, `retrieved_chunks` (with doc_id), `tool_outputs`, `final_response_json`
- Log to persistent file or database, not just console
- Use structlog with JSON formatting
- Never log secrets or PII

## Change Discipline

- Never commit secrets — use `.env.example` and gitignore real `.env`
- Prefer deterministic, testable behavior; fail safe when inputs are missing
