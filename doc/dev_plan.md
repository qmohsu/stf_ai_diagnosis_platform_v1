# Development Plan (v1.1 — OBD Agent integration)

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
