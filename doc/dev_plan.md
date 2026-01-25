# Development Plan

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

### 1.2 Out of Scope (Phase 1)
- Edge OBU hardware/software (real-time detection), mobile apps, fleet dashboard
  UI, location tracking.
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
Critical path dependency: DO‑01 → (APP‑01 + APP‑03) → APP‑06 → APP‑08 (Dify
integration) → INT‑01 (E2E demo).

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

POST /v1/diagnosis/request → creates a diagnosis request (on-demand "Step 5")

GET /v1/diagnosis/{diagnosis_id} → fetch stored result

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

/v1/diagnosis/request persists a record and returns an ID

/v1/diagnosis/{id} returns the stored record

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
Implement the /v1/diagnosis/request endpoint fully:

Accept validated request

Build derived feature summary + redact text

Query RAG retrieval for supporting manual/log chunks

Call LLM with prompt + RAG context

Validate output schema

Store diagnostic record in Postgres

Return response with diagnosis_id and structured output

Deliverables:

Working endpoint implementation

E2E test that runs against local stack (can mock LLM if needed)

Acceptance Criteria:

Given a sample request, endpoint returns:

diagnosis_id

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
Implement /v1/diagnosis/{id}/feedback endpoint:

Accept corrections: actual fault type, repair outcome, notes, confidence override

Store feedback linked to diagnostic record

Expose GET /v1/diagnosis/{id} to include feedback summary (if present)

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

Runs a sample diagnosis request

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

Notes on missing design doc

Your repo overview points to doc/design_doc.md, but it wasn't present in what I could review.

I based this plan on the repo overview + your application/presentation materials describing the cloud diagnosis flow, inputs (manuals/logs), and the "Step 5" on-demand classification requirement.

If you want, I can also convert these into a ready-to-import backlog format (CSV for Jira / GitHub Issues) — but the content above is already written as copy/paste ticket prompts.

## 4. Sources and Review Notes
### 4.1 Review Development Plan
#### 4.1.1 UM4MDC Consistency Review (Conflicts + What to Fix)
I reviewed these four rule files in your folder:

base.mdc (alwaysApply: true)

python-style-guide.mdc (alwaysApply: true)

dev-ops-env-setup-engineer.mdc (scoped by globs)

full-stack-ai-application-engineer.mdc (scoped by globs)

##### A. Real Conflicts (You Should Resolve Before Engineers Start)
Conflict 1 — Python line-length & formatter expectations

base.mdc requires line length <= 80 chars.

full-stack-ai-application-engineer.mdc also requires 80 chars.

python-style-guide.mdc suggests 88 (Black default) or 100, and explicitly says “Format code according to Black formatter (88 char line length)”.

Why it matters: you’ll get churn (endless reformat PRs), and engineers won’t know what CI/pre-commit expects.

 

Resolution (recommended):

Make 80 characters the single source of truth (because both base.mdc and the full-stack role doc agree on 80).

Keep Black if you want, but configure it to 80:

black --line-length 80

Update python-style-guide.mdc to match 80 and remove the “88 or 100” ambiguity.

Conflict 2 — Nginx ingress: optional vs mandatory

dev-ops-env-setup-engineer.mdc: Nginx is “optional but recommended”; allows exposing Dify web directly on localhost for pilot.

full-stack-ai-application-engineer.mdc: states “Only the nginx reverse proxy should handle ingress”.

Why it matters: it changes the infra definition-of-done and how you do port exposure, CORS, SSRF boundaries, etc.

 

Resolution (recommended):

Treat DevOps doc as authoritative for infra decisions and keep:

Nginx recommended, not mandatory for first bring-up.

But enforce the same security outcome either way: only localhost binding + minimal exposed ports.

Update the full-stack doc to:
“Prefer Nginx single-ingress; acceptable to expose Dify web directly bound to 127.0.0.1 for the laptop pilot.”

Conflict 3 — Two “always apply” documents that disagree

Both base.mdc and python-style-guide.mdc are alwaysApply: true, and they currently disagree (line length).

 

Resolution (recommended):

Keep base.mdc as the ultimate precedence doc.

Make python-style-guide.mdc explicitly subordinate by adding one rule at the top like:

“If any conflict exists, base.mdc wins.”

Or set python-style-guide.mdc to alwaysApply: false and let the full-stack role doc enforce Python style within code globs.

