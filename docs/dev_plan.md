# Development Plan (v1.2 — OBD Expert Diagnostic Web UI)

## 1. Scope Boundary
Scope boundary for this plan (so engineers don’t drift)

### 1.1 In Scope (Diagnosis AIH Cloud)
- Cloud diagnosis API + cloud workflow ("Step 5: specific fault type classified
  by cloud model upon request").
- RAG knowledge base for vehicle manuals + maintenance logs used to ground
  recommendations.
- Local LLM inference (Ollama/vLLM) + Dify workflow orchestration.
- Storage of diagnostic records (PostgreSQL recommended; includes user/vehicle/
  diagnosis/maintenance suggestion tables).
- Pilot OBD edge collector ("obd-agent") + OBD telemetry ingestion endpoints + Pass‑1 (OBD→subsystem+PID shortlist) mapping.
- OBD Expert Diagnostic Web UI ("obd-ui") — Next.js frontend for experts to submit OBD logs, view analysis results across 4 tabs (Summary, Detailed, RAG, AI Diagnosis), and provide per-tab structured feedback. AI diagnosis powered by SSE streaming via `POST /v2/obd/{session_id}/diagnose`. Persisted analysis sessions via `/v2/obd/*` endpoints with DB-first persistence.

### 1.2 Out of Scope (Phase 1)
- Full Edge OBU hardware/software (real-time detection), mobile apps, fleet dashboard
  UI, location tracking. (Pilot exception: the minimal OBD edge collector is in-scope.)
- Training large deep learning models (we’ll build the inference integration
  seam, not full training).

## 2. Engineer Order and Dependencies
### 2.1 Engineer Order (Who Goes First vs Second)
- Engineer 1 (DevOps / Environment) goes first. Reason: the AI engineer
  shouldn’t be blocked guessing ports, networks, secrets, and service URLs. You
  need a stable local “cloud stack” to integrate against.
- Engineer 2 (Full‑Stack AI Application) goes second (but can start in parallel
  once DO‑01 is done). Reason: once the base stack is runnable, the AI engineer
  can implement APIs, RAG ingestion/retrieval, and LLM‑guardrailed diagnosis
  logic.

### 2.2 Critical Path Dependency
Critical path dependency: DO‑01 → DO‑06 → (APP‑01 + APP‑02B + APP‑03) → APP‑06 → APP‑08 → APP‑11 (Dify integration) → INT‑01 (E2E demo).

**Summarization Pipeline Path:** APP‑02B → APP‑13 → (APP‑14 + APP‑15) → APP‑16 → APP‑17.

**OBD Expert UI Path:** APP‑17 → APP‑18 (backend persistence + feedback endpoints) → APP‑19 (frontend obd-ui) → APP‑20 (AI diagnosis SSE + RAG/AI feedback tables + DB-first session refactoring).

### 2.3 Definition of Done (Applies to Every Ticket)
A ticket is DONE only if:
- It’s merged with docs and tests updated.
- It respects: raw sensor data stays backend, only derived features/summaries to
  LLM, PII redaction, Docker network isolation.

### 2.4 Deviations and ADRs
If an engineer deviates from the recommended stack (FastAPI/Pydantic/Weaviate/
Ollama‑or‑vLLM/Dify), they must add an ADR (Architecture Decision Record)
explaining the alternative and why it's worth it.

## 3. Ticket-Style Prompts (Copy/Paste into Jira/GitHub Issues)
Below are the “perfect prompts” in task-ticket style: task + what to do +
acceptance criteria. I’m giving them in execution order. Each ticket includes
owner and dependencies.

### 3.1 DevOps / Environment Engineer Tickets

#### DO‑01 — Bootstrap diagnosis cloud stack (Docker Compose)

Owner: DevOps / Environment Engineer
Depends on: none

 

PROMPT (task ticket):
Title: DO‑01 Bootstrap local diagnosis cloud stack (Compose)

 

Context:
We are building the diagnosis cloud system using FastAPI + Pydantic + Weaviate + Ollama/vLLM + Dify.

The stack must support "cloud model classification upon request" (Step 5) and RAG based on manuals and maintenance logs.



Task:
Create a runnable, local docker-compose baseline under infra/ that starts:

diagnostic_api (FastAPI service container; can be a stub for now)

weaviate (vector DB)

ollama OR vLLM (local inference runtime)

dify (+ its required deps, typically Postgres + Redis; if Dify requires its own compose sub-stack, integrate cleanly)

postgres (if not already included by Dify stack)

redis (if not already included by Dify stack)

Requirements:

Pin image versions (no latest).

Use named volumes for persistence (DB, Weaviate, Ollama models).

Add healthchecks for every container.

Provide infra/.env.example containing all required env vars and safe defaults.

Provide Makefile (or justfile) targets: up, down, logs, ps, reset-volumes.

Deliverables:

infra/docker-compose.yml

infra/.env.example

infra/Makefile (or equivalent)

infra/README_LOCAL_SETUP.md with 1-command startup + troubleshooting

Acceptance Criteria:

A fresh machine can run make -C infra up and get all containers to “healthy”.

make -C infra ps shows all services up.

No service uses external LLM APIs (local-only inference runtime is present).

If you strongly disagree with any tool choice:
Create doc/adr/ADR-001-<topic>.md explaining the alternative, tradeoffs, and migration plan.

#### DO‑02 — Network isolation + local-only exposure

Owner: DevOps / Environment Engineer
Depends on: DO‑01

 

PROMPT (task ticket):
Title: DO‑02 Enforce local-only access + network isolation baseline

 

Context:
Privacy/security requirements include: raw sensor data stays backend, only derived features/summaries go to LLM, automated PII redaction, and Docker network isolation enforced.



Task:
Harden the docker networking so the stack is safe by default.

 

Requirements:

Bind exposed ports to 127.0.0.1 only (not 0.0.0.0).

Put internal services on an internal docker network where possible.

Only expose minimal entrypoints to host:

Dify UI (localhost)

diagnostic_api (localhost)

(optional) Weaviate only if needed for local debugging; otherwise keep internal

Provide a SECURITY_BASELINE.md describing:

Which ports are exposed and why

How network isolation is configured

Known gaps and follow-up hardening tasks

Deliverables:

Updated infra/docker-compose.yml with networks and port bindings

doc/SECURITY_BASELINE.md

Acceptance Criteria:

Running docker compose up results in only the explicitly documented ports exposed on localhost.

From another machine on the LAN, services are not reachable.

SECURITY_BASELINE.md clearly documents the boundary assumptions and residual risk.

#### DO‑03 — Local LLM runtime setup + model prefetch

Owner: DevOps / Environment Engineer
Depends on: DO‑01

 

PROMPT (task ticket):
Title: DO‑03 Configure local LLM runtime (Ollama/vLLM) + prefetch

 

Context:
The stack specifies Ollama/vLLM for local inference.



Task:
Make local LLM inference reproducible and not a tribal-knowledge setup.

 

Requirements:

Pick a default “dev model” that can run on a typical laptop (document memory expectations).

Provide a script infra/scripts/pull_models.sh that pulls the chosen model(s).

Ensure models persist in a docker volume so restarts are fast.

Document how to switch models via env var.

Deliverables:

infra/scripts/pull_models.sh

Update to README_LOCAL_SETUP.md with model pull instructions

Env vars: LLM_PROVIDER, LLM_BASE_URL, LLM_MODEL_NAME

Acceptance Criteria:

Running the model pull script succeeds and the model is listed/available.

A simple inference or embeddings request works from within the network (smoke-testable).

Tool alternatives:
If Ollama is unstable for your target workflow, propose vLLM (OpenAI-compatible) in an ADR with rationale.

#### DO‑04 — “One command” smoke test for the stack

Owner: DevOps / Environment Engineer
Depends on: DO‑01, DO‑03

 

PROMPT (task ticket):
Title: DO‑04 Add infra smoke test script (fast fail)

 

Task:
Create a infra/scripts/smoke_test.sh (or python script) that validates the stack.

 

Requirements:

Check: diagnostic_api /health returns 200

Check: diagnostic_api telemetry endpoint accepts a fixture OBDSnapshot (POST /v1/telemetry/obd_snapshot)

Check: Weaviate meta endpoint responds

Check: LLM endpoint responds (or embeddings endpoint)

Check: Dify web UI responds (200 or reachable)

Script returns non-zero on failure and prints actionable error output.

Deliverables:

infra/scripts/smoke_test.sh

make -C infra smoke-test target

Acceptance Criteria:

With stack up: smoke test passes.

With any service down: smoke test fails fast with useful message.

#### DO‑05 — CI pipeline skeleton (lint + unit tests + “no secrets” guard)

Owner: DevOps / Environment Engineer
Depends on: DO‑01

 

PROMPT (task ticket):
Title: DO‑05 Add CI workflow for quality gates

 

Task:
Add GitHub Actions (or equivalent) to run:

Python formatting/linting

Unit tests

Secret scanning (at minimum prevent committing .env)

Deliverables:

.github/workflows/ci.yml (or your CI equivalent)

.gitignore updates if needed

Acceptance Criteria:

CI triggers on PRs and main branch.

CI fails if formatting/tests fail.


#### DO‑06 — OBD Agent runtime + device access baseline

Owner: DevOps / Environment Engineer
Depends on: DO‑01


PROMPT (task ticket):
Title: DO‑06 Define and document OBD Agent runtime + device access


Context:
We are adding an edge collector ("obd-agent") that reads OBD‑II via ELM327 (USB serial or Bluetooth) and posts sanitized OBDSnapshot telemetry to diagnostic_api. This must remain a separate service to avoid hardware access + blocking I/O inside the API workers.


Task:
Define the supported runtime models and provide a reproducible setup:

Option A (recommended for pilot): run obd-agent as a host process (virtualenv) on the same machine that has the adapter.
Option B (optional): run obd-agent as a container with explicit device passthrough (e.g., --device=/dev/ttyUSB0).

Requirements:

Document OS-specific notes: Linux vs Docker Desktop vs WSL2.

Define env vars: OBD_PORT, OBD_BAUDRATE, VEHICLE_ID, DIAGNOSTIC_API_BASE_URL, SNAPSHOT_INTERVAL_SECONDS.

Document Bluetooth pairing (if used) and how to map to a serial device.

Security: obd-agent may only call diagnostic_api; no external egress.

Deliverables:

infra/README_OBD_AGENT_SETUP.md

(If using container option) infra/obd-agent.compose.override.yml

Acceptance Criteria:

A new developer can follow README_OBD_AGENT_SETUP.md and run obd-agent in "simulation mode" (no hardware) and "live mode" (with adapter) without guessing.

#### DO‑07 — Provide OBDSnapshot fixture + post script (for dev and CI)

Owner: DevOps / Environment Engineer
Depends on: DO‑01


PROMPT (task ticket):
Title: DO‑07 Add OBDSnapshot fixture + curl script


Task:
Create a small fixture payload and a helper script so the backend can be tested without hardware.

Requirements:

Add tests/fixtures/obd_snapshot.sample.json

Add scripts/post_obd_snapshot.sh that posts to POST /v1/telemetry/obd_snapshot

Update infra/scripts/smoke_test.sh to use the fixture when present

Acceptance Criteria:

Running the script returns 200 and the response includes a snapshot_id.

### 3.2 Full-Stack AI Application Engineer Tickets
#### APP‑01 — API skeleton with OpenAPI + strict request/response schemas

Owner: Full‑Stack AI Application Engineer
Depends on: DO‑01 (so you know service URLs), but you can start locally in parallel.

 

PROMPT (task ticket):
Title: APP‑01 Create diagnostic_api FastAPI skeleton + contracts

 

Context:
The backend is planned as diagnostic_api/ (FastAPI) and must follow Google Python Style Guide + Pydantic validation.



Task:
Create the FastAPI service skeleton with strict Pydantic models and OpenAPI docs.

 

Required endpoints (minimum):

GET /health → {status, version, dependencies}

POST /v1/vehicle/diagnose → creates a diagnosis session and returns structured output (Phase 1 pilot)

GET /v1/vehicle/diagnose/{session_id} → fetch stored session/result

POST /v1/telemetry/obd_snapshot → ingest OBDSnapshot from obd-agent

GET /v1/telemetry/obd_snapshot/latest → fetch latest OBDSnapshot for a vehicle

POST /v1/rag/retrieve → returns top-k knowledge chunks for a query (internal tool for Dify + diagnosis pipeline)

Requirements:

Use Pydantic models with explicit fields (no dict blobs except where justified).

Include request validation that rejects raw sensor payloads by default (raw audio/video/time-series should not be accepted in this endpoint for Phase 1).

Add structured logging with trace_id per request.

Deliverables:

diagnostic_api/ FastAPI app runnable locally

diagnostic_api/pyproject.toml (or requirements) with pinned deps

Unit tests for /health and schema validation

Acceptance Criteria:

uvicorn starts, docs available at /docs

Requests with unexpected “raw_*” fields are rejected with 400

/health returns 200

#### APP‑02 — Postgres persistence model + migrations (diagnosis records)

Owner: Full‑Stack AI Application Engineer
Depends on: DO‑01 (Postgres running)

 

PROMPT (task ticket):
Title: APP‑02 Implement Postgres schema + migrations for diagnosis records

 

Context:
PostgreSQL is recommended for concurrency and storage, with core tables including user, vehicle, diagnostic record, maintenance suggestion.



Task:
Implement persistence for diagnosis requests/responses.

 

Requirements:

Use SQLAlchemy + Alembic (or equivalent) migrations.

Create minimal tables:

users (minimal fields)

vehicles (minimal fields)

diagnostic_records (request summary + result JSON + timestamps + model versions + trace_id)

maintenance_suggestions (structured suggestions linked to diagnostic record)

Store only derived features/summaries; do not store raw sensor blobs.

Deliverables:

diagnostic_api/db/ models + migrations

alembic.ini and migration scripts

CRUD layer (repository/service)

Acceptance Criteria:

alembic upgrade head works on a clean DB

/v1/vehicle/diagnose persists a record and returns a session_id

/v1/vehicle/diagnose/{session_id} returns the stored record

#### APP‑02B — OBD snapshot persistence + telemetry endpoints

Owner: Full‑Stack AI Application Engineer
Depends on: DO‑01 (Postgres running), APP‑01


PROMPT (task ticket):
Title: APP‑02B Add OBD snapshot ingestion (Postgres + API)


Context:
We ingest OBD snapshots from a separate edge collector (obd-agent). The cloud must store the full sanitized snapshot for audit/replay, but only derived summaries are allowed into the LLM context.


Task:
Implement:

1) Postgres table `obd_snapshots` (id, vehicle_id, ts, adapter_type, payload_jsonb, created_at) + indexes on (vehicle_id, ts desc)

