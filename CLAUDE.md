# STF AI Diagnosis Platform — Project Rules

## Project Overview

Phase 1 local-first pilot for AI-assisted vehicle diagnosis.

- **Stack**: FastAPI + Pydantic backend (`diagnostic_api/`), pgvector (PostgreSQL) vector store, Ollama/vLLM local LLM, Docker Compose infrastructure
- **Author field**: Li-Ta Hsu
- **Runtime**: No public internet access. All services run locally (127.0.0.1 only)

## Privacy & Data Boundaries (Non-Negotiable)

- NEVER send raw sensor data to the LLM (no vibration waveforms, audio frames, video frames, full GNSS tracks)
- LLM context may contain ONLY summaries, risk scores, and text snippets
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
- Validate: schema correctness, error handling

## Repo Structure

```
diagnostic_api/   # FastAPI backend
  app/rag/        # RAG ingestion, chunking, retrieval
  app/            # API endpoints, models
  tests/          # Unit/integration tests
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
- Use dedicated internal Docker network for app-to-app traffic
- Only Nginx handles ingress; do not expose Postgres/diagnostic_api to LAN
- Named Docker volumes for persistence (Postgres, Ollama)

## Structured Logging

- All workflow nodes and API endpoints must include structured logging
- Log: `user_input`, `retrieved_chunks` (with doc_id), `tool_outputs`, `final_response_json`
- Log to persistent file or database, not just console
- Use structlog with JSON formatting
- Never log secrets or PII

## Change Discipline

- Never commit secrets — use `.env.example` and gitignore real `.env`
- Prefer deterministic, testable behavior; fail safe when inputs are missing

## Documentation Update Rule (Mandatory — Pre-Commit Gate)

**Before EVERY commit**, you MUST check whether the changes require documentation updates. There are two doc sets — route to the correct one:

### Doc routing: V1 vs V2

**V1 docs** (`docs/design_doc.md` + `docs/dev_plan.md`, ticket prefix `APP‑XX`):
- Shared infrastructure: Docker, Postgres, Ollama, Nginx, networking
- Auth (JWT, users, session isolation)
- RAG pipeline (ingestion, embedding, retrieval, PDF parsing, chunking)
- OBD agent (anomaly detection, clue generation, statistics, format normalization)
- V1 one-shot diagnosis endpoints (`/diagnose`, `/diagnose/premium`)
- Feedback, audio recording, session dashboard
- Model fine-tuning / LoRA / Phase 1.5 / Phase 2
- Deployment (PolyU server, Cloudflare Tunnel)

**V2 docs** (`docs/v2_design_doc.md` + `docs/v2_dev_plan.md`, ticket prefix `HARNESS‑XX`):
- Harness loop (agent loop, ReAct cycle)
- Tool registry and tool wrappers (`harness/`, `harness_tools/`)
- Session event log (`HarnessEventLog`)
- Context management (token budget, compaction)
- Agent diagnosis endpoint (`/diagnose/agent`)
- Graduated autonomy router (tier classification)
- Frontend agent visualization (tool-call cards, iteration counter)
- Sub-agents, skill loading, background tasks (future)

**Both doc sets** — update both if the change touches:
- `models_db.py` (shared DB models)
- `config.py` (shared configuration)
- `main.py` (router registration)
- Any module imported by both V1 endpoints and V2 harness tools

### What to update

- `docs/dev_plan.md` — Add/update the relevant ticket (APP‑XX), update scope (§1.1) if needed, update critical path (§2.2) if dependencies change, and add a changelog entry.
- `docs/design_doc.md` — Update architecture descriptions (§7.1 components, §8.3.7 endpoints/tables), update "New in this revision" field, bump version and date in document control.
- `docs/v2_dev_plan.md` — Add/update the relevant ticket (HARNESS‑XX), update scope (§1.1) if needed, and add a changelog entry.
- `docs/v2_design_doc.md` — Update the relevant section, bump version and date in document control, update "New in this revision" field.

### Pre-commit checklist

(run mentally before every `git commit`):
1. Does this commit add/change a feature, endpoint, config, or architecture? → Use routing table above to determine which docs to update.
2. Does this commit fix a bug that was introduced in the current session? → Doc update optional (fold into the parent feature's doc entry).
3. Is this a pure typo/formatting/comment-only change? → No doc update needed.

If in doubt, update the docs. A commit that changes system behavior without updating docs is incomplete. Include the doc updates **in the same commit** as the code change — not in a follow-up commit.

## Server Deployment (PolyU GPU Server)

When the user says **"deploy to server"** or **"update the server"**, follow this procedure:

1. **Pre-flight check**: Run `git status` and `git log origin/main..HEAD` locally to verify all changes are committed and pushed to `origin/main`. If there are unpushed commits or uncommitted changes, warn the user and do NOT proceed until everything is pushed.
2. **Pull on server**: `ssh polyu-gpu "cd ~/stf_ai_diagnosis_platform_v1 && git pull origin main"`
3. **Rebuild images**: `ssh polyu-gpu "cd ~/stf_ai_diagnosis_platform_v1/infra && ~/.local/bin/podman-compose -f docker-compose.yml -f docker-compose.polyu.yml build diagnostic-api obd-ui"`
4. **Force-recreate changed containers**: Podman 3.4 does NOT recreate containers when the image changes — `up -d --build` silently keeps old containers. You MUST use `down`+`up` for changed services:
   ```
   ssh polyu-gpu "cd ~/stf_ai_diagnosis_platform_v1/infra && \
     ~/.local/bin/podman-compose -f docker-compose.yml -f docker-compose.polyu.yml down && sleep 2 && \
     ~/.local/bin/podman-compose -f docker-compose.yml -f docker-compose.polyu.yml up -d postgres && sleep 5 && \
     ~/.local/bin/podman-compose -f docker-compose.yml -f docker-compose.polyu.yml up -d ollama && sleep 2 && \
     ~/.local/bin/podman-compose -f docker-compose.yml -f docker-compose.polyu.yml up -d diagnostic-api && sleep 5 && \
     ~/.local/bin/podman-compose -f docker-compose.yml -f docker-compose.polyu.yml up -d obd-ui && sleep 3 && \
     ~/.local/bin/podman-compose -f docker-compose.yml -f docker-compose.polyu.yml up -d nginx"
   ```
5. **Verify containers are fresh**: Check that `CREATED AT` timestamps are recent (within the last minute) for ALL rebuilt services. Old timestamps mean the container was NOT recreated:
   `ssh polyu-gpu "podman ps --format 'table {{.Names}} {{.CreatedAt}}'"`
6. **Health checks**: Verify all 5 services are healthy:
   - `curl -sf http://127.0.0.1:11434/api/version` (Ollama)
   - `curl -sf http://127.0.0.1:8001/health` (Diagnostic API)
   - `curl -sf http://127.0.0.1:3001` (OBD UI)
   - `curl -sf http://127.0.0.1:8080/health` (Nginx gateway)
7. **Verify deployed commit**: Confirm the running code matches what was pushed:
   `ssh polyu-gpu "cd ~/stf_ai_diagnosis_platform_v1 && git log --oneline -1"`

**Server details**: Podman 3.4 (rootless), host networking, API on port 8001, Nginx on port 8080, `runtime: nvidia` for Ollama GPU.

**CRITICAL Podman 3.4 gotcha**: `podman-compose up -d --build` builds new images but does NOT recreate containers. Always use `down` + `up` to ensure containers run the latest image. Verify via `podman ps` creation timestamps.

## Memory Management

When you discover something valuable for future sessions — architectural decisions, bug fixes, gotchas, environment quirks — immediately append it to .claude/memory.md

Don't wait to be asked. Don't wait for session end.

Keep entries short: date, what, why. Read this file at the start of every session.