##### B. Non-Conflicts (Looks Good)
These parts are consistent across docs and are “good to go”:

Local-first / no internet at runtime: consistent.

No raw sensor data to LLM: consistent and clearly stated.

Strict JSON output + citations + logging: consistent.

Pinned versions / no secrets committed: consistent.

Testing expectations (schema, redaction, error handling): consistent.

##### Bottom Line on UM4MDC Readiness
You’re almost good to go, but I would fix the two hard conflicts (line length + Nginx optional/mandatory) now. After that, these rule docs are strong enough to drive a clean development plan.

 

##### Links for Your Team to Open Directly
base.mdc

python-style-guide.mdc

dev-ops-env-setup-engineer.mdc

full-stack-ai-application-engineer.mdc

#### 4.1.2 Phase 1 Scope (What This Plan Will Build)
Per your instruction, this backlog focuses only on the Diagnosis AIH cloud computing system portion:

 

Phase 1 pilot outcome (Definition of Done):

Laptop-local stack runs via Docker Compose: Dify + Weaviate + Postgres + Redis + diagnostic_api + local LLM endpoint (Ollama or vLLM)

A Dify workflow can submit a “diagnosis request” (using summaries only, not raw signals)

The system returns strict JSON with citations (doc_id#section) or NO_SOURCE

Persistent interaction logging is produced for Phase 1.5 training readiness

Smoke tests validate health + schema + retrieval + logging

Out of scope for Phase 1 (explicitly):

Edge model deployment (OBU), real-time edge detection

Mobile apps / web dashboard product UI (beyond Dify UI)

Training deep temporal models (Transformer/LSTM) on real sensor streams

#### 4.1.3 Engineer Order and Dependency Rules
You asked “who goes first” and “who goes second”:

Engineer 1 — DevOps Environment Engineer goes first

Goal: make the stack runnable locally and unblock integration work.

Engineer 2 — Full-Stack AI Application Engineer follows

Goal: implement diagnostic_api, RAG, schemas, validators, logging, and the Dify workflow integration.

 

Critical handoff contract (must be decided early):

API endpoints + request/response schemas

Environment variable names and service hostnames inside Docker network

To prevent blocking, DevOps will stand up the stack with a stub diagnostic_api first; the AI engineer then replaces it with real implementation.

#### 4.1.4 Detailed Development Plan - Ticket-Style Prompts (DevOps Engineer)
Each ticket below is written as a “task ticket prompt” your engineer can follow directly.

#### DEVOPS-001 — Bootstrap local-first Docker network + port exposure policy

Owner: DevOps Environment Engineer
Depends on: none

 

Task
Create the baseline Docker networking model for Phase 1:

one internal Docker network for service-to-service traffic

only bind user-facing ports to 127.0.0.1

Deliverables

/infra/docker-compose.yml with an internal network defined (even if services are placeholders)

Documented exposed ports list

Acceptance Criteria

docker compose up -d works

docker compose ps shows services healthy/started

No service is exposed on 0.0.0.0

Internal-only services (Weaviate/Postgres/Redis/diagnostic_api) are not accessible from LAN

Suggested tools (default)
Docker Compose v2

 

If proposing alternatives
Provide rationale + how it preserves local-first + isolation (no Kubernetes for Phase 1 unless there’s a hard reason).

 

Prompt (copy/paste to engineer)

You are the DevOps engineer. Implement /infra/docker-compose.yml with an internal Docker network and localhost-only port bindings. Ensure only the UI ingress is exposed on 127.0.0.1. Provide the final compose file, explain which ports are exposed and why, and include run commands and verification commands.

#### DEVOPS-002 — Stand up Dify (pinned release) + Postgres + Redis

Owner: DevOps Environment Engineer
Depends on: DEVOPS-001

 

Task
Add official Dify docker deployment (do not fork) with pinned versions. Include Postgres and Redis.

 

Deliverables

Dify services in /infra/docker-compose.yml

/infra/.env.example with required Dify environment variables (placeholders)

Volume persistence for Postgres/Redis

Acceptance Criteria

Dify web UI is reachable on localhost

Postgres/Redis persist across container restarts

Images are pinned (no latest)

Suggested tools (default)
Official Dify docker-compose deployment (pinned tag)

 

Prompt

Add Dify to /infra/docker-compose.yml using official images pinned to a specific release tag. Add Postgres + Redis with persistent volumes. Bind Dify web to 127.0.0.1 only. Produce /infra/.env.example with documented variables. Include a short “how to verify Dify is up” checklist.

#### DEVOPS-003 — Add Weaviate (pinned) with persistence + readiness check

Owner: DevOps Environment Engineer
Depends on: DEVOPS-001

 

Task
Run Weaviate locally for RAG retrieval.

 

Deliverables

Weaviate service in compose

Volume persistence for Weaviate

Health/readiness endpoint documented in LOCAL_SETUP.md later

Acceptance Criteria

Weaviate responds to readiness endpoint from inside network

Data persists across restarts

Not exposed to LAN

Prompt

Add Weaviate to docker-compose with pinned version and persistent storage. Ensure it is only reachable inside the Docker network. Provide health check configuration and a simple curl command to validate readiness from host (if port is exposed locally, it must still be bound to 127.0.0.1 only).

#### DEVOPS-004 — Provide local LLM endpoint connectivity (Ollama preferred)

Owner: DevOps Environment Engineer
Depends on: DEVOPS-001

 

Task
Ensure containers can reach an OpenAI-compatible endpoint for local LLM inference (Ollama or vLLM).

 

Deliverables

Documented approach for:

macOS/Windows Docker Desktop: host.docker.internal

Linux: host gateway or run Ollama in container

Environment variables for LLM base URL in .env.example

Acceptance Criteria

A container can reach the LLM endpoint successfully (document a test)

No external hosted LLM dependency

Prompt

Implement and document how Docker containers reach a local LLM endpoint (Ollama preferred). Provide OS-specific notes (Linux vs Docker Desktop). Add .env.example variables for LLM_BASE_URL and model name. Provide a verification step that runs from inside a container.

#### DEVOPS-005 — Add stub diagnostic_api service + health route check

Owner: DevOps Environment Engineer
Depends on: DEVOPS-001

 

Task
Add diagnostic_api as a container service even before real code exists (stub is fine) so Dify/Weaviate wiring can be validated.

 

Deliverables

diagnostic_api service in compose (initially can be a minimal FastAPI hello image or local build target)

Local-only port binding (optional; internal use preferred)

Acceptance Criteria

GET /health returns 200 from inside Docker network

Service DNS name works (e.g., http://diagnostic_api:8000/health)

Prompt

Add a diagnostic_api service to docker-compose. If the real app isn’t ready, use a minimal FastAPI stub that exposes /health. Ensure the service is reachable by other containers via Docker DNS. Document the internal URL.

#### DEVOPS-006 — SSRF / egress control baseline for Dify

Owner: DevOps Environment Engineer
Depends on: DEVOPS-002, DEVOPS-005, DEVOPS-003, DEVOPS-004

 

Task
Configure Dify outbound access rules so it can only call:

diagnostic_api

local LLM endpoint

Weaviate (if needed)

Deliverables

Config notes in /docs/SECURITY_BASELINE.md

Dify configuration set accordingly (where applicable)

Acceptance Criteria

Dify workflow HTTP calls succeed only to allowlisted targets

Any attempt to call non-allowlisted addresses fails (document how you tested)

Prompt

Configure Dify SSRF proxy / outbound restrictions so runtime egress is limited to internal services and the local LLM endpoint. Document exact allowlist rules and how to test both allowed and blocked outbound requests.

#### DEVOPS-007 — Create /docs/LOCAL_SETUP.md

Owner: DevOps Environment Engineer
Depends on: DEVOPS-002..006

 

Task
Produce copy/paste setup instructions for a clean laptop.

 

Deliverables

/docs/LOCAL_SETUP.md

Acceptance Criteria

A new developer can bring stack up with one linear set of commands

Includes troubleshooting (ports, Docker resources, model download)

Prompt

Write /docs/LOCAL_SETUP.md with step-by-step commands to install prerequisites, configure .env, start Docker Compose, start Ollama, and verify each service. Include a troubleshooting section and minimum laptop specs.

#### DEVOPS-008 — Create /docs/SECURITY_BASELINE.md

Owner: DevOps Environment Engineer
Depends on: DEVOPS-001..006

 

Task
Document the security posture for Phase 1 local pilot.

 

Deliverables

/docs/SECURITY_BASELINE.md

Acceptance Criteria

Explicitly lists what is exposed and what is not

Includes “no raw sensor data to LLM” reminder (as an operational control)

Includes secrets handling + rotation notes

Prompt

Write /docs/SECURITY_BASELINE.md describing local-only exposure, internal networks, SSRF restrictions, secrets handling, and the privacy boundary: never send raw sensor data to LLM. Include a short “audit checklist” someone can run before demo.

#### DEVOPS-009 — Smoke test script (health + schema + retrieval + logging)

Owner: DevOps Environment Engineer
Depends on: DEVOPS-003, DEVOPS-005, DEVOPS-007

 

Task
Automate verification.

 

Deliverables

/infra/smoke_test.sh or /infra/smoke_test.py

Acceptance Criteria

Script checks:

Dify reachable on localhost

Weaviate ready

diagnostic_api /health OK

diagnostic_api /v1/rag/retrieve returns chunks with doc_id#section

diagnostic_api /v1/vehicle/diagnose returns schema-valid JSON (initially can be stubbed)

A persisted interaction log record exists after the run

Prompt

Implement a smoke test script that verifies the full local stack. The script must fail fast on missing dependencies and print actionable errors. It must validate at least one schema-valid diagnostic JSON response and verify a persisted log record exists.

#### DEVOPS-010 — Replace stub with real app build integration

Owner: DevOps Environment Engineer
Depends on: AIAPP-002 (below)

 

Task
Update compose so diagnostic_api is built from repo, not stub.

 

Deliverables

Dockerfile / compose build configuration for diagnostic_api

Updated smoke test if endpoints changed

Acceptance Criteria

docker compose build diagnostic_api works

Smoke test passes end-to-end

Prompt

Switch diagnostic_api service to build from the repository source. Ensure dependencies are pinned, build is reproducible, and the smoke test passes.

#### 4.1.5 Detailed Development Plan - Ticket-Style Prompts (Full-Stack AI App Engineer)
#### AIAPP-001 — Define strict JSON schema v1.0 + Pydantic models

Owner: Full-Stack AI Application Engineer
Depends on: none (but should align with DevOps stub routes)

 

Task
Create the authoritative Phase 1 “expert output” schema and code-level models.

 

Deliverables

/expert_model/schema/diagnosis_output_v1_0.json

/diagnostic_api/models/*.py Pydantic models mirroring schema

Enumerations/placeholders for:

subsystems (target 8)

fault types (target ~33) — can be placeholders in Phase 1

Acceptance Criteria

Output schema can be validated deterministically in Python

Schema requires citations per recommendation OR NO_SOURCE

Includes limitations and “data missing” handling fields

Suggested tools (default)
FastAPI + Pydantic

 

Prompt

Implement strict JSON schema v1.0 for the diagnostic expert output, plus matching Pydantic models. Enforce citations (doc_id#section) for any recommendation, or NO_SOURCE with an explicit uncertainty statement. Provide a small Python validator function and at least 3 unit tests: valid output, missing citation, invalid type.

#### AIAPP-002 — Build diagnostic_api FastAPI skeleton (health + version + typed errors)

Owner: Full-Stack AI Application Engineer
Depends on: DEVOPS-005 (stub exists) but can proceed independently

 

Task
Create the real FastAPI service that will replace the stub.

 

Deliverables

/diagnostic_api/main.py with:

GET /health

GET /version

Project packaging / dependency pinning (requirements/poetry/uv)

Structured JSON logging scaffold (even if minimal at first)

Acceptance Criteria

Runs locally with uvicorn

Passes lint/type check strategy you select (document it)

Complies with Google style (80 char lines, docstrings, typing)

Prompt

Create a FastAPI app in /diagnostic_api with /health and /version. Use Pydantic models for responses. Add structured JSON logging scaffolding. Include run commands, a minimal Dockerfile if appropriate, and unit tests for /health.

#### AIAPP-003 — Implement privacy boundary + PII redaction module

Owner: Full-Stack AI Application Engineer
Depends on: AIAPP-002

 

Task
Create a deterministic redaction layer for all user-entered text and maintenance notes.

 

Deliverables

/diagnostic_api/privacy/redaction.py

Unit tests demonstrating removal of:

names (basic patterns)

phone numbers

email addresses

overly precise location strings (as defined by your rule)

Acceptance Criteria

Redaction runs before logging and before any LLM prompt build

Tests prove raw PII isn’t present in logs or LLM context builder output

Prompt

Implement redact_pii(text: str) -> str and wire it into request handling so user-entered text is redacted before logging and before prompt building. Add tests for phone/email/name-like patterns. Document limitations (it won’t be perfect, but must be deterministic and safe-by-default).

#### AIAPP-004 — Implement persistent interaction logging (Phase 1.5-ready)

Owner: Full-Stack AI Application Engineer
Depends on: AIAPP-002, AIAPP-003

 

Task
Log structured records that can later become training data.

 

Deliverables

/diagnostic_api/logging/interaction_logger.py

A persisted storage mechanism:

local JSONL file volume OR Postgres table (your choice)

Logged fields (minimum):

request_id, vehicle_id (pseudonymous), timestamps

redacted user_input

retrieved_chunks with doc_id and section

tool outputs

final response JSON

validation results (pass/fail)

Acceptance Criteria

Records persist across container restarts

No raw sensor data, no PII in persisted logs

Unit test confirms log write happens on request

Prompt

Implement persistent JSON logging for each diagnosis request. Logs must be training-ready: include redacted inputs, retrieved chunk metadata (doc_id#section), tool outputs, final JSON, and validation results. Ensure logs persist via Docker volume. Add tests that assert sensitive fields are not present.

#### AIAPP-005 — Implement Weaviate retrieval client + /v1/rag/retrieve

Owner: Full-Stack AI Application Engineer
Depends on: DEVOPS-003 (Weaviate exists), AIAPP-002

 

Task
Provide RAG retrieval as a typed API.

 

Deliverables

/rag/ retrieval utilities

/diagnostic_api/routes/rag.py endpoint POST /v1/rag/retrieve

Chunk schema includes doc_id, section_anchor, text, score

Acceptance Criteria

Endpoint returns consistent structure even when 0 results

Every returned chunk has doc_id#section_anchor

No network calls outside the local Docker network

Prompt

Implement /v1/rag/retrieve that queries Weaviate and returns top-k chunks with doc_id, section_anchor, text, and score. Enforce presence of citation metadata. Add unit tests using mocks, plus an integration test that can run when Weaviate is available.

#### AIAPP-006 — Implement document ingestion pipeline (Phase 1 seed corpus)

Owner: Full-Stack AI Application Engineer
Depends on: DEVOPS-003, AIAPP-005

 

Task
Create a repeatable ingestion process for manuals/SOPs into Weaviate.

 

Deliverables

/rag/ingest.py CLI script

Chunking strategy documented (size, overlap)

Sample docs folder (even 1–3 small markdown SOPs) for pilot

Acceptance Criteria

Running ingest twice is idempotent (no duplicates or has clean overwrite strategy)

After ingest, /v1/rag/retrieve returns results for known queries

Prompt

Build a CLI ingestion pipeline that takes a folder of documents, chunks them, assigns doc_id and section_anchor, and upserts into Weaviate. Document the chunking parameters and provide a small sample corpus so the pipeline is demoable immediately.

#### AIAPP-007 — Prompt templates for strict JSON + citation discipline

Owner: Full-Stack AI Application Engineer
Depends on: AIAPP-001, AIAPP-005

 

Task
Create LLM prompts that reliably output schema-valid JSON and enforce citations.

 

Deliverables

/expert_model/prompts/diagnosis_system_prompt.md

/expert_model/prompts/diagnosis_user_prompt.md

“No raw sensor data” rule included in prompt content

Acceptance Criteria

Prompt explicitly instructs: JSON only, no markdown

Prompt defines citation format: doc_id#section

Prompt defines NO_SOURCE rules when retrieval has nothing relevant

Prompt

Write prompt templates that produce strict JSON matching schema v1.0. The prompt must enforce citation discipline and forbid raw sensor data in context. Provide at least 2 example prompt+expected-output pairs (goldens) and add tests that validate the example outputs against the schema.

#### AIAPP-008 — /v1/vehicle/diagnose: orchestrate summarize → retrieve → LLM → validate → log

Owner: Full-Stack AI Application Engineer
Depends on: AIAPP-001..007

 

Task
Implement the core diagnosis endpoint for Phase 1 (cloud diagnosis pilot).

 

Deliverables

POST /v1/vehicle/diagnose endpoint

Input model includes only LLM-safe summaries:

OBD-II summary, risk scores, maintenance snippets (redacted)

Pipeline steps:

validate + redact

build retrieval query

retrieve top-k chunks

call local LLM endpoint

validate output schema

if invalid → repair once

persist log record

Acceptance Criteria

Works end-to-end locally with Ollama/vLLM endpoint

Returns schema-valid JSON (or a schema-valid “data missing / cannot conclude” response)

Emits a persisted interaction log record

Prompt

Implement /v1/vehicle/diagnose as the Phase 1 cloud diagnosis endpoint. Inputs must be summaries only (no raw waveforms/audio/video/full GNSS). The endpoint must: redact PII, retrieve supporting SOP/manual chunks from Weaviate, call the local LLM, validate the JSON output against schema v1.0, attempt a single repair if invalid, then persist a structured log. Add unit tests for validation failure and repair behavior.

#### AIAPP-009 — Validation + “repair once” mechanism

Owner: Full-Stack AI Application Engineer
Depends on: AIAPP-001, AIAPP-007, AIAPP-008

 

Task
Implement deterministic schema validation + controlled repair.

 

Deliverables

/diagnostic_api/validation/schema_validator.py

Optional endpoint: POST /v1/llm/repair_json

Tests covering:

invalid JSON

missing fields

wrong types

missing citations

Acceptance Criteria

Repairs are attempted at most once

If still invalid, returns a safe failure JSON (schema-valid) and logs the failure

Prompt

Add a schema validation layer and a single-attempt repair flow. If the model output fails validation, call a “repair” prompt once; if it still fails, return a schema-valid failure response with explicit limitations. Add tests for each failure mode and confirm no infinite loops.

#### AIAPP-010 — Dify workflow export that uses the diagnostic_api tools

Owner: Full-Stack AI Application Engineer
Depends on: DEVOPS-002, DEVOPS-006, AIAPP-008

 

Task
Implement the Phase 1 workflow in Dify so the UI can run a diagnosis.

 

Deliverables

A Dify workflow definition/export file stored in repo (e.g., /expert_model/dify/diagnosis_workflow_export.json)

Minimal instructions to import/run it

Acceptance Criteria

Workflow calls diagnostic_api endpoints successfully

Workflow returns the final JSON output to the UI

SSRF restrictions still allow only intended calls

Prompt

Build and export a Dify workflow for Phase 1 “cloud diagnosis”. The workflow should collect inputs (vehicle summary + symptoms), call /v1/rag/retrieve, call the local LLM with the strict JSON prompt, validate/repair via the API, and return the final JSON. Export the workflow file into the repo and document import/run steps.

#### 4.1.6 The "Good Enough" Integration Sequence (So Nobody Blocks)

If you want the shortest path to a working demo:

DevOps: DEVOPS-001 → 002 → 003 → 004 → 005 (stub)

AI Engineer: AIAPP-001 → 002 → 005 → 006 → 007 → 008

DevOps: DEVOPS-006 → 007 → 009

AI Engineer: AIAPP-009 → 010

DevOps: DEVOPS-010 (wire real build)

That order gets you: local runnable system + one end-to-end diagnosis flow + logs.

### 5. Future Optimizations (Quality Phase)

#### OPT-01 — Advanced RAG Chunking
Owner: Full‑Stack AI Application Engineer
Depends on: APP-03
Reference: [Chunking Best Practices](https://example.com/chunking)

PROMPT (task ticket):
Title: OPT-01 Upgrade to Recursive/Semantic Chunking
Context: Current chunker splits by whitespace every 500 chars. This breaks context in technical manuals.
Task:
- Implement recursive character splitting (paragraphs -> sentences).
- Implement semantic splitting (break when topic changes).
- Respect Markdown headers.
Acceptance Criteria:
- Chunks do not cut off mid-sentence.
- Header context is preserved in metadata.

#### OPT-02 — Optimized Embeddings (Nomic/BGE)
Owner: Full‑Stack AI Application Engineer
Depends on: APP-03
Reference: [MTEB Leaderboard](https://huggingface.co/spaces/mteb/leaderboard)

PROMPT (task ticket):
Title: OPT-02 Switch to Nomic/BGE embedding models
Context: Current `llama3:8b` is a chat model, not optimized for retrieval.
Task:
- Pull `nomic-embed-text` or `bge-m3` via Ollama.
- Configure `embedding.py` to use the new model.
- Re-ingest all data.
Acceptance Criteria:
- Retrieval accuracy improves (measure top-k relevance).
- Embeddings are generated faster.