2) Endpoint: POST /v1/telemetry/obd_snapshot
   - Validates strict OBDSnapshot schema (Pydantic)
   - Rejects high-risk fields (raw logs/CAN frames/oversized arrays)
   - Persists snapshot and returns snapshot_id

3) Endpoint: GET /v1/telemetry/obd_snapshot/latest?vehicle_id=...&max_age_seconds=...

Deliverables:

DB migration + models

Pydantic models for OBDSnapshot

CRUD/repository methods

Unit tests for payload validation + latest lookup

Acceptance Criteria:

Posting the fixture OBDSnapshot returns 200 with snapshot_id

Latest lookup returns the most recent snapshot for a vehicle

No raw debug logs or time-series arrays are accepted

#### APP‑02C — Pass‑1 mapper (OBD → subsystem shortlist + PID shortlist)

Owner: Full‑Stack AI Application Engineer
Depends on: APP‑02B


PROMPT (task ticket):
Title: APP‑02C Implement Pass‑1 mapping (rules + tables)


Context:
Pass‑1 is the deterministic pipeline that maps DTC(s) + freeze frame + supported PID list (+ symptom tags) to a ranked subsystem shortlist and a 10–25 PID shortlist, filtered by vehicle-supported PIDs.


Task:
Create a module (e.g., diagnostic_api/pass1/) with:

- Rules: DTC family → subsystem (P03xx=misfire/ignition, P01xx/P02xx=fuel/air, P04xx=EVAP, etc.)

- Tables (CSV/YAML): subsystem → PID priority list

- Symptom tags → subsystem rules for "no DTC" cases

Expose a pure function: map_pass1(dtc_codes, freeze_frame, supported_pids, symptom_tags) -> {subsystem_shortlist, candidate_pids, highlights, limitations}

Deliverables:

pass1/ tables + code

Unit tests for at least 5 DTC families and 3 symptom-only cases

Acceptance Criteria:

Given a P03xx code and supported_pids, the mapper returns ignition/misfire as top subsystem and a PID shortlist that is a subset of supported_pids.


#### APP‑03 — Weaviate schema + ingestion CLI for manuals/logs

Owner: Full‑Stack AI Application Engineer
Depends on: DO‑01 (Weaviate running), DO‑03 (embeddings available)

 

PROMPT (task ticket):
Title: APP‑03 Build RAG ingestion pipeline (manuals + maintenance logs) into Weaviate

 

Context:
Cloud model inputs include maintenance logs and vehicle manuals, and we use Weaviate for RAG.



Task:
Create a CLI ingestion tool under rag/ that:

loads documents (start with TXT/MD; optionally PDF later)

chunks them deterministically (chunk size + overlap)

generates embeddings locally (via Ollama embeddings endpoint or local embedding model)

writes objects + vectors into Weaviate with metadata

Metadata fields (minimum):

source_type (manual|maintenance_log)

doc_id

vehicle_make_model (optional)

section_title

chunk_index

text

checksum (for dedupe)

created_at

Deliverables:

rag/ingest.py (CLI)

rag/chunker.py

rag/embedding.py

rag/README.md with sample commands and sample data folder structure

Acceptance Criteria:

Running ingestion against sample docs produces objects in Weaviate

Re-running ingestion is idempotent (doesn’t duplicate chunks)

A basic query can retrieve relevant chunks

If you think Weaviate is the wrong choice:
Write an ADR proposing Qdrant/pgvector/etc with justification (latency, ops simplicity, offline constraints).

#### APP‑04 — Retrieval service + API endpoint (top‑k with citations)

Owner: Full‑Stack AI Application Engineer
Depends on: APP‑03

 

PROMPT (task ticket):
Title: APP‑04 Implement /v1/rag/retrieve (ranked chunks + metadata)

 

Task:
Implement retrieval endpoint and core retrieval module.

 

Requirements:

Input: {query, top_k, filters(optional)}

Output: list of {text, score, doc_id, section_title, chunk_index, source_type}

Include a clear “citation payload” so the diagnosis response can reference manual sections.

Add caching (in-memory is fine for now) for repeated queries.

Deliverables:

Retrieval module under diagnostic_api/rag/ or rag/

Endpoint implementation + tests

Acceptance Criteria:

Endpoint returns top_k results with metadata and stable ordering

Basic unit test verifies expected behavior on mocked Weaviate responses

#### APP‑05 — Expert prompts + JSON schema validators (guardrails)

Owner: Full‑Stack AI Application Engineer
Depends on: APP‑01

 

PROMPT (task ticket):
Title: APP‑05 Add expert prompts + strict output schemas for diagnosis

 

Context:
We need "specific fault types under a subsystem" and actionable diagnostic summaries.



Task:
Create prompt templates and validators under expert_model/:

expert_model/prompts/diagnosis_system_prompt.md

expert_model/prompts/diagnosis_user_prompt.md (template)

expert_model/schemas/diagnosis_output.schema.json

expert_model/validate.py (validates model output)

Requirements:

Output must be strict JSON matching schema.

Include fields:

subsystem_category (one of the predefined systems)

specific_fault_type

confidence (0–1)

severity (enum)

recommended_actions (list)

evidence_summary (list)

rag_references (list of doc_id/section/chunk)

Include “refuse / escalate” behavior when confidence is low or info insufficient.

Deliverables:

Prompts + schema + validator

Unit tests for validator

Acceptance Criteria:

Invalid output fails validation with clear reason

Valid output passes and is ready to be stored/returned

#### APP‑06 — Data boundary enforcement + PII redaction

Owner: Full‑Stack AI Application Engineer
Depends on: APP‑01

 

PROMPT (task ticket):
Title: APP‑06 Enforce privacy boundary (derived features only) + implement PII redaction

 

Context:
Privacy requirements: raw sensor data stays backend; only derived features/summaries go to LLM; PII redaction is implemented.



Task:
Implement:

A “derived feature” builder that converts any incoming diagnostic payload into an allowlisted summary format.

A PII redaction step for free-text (symptom descriptions, notes).

Requirements:

Define an allowlist for what may reach the LLM (e.g., DTC codes, aggregate stats, extracted features).

Reject or strip:

raw audio/video

raw time-series arrays beyond safe summary

any obvious personal identifiers in text (phone/email/ID)

Log redaction events (count/type), but do not log raw PII.

Deliverables:

diagnostic_api/privacy/redaction.py

diagnostic_api/privacy/feature_boundary.py

Tests proving raw fields are rejected/stripped

Acceptance Criteria:

LLM call path receives only derived/allowlisted data (prove via unit test)

Redaction masks PII patterns in text inputs

#### APP‑07 — LLM client integration (Ollama/vLLM) with retries + JSON repair

Owner: Full‑Stack AI Application Engineer
Depends on: DO‑03, APP‑05

 

PROMPT (task ticket):
Title: APP‑07 Implement robust LLM client wrapper (local inference only)

 

Task:
Implement an LLM client module that:

Calls local inference runtime (Ollama or vLLM)

Has timeouts, retries, and backoff

Produces structured JSON output validated by schema

Implements “JSON repair” strategy:

If invalid JSON, re-prompt with “fix to valid JSON” instruction

If still invalid, return “needs human review” response

Deliverables:

diagnostic_api/llm/client.py

diagnostic_api/llm/prompts.py (or uses expert_model prompts)

Tests with mocked LLM responses (valid/invalid)

Acceptance Criteria:

Invalid LLM output does not crash the service

Responses are always either valid schema output or explicit “review required” envelope

#### APP‑08 — Diagnosis pipeline (“Step 5 on request”) end-to-end

Owner: Full‑Stack AI Application Engineer
Depends on: APP‑02, APP‑04, APP‑05, APP‑07

 

PROMPT (task ticket):
Title: APP‑08 Implement diagnosis request pipeline (RAG + LLM + persistence)

 

Context:
Cloud diagnosis should produce "specific fault type under a subsystem" upon request (Step 5).

Inputs include manuals/logs for grounding.



Task:
Implement the /v1/vehicle/diagnose endpoint fully (including optional Pass‑1 from latest OBDSnapshot):

Accept validated request

Build derived feature summary + redact text

Query RAG retrieval for supporting manual/log chunks

Call LLM with prompt + RAG context

Validate output schema

Store diagnostic record in Postgres

Return response with session_id and structured output

Deliverables:

Working endpoint implementation

E2E test that runs against local stack (can mock LLM if needed)

Acceptance Criteria:

Given a sample request, endpoint returns:

session_id

subsystem_category

specific_fault_type

recommended_actions

rag_references

Record is persisted and retrievable by ID

#### APP‑09 — Technician feedback endpoint (continuous improvement hook)

Owner: Full‑Stack AI Application Engineer
Depends on: APP‑02

 

PROMPT (task ticket):
Title: APP‑09 Add feedback API for expert correction

 

Context:
Operation flow includes technician feedback for improving model accuracy.



Task:
Implement /v1/vehicle/diagnose/{session_id}/feedback endpoint:

Accept corrections: actual fault type, repair outcome, notes, confidence override

Store feedback linked to diagnostic record

Expose GET /v1/vehicle/diagnose/{session_id} to include feedback summary (if present)

Deliverables:

DB migration for feedback table

Endpoint + tests

Acceptance Criteria:

Feedback can be submitted and is persisted

Diagnostic record fetch shows feedback status

#### APP‑10 — Daily report generation (minimal cloud report, not UI)

Owner: Full‑Stack AI Application Engineer
Depends on: APP‑02, APP‑08

 

PROMPT (task ticket):
Title: APP‑10 Implement daily diagnostic report generation (API + background job)

 

Context:
Operation flow includes generating daily report for each vehicle.



Task:
Implement a minimal “daily report” backend:

Endpoint: POST /v1/reports/daily (for a vehicle_id + date) triggers report generation

Endpoint: GET /v1/reports/{report_id} returns status + result

Report content can be JSON in Phase 1; PDF is optional.

Requirements:

Run generation asynchronously (FastAPI background tasks or Celery/Redis).

Report aggregates diagnostic records for that day and produces summary + recommended actions.

Deliverables:

Report table + endpoints

Minimal async worker mechanism

Acceptance Criteria:

Report requests return quickly with report_id

Report can be retrieved later with completed status and content

#### APP‑11 — Dify workflow integration (agentic “front door”)

Owner: Full‑Stack AI Application Engineer
Depends on: DO‑01, APP‑08

 

PROMPT (task ticket):
Title: APP‑11 Integrate Dify workflow calling diagnostic_api

 

Context:
We use Dify for agentic workflows.



Task:
Create a documented Dify workflow that:

Takes a technician’s natural-language request (symptoms + context)

Calls diagnostic_api endpoints (either directly or via a tool definition)

Returns a structured diagnostic response and human-readable summary

Requirements:

Document exact Dify configuration steps and required env vars.

If Dify cannot call the local LLM/provider you chose, propose an alternative (e.g., vLLM OpenAI-compatible endpoint) with ADR.

Deliverables:

doc/DIFY_SETUP.md

Exported workflow config if possible (or screenshots + step-by-step)

Sample demo inputs/outputs

Acceptance Criteria:

With stack running, a user can execute the workflow and get a diagnosis result end-to-end.

#### APP‑12 — Evaluation harness (schema validity + regression tests)

Owner: Full‑Stack AI Application Engineer
Depends on: APP‑08

 

PROMPT (task ticket):
Title: APP‑12 Add evaluation harness for diagnosis (offline regression)

 

Task:
Create eval/ harness that can run a set of golden test cases:

Each case includes: derived feature summary + expected subsystem category

Run pipeline and compute:

schema validity rate

“needs review” rate

basic accuracy vs expected category (where labels exist)

Deliverables:

eval/run_eval.py

eval/cases/*.json

Summary output (JSON + human-readable report)

Acceptance Criteria:

python eval/run_eval.py runs locally and produces a report

CI can run evaluation in “mock LLM mode” to avoid flakiness

#### APP‑13 — Upgrade Log Parsing & Time-Series Normalization (Stage 0)

Owner: Full‑Stack AI Application Engineer
Depends on: APP‑02B

PROMPT (task ticket):
Title: APP‑13 Upgrade log parsing to produce unified time-series dataframe

Context:
The current `log_parser.py` produces OBDSnapshots. For the production summarization pipeline (design_doc.md Section 8.3), we need a clean, unified time-series representation suitable for advanced statistical and anomaly detection.

Task:
Refactor or extend the log parsing module to:

- Parse timestamps and signal identifiers
- Map PIDs to semantic signal names
- Perform unit normalization
- Resample to a unified time grid (configurable interval)
- Handle missing values (interpolation / masking)

Requirements:

Use pandas for time-series operations
Output a multivariate DataFrame: time × signals
Maintain backward compatibility with existing OBDSnapshot flow
Add configuration for resampling interval (default: 1 second)

Deliverables:

obd_agent/time_series_normalizer.py
Unit tests for resampling and missing value handling
Documentation of PID-to-signal-name mapping

Acceptance Criteria:

Given a raw TSV log, the module produces a clean DataFrame with consistent timestamps
Missing values are handled per configuration (interpolate, forward-fill, or mask)
Existing OBDSnapshot generation is not broken

#### APP‑14 — Enhanced Value Statistics Extraction (Stage 1)

Owner: Full‑Stack AI Application Engineer
Depends on: APP‑13

PROMPT (task ticket):
Title: APP‑14 Implement advanced statistics extraction with tsfresh

Context:
The current summarizer extracts only min/max/mean/latest. The production pipeline (design_doc.md Section 8.3.3) requires expanded statistics for LLM context.

Task:
Create a statistics extraction module that computes for each signal:

- Mean, standard deviation, min, max
- Percentiles (P5, P25, P50, P75, P95)
- Autocorrelation (lag 1)
- Energy, entropy
- Change rate statistics (mean absolute change, max change)

Requirements:

Integrate tsfresh (or equivalent) for feature extraction
Make feature set configurable (minimal vs full)
Output schema compatible with existing LogSummary

Deliverables:

obd_agent/statistics_extractor.py
Requirements update: add tsfresh dependency
Unit tests with sample time-series data
Performance benchmark for large logs (>10,000 rows)

Acceptance Criteria:

Given a time-series DataFrame, the module returns a statistics dict matching the schema
Minimal mode returns only existing fields (backward compatible)
Full mode includes percentiles, autocorrelation, entropy

#### APP‑15 — Anomaly Detection with Temporal Context (Stage 2)

Owner: Full‑Stack AI Application Engineer
Depends on: APP‑13

PROMPT (task ticket):
Title: APP‑15 Implement anomaly detection with temporal context mining

Context:
The current anomaly detection uses simple heuristics (range shift, out-of-range). The production pipeline (design_doc.md Section 8.3.4) requires context-aware anomaly detection with temporal information.

Task:
Create an anomaly detection module using:

- Change-point and regime detection (ruptures)
- Multivariate anomaly detection (Isolation Forest, LOF via scikit-learn or PyOD)
- Optional: temporal pattern discovery (STUMPY matrix profile)

Requirements:

Each detected anomaly must include:
  - time_window (start ~ end)
  - signals involved
  - pattern description
  - driving context (idle, cruise, acceleration)
  - severity (low, medium, high)
Output is a list of anomaly event objects

Deliverables:

obd_agent/anomaly_detector.py
Requirements update: add ruptures, pyod dependencies
Unit tests with synthetic anomaly scenarios
Integration test with real log samples

Acceptance Criteria:

Given a time-series DataFrame, the module returns a list of anomaly events
Each event has time_window, signals, pattern, context, and severity fields
Change-point detection identifies regime shifts correctly

#### APP‑16 — Diagnostic Semantic Clue Generation (Stage 3)

Owner: Full‑Stack AI Application Engineer
Depends on: APP‑14, APP‑15

PROMPT (task ticket):
Title: APP‑16 Implement rule-based diagnostic clue generation

Context:
The production pipeline (design_doc.md Section 8.3.5) requires conversion of statistical and anomaly findings into LLM-ready semantic facts. This must be rule-based (not LLM-generated) to ensure traceability.

Task:
Create a diagnostic clue generator that applies:

- Domain heuristics (e.g., throttle variance + RPM pattern rules)
- Signal interaction rules (e.g., fuel trim vs airflow)
- Cause–effect temporal ordering (e.g., A precedes B patterns)
- DTC-aware clue generation

Requirements:

Rules must be configurable (YAML or Python dict)
Each clue must be traceable to source evidence
No LLM calls in this module
Output: list of diagnostic clue strings

Deliverables:

obd_agent/clue_generator.py
obd_agent/rules/diagnostic_rules.yaml
Unit tests for at least 10 diagnostic scenarios
Documentation of rule format

Acceptance Criteria:

Given statistics + anomaly events + DTCs, the module returns diagnostic clues
Each clue references the evidence that triggered it (for traceability)
Rules can be extended without code changes

#### APP‑17 — Unified v2 Summarization API Endpoint

Owner: Full‑Stack AI Application Engineer
Depends on: APP‑13, APP‑14, APP‑15, APP‑16

PROMPT (task ticket):
Title: APP‑17 Implement POST /v2/tools/summarize-log-raw endpoint

Context:
The v1 endpoint returns basic summaries. The v2 endpoint integrates all pipeline stages for production use (design_doc.md Section 8.3.6).

Task:
Implement a new API endpoint:

POST /v2/tools/summarize-log-raw

Response includes:
- vehicle_id, time_range, dtc_codes (existing)
- value_statistics (Stage 1 output)
- anomaly_events (Stage 2 output)
- diagnostic_clues (Stage 3 output)
- pid_summary (backward compatible)

Requirements:

Maintain v1 endpoint for backward compatibility
Add query parameter for mode: ?mode=minimal|full
Full mode runs all pipeline stages
Minimal mode returns only basic stats (v1 equivalent)

Deliverables:

diagnostic_api/app/api/v2/endpoints/log_summary.py
Pydantic models for v2 response schema
Integration tests for both modes
API documentation update

Acceptance Criteria:

POST /v2/tools/summarize-log-raw with raw log returns full structured summary
mode=minimal returns v1-equivalent output
mode=full returns all pipeline stages
v1 endpoint continues to work unchanged

#### APP‑18 — OBD Analysis Session Persistence + Feedback Endpoints

Owner: Full‑Stack AI Application Engineer
Depends on: APP‑17
Status: **DONE** (2026-02-14)

PROMPT (task ticket):
Title: APP‑18 Add persisted OBD analysis sessions + expert feedback API

Context:
Experts need a way to submit OBD logs via the web UI and have results persisted for later review. They also need to provide per-tab structured feedback (rating, helpfulness, comments) on each analysis, across 4 result categories: summary, detailed, RAG, and AI diagnosis.

Task:
Implement endpoints under `/v2/obd/`:

1) `POST /v2/obd/analyze`
   - Accepts raw OBD TSV text body
   - Creates an `OBDAnalysisSession` record (UUID PK, status=PENDING) persisted to Postgres immediately (DB-first, no in-memory cache)
   - Deduplicates via SHA-256 hash of input text; returns existing session on match
   - Runs `_run_pipeline()` (same 5-stage pipeline as `/v2/tools/summarize-log-raw`)
   - Stores `raw_input_text`, `parsed_summary_payload`, and `result_payload` (JSONB)
   - Persists result as JSONB (status=COMPLETED) or error (status=FAILED)
   - Returns session_id + full LogSummaryV2

2) `GET /v2/obd/{session_id}`
   - Retrieves persisted session by UUID
   - Reconstructs LogSummaryV2 from stored JSONB

3) `POST /v2/obd/{session_id}/diagnose`
   - SSE streaming AI diagnosis endpoint
   - Streams diagnosis text via Server-Sent Events
   - Stores `diagnosis_text` on the session record upon completion

4) `POST /v2/obd/{session_id}/feedback/summary`
   `POST /v2/obd/{session_id}/feedback/detailed`
   `POST /v2/obd/{session_id}/feedback/rag`
   `POST /v2/obd/{session_id}/feedback/ai_diagnosis`
   - Accepts: rating (1-5), is_helpful (bool), comments (optional)
   - Multiple feedback per session allowed (capped at 10 per tab); returns 429 when limit exceeded
   - RAG feedback snapshots retrieved RAG text; AI diagnosis feedback snapshots diagnosis text via `extra_fields`

Database changes:
- Table `obd_analysis_sessions`: id (UUID PK), vehicle_id (indexed), status (indexed), input_text_hash (SHA-256, indexed, unique for dedup), input_size_bytes, raw_input_text, parsed_summary_payload (JSONB), result_payload (JSONB), diagnosis_text, error_message, created_at, updated_at
- 4 feedback tables (one per tab):
  - `obd_summary_feedback`: id, session_id (FK), rating, is_helpful, comments, created_at
  - `obd_detailed_feedback`: id, session_id (FK), rating, is_helpful, comments, created_at
  - `obd_rag_feedback`: id, session_id (FK), rating, is_helpful, comments, rag_retrieved_text, created_at
  - `obd_ai_diagnosis_feedback`: id, session_id (FK), rating, is_helpful, comments, diagnosis_text, created_at
- Alembic migrations applied

Deliverables:

`diagnostic_api/app/api/v2/endpoints/obd_analysis.py`
`diagnostic_api/app/models_db.py` (5 new ORM classes: 1 session + 4 feedback)
`diagnostic_api/app/api/v2/schemas.py` (Pydantic models for session, diagnosis, and 4 feedback types)
Alembic migrations
Updated `main.py` (CORS + router registration)

Acceptance Criteria:

POST /v2/obd/analyze with fixture TSV returns session_id + full analysis JSON ✓
Duplicate input text returns existing session (dedup via SHA-256) ✓
GET /v2/obd/{session_id} returns persisted session with raw_input_text and parsed_summary ✓
POST /v2/obd/{session_id}/diagnose streams AI diagnosis via SSE ✓
POST feedback (4 endpoints) returns 201; 11th submission returns 429 ✓
CORS allows requests from localhost:3001 ✓

#### APP‑19 — OBD Expert Diagnostic Web UI (obd-ui)

Owner: Full‑Stack AI Application Engineer
Depends on: APP‑18
Status: **DONE** (2026-02-14)

PROMPT (task ticket):
Title: APP‑19 Build Next.js expert diagnostic frontend (obd-ui)

Context:
Experts currently have no visual way to submit OBD logs, view analysis results with charts, or provide feedback. This ticket adds a Next.js frontend on port 3001 that consumes the `/v2/obd/*` endpoints from APP‑18.

Task:
Scaffold and implement a Next.js 15 application (`obd-ui/`) with:

**Input Page (`/`):**
- Large monospace textarea for pasting TSV data
- File drag-and-drop (.txt/.tsv/.log, max 10MB)
- "Analyze" button → calls POST /v2/obd/analyze → redirects to results page

**Analysis Page (`/analysis/[sessionId]`):**
- Fetches session via GET /v2/obd/{session_id}
- Header: vehicle_id, time range, duration, sample count, DTC badges
- Tab 1 (Summary): PID summary table (8 signals), diagnostic clues list, DTC codes
- Tab 2 (Detailed):
  - Signal bar chart (grouped min/mean/max, filterable by unit)
  - Signal box plot (P5/P25/P50/P75/P95 visualization)
  - Anomaly timeline (scatter: time × score, color by severity, clickable)
  - Anomaly event cards (filterable by severity, sortable by score/time)
  - Clue detail cards (grouped by category with evidence lists)
- Tab 3 (RAG): RAG retrieval display with retrieved text and feedback form
- Tab 4 (AI Diagnosis): SSE streaming AI diagnostic result via POST /v2/obd/{session_id}/diagnose, with real-time text rendering and feedback form
- Per-tab feedback forms: star rating (1-5), helpful toggle, comments (no corrected_diagnosis field)
- Dedup handling: re-submitting identical input returns existing session
- forceMount on tab panels to preserve state across tab switches

**Tech stack:**
- Next.js 15, TypeScript, Tailwind CSS, App Router
- shadcn/ui components (button, card, badge, tabs, table, textarea, alert, select)
- recharts (BarChart, ComposedChart, ScatterChart)
- lucide-react icons

**Docker:**
- Multi-stage Dockerfile (node:20-alpine builder → standalone output)
- Service in docker-compose.yml on port 3001

Deliverables:

`obd-ui/` directory (~30 component files)
`obd-ui/Dockerfile` + `.dockerignore`
Updated `infra/docker-compose.yml` (new obd-ui service)

Acceptance Criteria:

Frontend serves on port 3001 ✓
Paste sample TSV → Analyze → results page renders ✓
Summary tab: PID table + clue bullets + per-tab feedback ✓
Detailed tab: bar chart, box plot, anomaly timeline, event cards, clue cards + per-tab feedback ✓
RAG tab: retrieval display + feedback ✓
AI Diagnosis tab: SSE streaming diagnosis + feedback ✓
Duplicate input detected and existing session returned ✓
Feedback submission works (4 separate endpoints) ✓
`npm run build` passes with 0 errors ✓
Docker build succeeds ✓

#### APP‑20 — AI Diagnosis + RAG Feedback + DB-First Session Refactoring

Owner: Full‑Stack AI Application Engineer
Depends on: APP‑19
Status: **DONE** (2026-02-15)

PROMPT (task ticket):
Title: APP‑20 AI diagnosis SSE endpoint, feedback table split, and DB-first session refactoring

Context:
After APP‑19 shipped the initial 2-tab UI with a single feedback table, iterative work expanded the platform to 4 analysis tabs with dedicated feedback tables, an SSE-streaming AI diagnosis endpoint, and a DB-first session architecture replacing the in-memory cache layer.

Task:
This ticket covers the post-APP‑19 refinements delivered across multiple commits:

1) **AI Diagnosis SSE endpoint** — `POST /v2/obd/{session_id}/diagnose` streams LLM diagnosis text via Server-Sent Events; stores `diagnosis_text` on the session record upon completion.

2) **Feedback table split** — Replaced the single `obd_analysis_feedback` table with 4 per-tab tables: `obd_summary_feedback`, `obd_detailed_feedback`, `obd_rag_feedback`, `obd_ai_diagnosis_feedback`. Each supports multiple submissions per session (capped at 10); excess returns 429.

3) **RAG + AI diagnosis feedback text snapshots** — RAG feedback rows snapshot the retrieved RAG text; AI diagnosis feedback rows snapshot the diagnosis text at submission time via `extra_fields`.

4) **Dropped `corrected_diagnosis`** — Removed from all feedback tables and schemas (redundant with comments field).

5) **DB-first session persistence** — Removed in-memory cache layer; sessions are now written to Postgres immediately on `POST /v2/obd/analyze`. Deleted `_ensure_session_in_db` helper.

6) **Dedup via SHA-256** — `input_text_hash` column (unique index) enables dedup; re-submitting identical input returns the existing session instead of creating a new one.

7) **New session columns** — `raw_input_text`, `parsed_summary_payload` (JSONB), `diagnosis_text` added to `obd_analysis_sessions`.

8) **Frontend updates** — obd-ui expanded to 4 tabs (Summary, Detailed, RAG, AI Diagnosis) with per-tab feedback forms, `forceMount` for tab state preservation, and regenerate-diagnosis support.

Deliverables:

Updated `diagnostic_api/app/api/v2/endpoints/obd_analysis.py` (SSE diagnose endpoint, 4 feedback endpoints, DB-first persistence)
Updated `diagnostic_api/app/models_db.py` (4 feedback ORM classes, expanded session columns)
Updated `diagnostic_api/app/api/v2/schemas.py` (per-tab feedback schemas, diagnosis schemas)
Alembic migrations for table split, new columns, and dropped fields
Updated obd-ui components (RAG tab, AI Diagnosis tab, per-tab feedback forms, dedup handling)

Acceptance Criteria:

POST /v2/obd/{session_id}/diagnose streams diagnosis via SSE ✓
4 feedback endpoints accept submissions independently ✓
11th feedback on same tab returns 429 ✓
RAG feedback includes rag_retrieved_text snapshot ✓
AI diagnosis feedback includes diagnosis_text snapshot ✓
No corrected_diagnosis field in any schema or table ✓
Sessions persisted to DB immediately on analyze (no in-memory cache) ✓
Duplicate input returns existing session via SHA-256 dedup ✓
obd-ui renders 4 tabs with per-tab feedback ✓

### 3.3 Integration and Finalization Tickets
#### INT‑01 — End-to-end demo script (“one command demo”)

Owner: DevOps (primary) + AI Engineer (review)
Depends on: DO‑04, APP‑08, APP‑11

 

PROMPT (task ticket):
Title: INT‑01 Ship an end-to-end demo (“bring up stack → ingest docs → run diagnosis”)

 

Task:
Create a single demo script that:

Starts stack

Ingests sample manuals/logs into Weaviate

Runs a sample diagnosis request (optionally after posting an OBDSnapshot fixture)

Shows the output (JSON + short explanation)

Deliverables:

scripts/demo_e2e.sh (or python scripts/demo_e2e.py)

Sample docs under rag/sample_data/ and sample request under tests/fixtures/

Acceptance Criteria:

A new developer can run the demo and see a diagnosis result without manual setup steps.

#### SEC‑01 — Threat model + security checks for diagnosis cloud

Owner: DevOps (primary) + AI Engineer (contributor)
Depends on: DO‑02, APP‑06

 

PROMPT (task ticket):
Title: SEC‑01 Threat model + automated security checks (LLM boundary)

 

Context:
Privacy & security requires raw sensor data remains backend, only derived features to LLM, PII redaction, docker isolation.



Task:
Produce:

doc/THREAT_MODEL.md focusing on:

prompt injection via maintenance logs/manual text

data exfiltration through LLM output

SSRF and lateral movement inside docker network

Add automated tests that ensure:

requests containing raw sensor blobs are rejected

redaction runs on text before LLM call

diagnostic_api cannot reach external network endpoints (if feasible)

Deliverables:

doc/THREAT_MODEL.md

Security unit/integration tests

Acceptance Criteria:

Threat model identifies top risks and mitigations

Tests fail if boundary enforcement is removed/broken

#### ADR‑001 — Tooling deviation process (only if needed)

Owner: Whoever wants to deviate
Depends on: none

 

PROMPT (task ticket):
Title: ADR‑001 Propose alternative tool to stack (only if you strongly disagree)

 

Task:
If you believe the recommended tool (FastAPI/Pydantic/Weaviate/Ollama‑or‑vLLM/Dify) should be replaced, write an ADR including:

Problem statement

Proposed alternative(s)

Pros/cons

Migration plan and rollback plan

Impact on offline/local-only requirement

Acceptance Criteria:

ADR is concrete and actionable (not opinion-only)

Includes measurable reasons (latency, maintainability, compatibility, ops cost)

What this plan deliberately avoids (because it’s where projects die)

Letting the LLM be the only "truth engine" for fault classification without guardrails. The plan forces strict schemas, validation, and a "needs review" path.

"We'll secure it later." You already have explicit privacy and isolation requirements; ignoring them early guarantees rework.

Notes

The design doc (design_doc.md) is now present and should be treated as the authoritative architecture reference for Phase 1 → 2.

I based this plan on the repo overview + your application/presentation materials describing the cloud diagnosis flow, inputs (manuals/logs), and the "Step 5" on-demand classification requirement.

If you want, I can also convert these into a ready-to-import backlog format (CSV for Jira / GitHub Issues) — but the content above is already written as copy/paste ticket prompts.
