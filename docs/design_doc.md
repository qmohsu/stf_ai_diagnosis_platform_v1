# Pilot Expert Model Training Pipeline (LLM + RAG + Tooling) for AI-Assisted Vehicle Predictive Diagnosis

**Revised to include Phase 1 → Phase 1.5 → Phase 2 plan (incl. LlamaFactory)**

## Document control

| Field | Value |
|-------|-------|
| **Doc title** | Pilot Expert Model Training Pipeline (LLM + RAG + Tooling) for Vehicle Predictive Diagnosis |
| **Project** | AI-assisted vehicle self-diagnosis + fleet management (edge + cloud) |
| **Status** | Draft v3.3 (Audio feedback recording) |
| **Owner** | (You / ML Lead) |
| **Contributors** | ML engineers; data engineers; backend engineers; DevOps; security reviewer; workshop/technician SMEs |
| **Last updated** | 2026-03-22 (v3.3) |
| **Primary pilot stack** | FastAPI (diagnostic_api) + (Ollama or vLLM OpenAI-compatible server) + Next.js (obd-ui) + pgvector (PostgreSQL) |
| **New in this revision** | APP-37: Audio feedback recording (GitHub Issue #12). Optional voice recording on all feedback forms via browser MediaRecorder API. Two-step upload flow (staging + token linking). Audio files stored on disk with Docker named volume. Playback via JWT-authed endpoint with Blob URL in frontend. |

### Revision history

| Version | Date | Summary |
|---------|------|---------|
| v3.3 | 2026-03-22 | Audio feedback recording (APP-37, GitHub Issue #12): Optional voice recording on all 5 feedback forms via browser MediaRecorder API (WebM/Opus, max 120s/5 MB). Two-step upload: `POST /v2/obd/audio/upload` stages file and returns token; feedback JSON includes token to link audio. `GET /v2/obd/audio/{feedback_id}` streams playback with JWT auth. Audio stored on disk (`/app/data/audio/`) via Docker named volume. 3 new columns on `_OBDFeedbackMixin`. New `AudioRecorder.tsx` component. `FeedbackHistoryView` inline audio player with auth Blob URLs. Startup cleanup of stale staging files. i18n (EN/zh-CN/zh-TW). 12 new tests. |
| v3.2 | 2026-03-21 | PolyU GPU server deployment (DO-07): Podman compose override (`docker-compose.polyu.yml`) with CDI GPU passthrough for Ollama. Nginx reverse proxy (`nginx/nginx.conf`) as sole external gateway on port 80, proxying frontend (`/`) and API (`/v1/`, `/v2/`, `/auth/`, `/health`, `/docs`) with SSE streaming support. Server-specific env template (`.env.polyu.example`). Automated setup (`polyu-setup.sh`) and deploy (`polyu-deploy.sh`) scripts. Comprehensive deployment guide with backup, monitoring, troubleshooting, and multi-user GPU etiquette (GitHub Issue #21) |
| v3.1 | 2026-03-21 | Feedback-diagnosis link (APP-36): `diagnosis_history_id` FK on AI/premium feedback tables, SSE `done`/`cached` emit generation ID, feedback retrieval returns model name + generation timestamp, frontend threads history ID through components (GitHub Issue #9) |
| v3.0 | 2026-03-21 | Session dashboard (APP-35): `GET /v2/obd/sessions` paginated listing endpoint, `/sessions` page in obd-ui, navigation links, i18n (GitHub Issue #10) |
| v2.9 | 2026-03-21 | Weaviate → pgvector migration (APP-34): eliminated Weaviate Docker service, consolidated vector storage into PostgreSQL via pgvector extension, HNSW index for cosine similarity |
| v2.8 | 2026-03-16 | OBD threshold rationale docs (APP-33): `docs/preprocessing_rationale.md` — sources and rationale for all pre-processing thresholds |
| v2.7 | 2026-03-16 | i18n support (APP-32): EN/zh-CN/zh-TW via react-i18next, LanguageSwitcher, 150+ keys per locale, CJK fonts |
| v2.6 | 2026-03-09 | Dead code removal (APP-30): deleted unused cache module, validate.py, schemas.py, dead client methods, orphaned test script (~380 LOC) |
| v2.4 | 2026-03-09 | Code review cleanup (APP-29): drop V1 tables migration, lifespan migration, print→logging, datetime fix, error leakage fix, dev bind fix |
| v2.3 | 2026-03-08 | Removed V1 API layer, PII redaction, VIN validation for R&D prototype |
| v2.2 | 2026-03-08 | JWT auth + per-user session isolation (APP-28) |
| v2.1 | 2026-03-07 | Removed Dify dependency |
| v2.0 | 2026-03-05 | Translation performance fix (80x speedup), premium LLM model list update |
| v1.x | 2026-01–03 | Initial pilot: OBD UI, DB persistence, feedback, AI diagnosis, premium LLM, history, RAG image parsing |

## Related project deliverables (from proposal)
•	Deliverable 1: Database establishment + preprocessing (1–18 months)
•	Deliverable 2: Cloud deep diagnostic AI engine (7–12 months)
•	Deliverable 3: Lightweight edge AI diagnostic module (13–18 months)
•	Deliverable 4: Location monitoring module (15–18 months)
•	Deliverable 5: Fleet management platform (19–24 months)
## 1) Executive summary
This pilot delivers an “expert model” layer (LLM + retrieval + tool-calling) that turns outputs from the deep predictive diagnosis engine into grounded, structured, technician-grade guidance. The expert layer is designed to run fully on-prem (no external LLM calls) and to produce machine-checkable JSON with traceable citations to SOP/manual sources.
•	Inputs (Phase 1 baseline):
•	Technician/fleet question + vehicle context (pseudonymous vehicle_id, time range, symptom notes)
•	diagnostic_api output (risk scores, top faults, evidence summaries, explicit limitations)
•	Retrieved SOP/manual snippets (RAG) with stable doc_id + section anchors
•	Outputs (all phases):
•	Strict JSON that follows a non-negotiable schema (for logging, evaluation, and downstream workflow integration)
•	A short human-readable summary (derived from JSON) for technicians
•	Citations per recommended action (or explicit “no supporting doc found”)
This design intentionally keeps the model interface stable across phases: the web UI (`obd-ui`) and FastAPI backend (`diagnostic_api`) talk to an OpenAI-compatible model endpoint. Phase 1.5/2 adds LlamaFactory to fine-tune a model on real pilot interactions and then swaps the model endpoint without rewriting workflows, RAG ingestion, or diagnostic API contracts.
## 2) Problem statement and goals
### 2.1 Problem
Technicians and fleet operators need fast, interpretable guidance, but fault patterns are multi-modal, heterogeneous, and noisy (OBD-II + vibration/acoustic + vision + GNSS/IMU + driver-state). Deep models output probabilities/risk scores, yet field action still depends on SOP-aligned interpretation and consistent documentation.
### 2.2 Goals (pilot)
G1 — Grounded expert assistance: Answers must be grounded in diagnostic_api outputs and retrieved knowledge; no free-form guessing.
G2 — Strict structured output: Every response must validate against a strict JSON schema.
G3 — Tool-calling reliability: Reliable diagnostic_api invocation; safe failure when evidence is missing.
G4 — Local-first privacy/security: Operate on-prem; minimize exposure of sensitive data to the LLM context.
G5 — Phase-ready learning loop: Log pilot interactions so Phase 1.5/2 fine-tuning is data-driven and measurable.
### 2.3 Non-goals (pilot)
•	Training a base LLM from scratch.
•	Replacing the diagnostic deep learning model.
•	Full production fleet platform rollout (pilot uses `obd-ui` Next.js app; production UI later can integrate with your FastAPI/Vue stack).
## 3) Scope and deliverables (Phase 1 → 1.5 → 2)
### 3.1 Phase 1 (baseline pilot: Prompt + RAG + tool calling)
•	FastAPI workflow orchestration and Next.js web UI (`obd-ui`) for technician Q&A (internal pilot).
•	diagnostic_api (FastAPI) that wraps deep model inference + summary generation (LLM-safe).
•	OBD Agent (edge collector) + OBDSnapshot telemetry ingestion + Pass‑1 (OBD→subsystem+PID shortlist) mapping.
•	RAG knowledge ingestion into vector store (SOPs/manuals/checklists; curated excerpts of maintenance reports). Includes PDF image parsing pipeline: OCR (easyocr, CJK+English) for text-in-image extraction, vision model descriptions, full-page rendering, CJK→English translation (Ollama chat API with thinking disabled), and image-aware chunking.
•	Strict JSON output contract with schema validation + citations per action.
•	Observability: logs for each interaction (inputs, retrieved chunks, tool outputs, JSON validation, latency).
•	Security baseline: local-only deployment, RBAC, network allow-listing for outbound calls.
### 3.2 Phase 1.5 (model improvement: data-driven LoRA/SFT via LlamaFactory)
•	Convert Phase 1 interaction logs into training examples (“case packages”) with SME corrections as ground truth.
•	Use LlamaFactory to run parameter-efficient fine-tuning (LoRA/QLoRA) for: schema adherence, safer tool use, SOP-aligned phrasing, and better clarification questions.
•	Establish an evaluation harness with regression tests (format/citation/tool-call correctness) and an SME review protocol.
•	Deploy the tuned model behind an OpenAI-compatible endpoint (prefer vLLM/SGLang for server inference) and repoint diagnostic_api to it.
### 3.3 Phase 2 (preference optimization + production hardening)
•	Preference tuning (e.g., DPO/KTO/ORPO) using SME-ranked outputs to reduce hallucinations and improve decision-making under uncertainty.
•	Hardening: canary deployments, drift detection, rollback strategy, model registry and versioning, and security review for exposed endpoints.
•	Scale-out: multi-tenant RBAC, audit trails, and integration path into the future FastAPI/Vue fleet management platform.
## 4) Success metrics and phase gates
### 4.1 Pilot KPIs (expert model)

These are acceptance gates; Phase 1 must pass before Phase 1.5 tuning, and Phase 1.5 must pass before Phase 2 tuning.

| Metric | Target | Phase gate | Measurement |
|--------|--------|------------|-------------|
| JSON schema validity | >= 99% parse & validate | Phase 1 / 1.5 / 2 | Automated jsonschema validation on all responses |
| Citation coverage | >= 95% actions cite source OR explicit 'no source' | Phase 1 / 1.5 / 2 | Parse recommended_actions[].source + verify retrieved chunk IDs |
| Tool-call success rate | >= 99% when backend healthy | Phase 1 / 1.5 / 2 | diagnostic_api status codes + retries |
| SME acceptance | >= 80% sampled cases 'actionable' | Phase 1 / 1.5 / 2 | Technician review rubric |
| Unsupported mechanical claims | <= 5% of sampled cases | Phase 1 / 1.5 / 2 | SME review + heuristic detector |
| Latency (end-to-end) | < TBD seconds | Phase 1 / 1.5 / 2 | Instrumented timings: tool call + retrieval + generation |
| Regression stability | No degradation on locked eval set | Phase 1.5 / 2 | Compare tuned model vs baseline on fixed test suite |

## 5) Stakeholders and responsibilities

- **Workshop SMEs / technicians:** Provide labeling ground truth; review recommendations; approve safety-sensitive outputs.
- **ML team:** Maintain diagnostic deep model outputs; add explainable summary fields; support evaluation.
- **Data engineering:** Build ingestion + preprocessing for multi-modal streams and maintenance logs.
- **Backend:** Provide diagnostic_api wrapper for inference and summary retrieval; enforce data boundaries via schema validation.
- **DevOps/Security:** Local deployment; secrets management; network policy; vulnerability review; SRAA readiness.
## 6) System context and constraints
### 6.1 Data sources and modalities (existing project truth)
•	OBD-II telemetry (RPM, throttle, coolant temperature, MAP, fuel trim, oxygen sensor signals, DTCs, etc.)
•	Vibration/acoustic signals with derived features (RMS, kurtosis, spectral energy, Mel-scale coefficients; denoising via band-pass + wavelet filtering).
•	Dual cameras (road-scene + driver-state monitoring).
•	GNSS/IMU for positional and dynamic behavior analysis.
•	Operational metadata (trip logs, idling duration, dispatch metadata).
### 6.2 Fleet + dataset scale targets (constraints for training & evaluation)
Pilot constraints include a supporting-party fleet (e.g., 20 vehicles with 5G OBU + multi-sensor suite) and a target of >= 1,500 hours of synchronized, workshop-verified annotated data. Some documents reference additional vehicles/hours; treat those as TBD and reconcile early because they affect evaluation representativeness.
### 6.3 Diagnostic label space (taxonomy)
Your materials reference multiple taxonomies (8 system categories; 17-class; 33 specific fault types). The pilot should choose a primary taxonomy and include a 'taxonomy' field in API outputs so the expert model remains forward-compatible.
## 7) Pilot architecture overview
### 7.1 High-level components
•	diagnostic_api (FastAPI): workflow orchestration, REST API, LLM-safe summarization, RAG retrieval, schema validation, and tool-calling logic. Handles the full pipeline natively (HTTP tool call, retrieval, generation, schema validation).
•	Model server (Phase 1): Ollama (OpenAI-compatible endpoints).
•	Model server (Phase 1.5/2): tuned model served via vLLM/SGLang (OpenAI-compatible), or Ollama with adapters (if chosen).
•	Vector store: pgvector (PostgreSQL extension) for SOP/manual chunks and sanitized knowledge. Chunk metadata includes `has_image` flag and `metadata_json` (JSONB) for image-containing chunks.
•	Postgres: session persistence, diagnosis history, feedback tables, and OBD snapshot storage.
•	OBD Agent (edge collector): a separate service/daemon (python‑OBD or equivalent) that reads ELM327 OBD‑II and posts sanitized OBDSnapshot telemetry to diagnostic_api.
•	**OBD Expert Diagnostic Web UI (`obd-ui`)**: Next.js 15 (TypeScript, Tailwind CSS, shadcn/ui, recharts) on port 3001. Provides experts with a visual interface to submit OBD logs, view analysis results across five tabs (Summary, Detailed, RAG, AI Diagnosis, History), and submit structured feedback per tab (up to 10 submissions per tab per session). Session dashboard (`/sessions`) lists all past analysis sessions with status filter, pagination, and diagnosis indicators (`has_diagnosis`, `has_premium_diagnosis`). History tab displays all past AI diagnosis generations with provider badge, model name, timestamp, and expandable text. RAG tab displays retrieved context; AI Diagnosis tab contains Local LLM / Cloud LLM (OpenRouter) sub-tabs for side-by-side comparison — local streams via SSE from Ollama, premium streams via SSE from OpenRouter (opt-in, multi-model). Premium sub-tab includes a model selector dropdown populated from admin-curated list. Communicates with diagnostic_api via `/v2/obd/*` endpoints. Runs as a standalone Docker service.
•	**Premium LLM client (opt-in)**: `PremiumLLMClient` using OpenAI Python SDK (`AsyncOpenAI`) pointing at **OpenRouter** (`base_url=https://openrouter.ai/api/v1`) for cloud-based diagnosis. Supports any model available on OpenRouter; admin-curated model list configured via `PREMIUM_LLM_CURATED_MODELS` env var. Feature-gated (`PREMIUM_LLM_ENABLED=false` by default). The only component that requires internet access. Uses the same prompts and RAG context as the local Ollama client.
### 7.2 Deployment principle: local-first and interface invariants
Interface invariants that must not change across phases:
•	diagnostic_api calls the model through an OpenAI-compatible base URL.
•	diagnostic_api schema stays stable; new fields are additive.
•	Expert output JSON schema is versioned and backward compatible.
•	RAG doc_id + section anchors are stable (no silent renumbering). Image markers (`[Image N, Page M]`, `[OCR, Page M]`, `[Full Page, Page M]`) are stable inline references within section bodies.

**Exception — Premium LLM (opt-in internet access):**
The premium LLM client is the sole exception to the local-only deployment rule. It is disabled by default (`PREMIUM_LLM_ENABLED=false`) and requires an explicit `PREMIUM_LLM_API_KEY` (OpenRouter API key). When enabled, the diagnostic_api container must have outbound internet access to reach the OpenRouter API (`PREMIUM_LLM_BASE_URL`, default `https://openrouter.ai/api/v1`). All other services remain strictly local.
### 7.3 Network flow (reference)
The deployment consists of the Next.js web UI (`obd-ui`, port 3001), the FastAPI backend (`diagnostic_api`), Ollama (model server), and Postgres with pgvector (database + vector store). All services communicate over a dedicated internal Docker network. Only the Nginx reverse proxy handles ingress; backend services are not exposed to the LAN. The outbound allow-list should be enforced at the network layer to restrict calls to internal services only.

**PolyU server deployment (Podman):** The PolyU HK GPU server (2x RTX 6000 Ada, 92 GB VRAM) uses Podman instead of Docker for rootless multi-user access. GPU passthrough uses CDI (Container Device Interface) via `devices: ["nvidia.com/gpu=all"]` in the Podman compose override (`infra/docker-compose.polyu.yml`). Nginx is deployed as a container service on port 80, proxying all external traffic to the frontend and API. SSE streaming endpoints have `proxy_buffering off` for real-time token delivery. See `docs/deployment_polyu.md` for full setup instructions.
## 8) Data architecture for the expert model pipeline
### 8.1 Data boundaries: what the LLM can and cannot see
Allowed in LLM context (summaries only):
•	vehicle_id (pseudonymous), time_range, and relevant context flags
•	diagnostic model summary outputs (risk scores, top-k faults, confidence, explicit limitations)
•	DTC codes + key OBD-II parameter summaries and trends
•	derived feature summaries (no raw waveforms/images)
•	retrieved SOP/manual snippets (with doc_id + section anchors)
Not allowed in LLM context (keep in backend):
•	raw audio/video frames, vibration waveforms, and full GNSS tracks
•	any personal data (faces, voice) and raw location details
•	direct identifiers beyond what the workflow needs (names, phone, plate numbers, etc.)

### 8.1.1 OBDSnapshot contract (edge → cloud)
The edge collector must send a **sanitized, JSON-only** snapshot to the cloud. This keeps hardware access, serial I/O, and any adapter quirks out of the cloud API workers.

**Design rules**
• Store the full OBDSnapshot in the backend (for audit + reprocessing), but only send **derived summaries** into the LLM context.
• Never include raw adapter debug logs, raw CAN frames, or high-frequency time-series arrays in this payload.
• Treat OBDSnapshot as an additive contract: new fields can be added, but existing fields must remain backward compatible.

**Minimum payload (illustrative)**
```json
{
  "vehicle_id": "V123",
  "ts": "2026-02-01T12:34:56Z",
  "adapter": {"type": "ELM327", "port": "/dev/ttyUSB0"},
  "dtc": [{"code":"P0301","desc":"..."}],
  "freeze_frame": {"RPM": {"value": 850, "unit": "rpm"}},
  "supported_pids": ["RPM","COOLANT_TEMP"],
  "baseline_pids": {"RPM": {"value": 780, "unit": "rpm"}}
}
```

### 8.2 Storage layers (recommended)
•	Raw layer (immutable): object storage (MinIO/S3-compatible), partitioned by date/vehicle/modality.
•	Processed layer: standardized synchronized sequences (e.g., Parquet).
•	Feature layer: extracted features (RMS/kurtosis/MFCC etc.) + OBD summary features.
•	OBD snapshot layer (pilot): Postgres table `obd_snapshots` storing sanitized OBDSnapshot as JSONB, indexed by (vehicle_id, ts).
•	Label layer: workshop-confirmed labels from maintenance records.
•	Case packages: one record per incident/question, used for training and evaluation.

### 8.3 OBD-II Diagnostic Summarization Pipeline (LLM-Ready)

The summarization pipeline converts raw OBD-II log files into structured, LLM-ready diagnostic summaries. This is critical for both RAG (retrieval-augmented generation) and direct LLM prompting.

**Design Principles:**
- Model-agnostic: No dependency on proprietary LLMs or closed diagnostic systems
- Explainable: All extracted features and events are traceable to raw signals
- Composable: Each stage can be independently replaced or extended
- RAG-friendly: Outputs are structured for retrieval and embedding
- Open-source: Built entirely on widely used open-source libraries

#### 8.3.1 Pipeline Stages

| Stage | Purpose | Open-Source Tools | Output |
|-------|---------|-------------------|--------|
| **Stage 0** | Log Parsing & Time-Series Normalization | pandas, numpy | Multivariate time-series dataframe |
| **Stage 1** | Value Statistics Extraction | pandas, tsfresh | Per-signal statistics (mean, std, percentiles, entropy) |
| **Stage 2** | Anomaly Detection with Temporal Context | ruptures, scikit-learn/PyOD, STUMPY | Anomaly events with time windows and context |
| **Stage 3** | Diagnostic Semantic Clue Generation | Rule-based engine | Traceable diagnostic facts for LLM reasoning |

#### 8.3.2 Stage 0: Log Parsing and Time-Series Normalization

**Objective:** Convert raw OBD-II logs into a clean, unified time-series representation.

**Key steps:**
- Parse timestamps and signal identifiers
- Map PIDs to semantic signal names
- Unit normalization
- Resampling to a unified time grid
- Handling missing values (interpolation / masking)

**Output:** Multivariate time-series dataframe: `time × signals`

#### 8.3.3 Stage 1: Value Statistics Extraction

**Objective:** Capture global and local statistical characteristics of each signal.

**Extracted features include:**
- Mean, standard deviation, min, max
- Percentiles (e.g., P95)
- Autocorrelation
- Energy, entropy
- Change rate statistics

**Example output:**
```json
{
  "engine_rpm": {
    "mean": 2150,
    "std": 430,
    "min": 780,
    "max": 5200,
    "p95": 4100
  }
}
```

#### 8.3.4 Stage 2: Anomaly Detection and Temporal Context Mining

**Objective:** Identify diagnostically meaningful abnormal behaviors with context, not just point outliers.

**Methods:**
- **Change-point and regime detection:** ruptures
- **Multivariate anomaly detection:** scikit-learn (Isolation Forest, LOF), PyOD
- **Temporal pattern discovery (optional):** STUMPY (matrix profile)

**Detected anomaly representation:**
```json
{
  "time_window": "2026-02-03 16:48:10 ~ 16:49:30",
  "signals": ["engine_rpm", "maf", "fuel_trim"],
  "pattern": "RPM oscillation with airflow drop",
  "context": "steady cruise, throttle stable",
  "severity": "medium"
}
```

#### 8.3.5 Stage 3: Diagnostic Semantic Clue Generation

**Objective:** Convert statistical and temporal findings into diagnosis-oriented semantic facts suitable for LLM reasoning.

> This stage is intentionally **rule-based**, not LLM-generated, to ensure traceability and avoid hallucination.

**Approach:**
- Domain heuristics (e.g., throttle variance, RPM-frequency coupling)
- Signal interaction rules
- Cause–effect temporal ordering

**Example output:**
```json
{
  "diagnostic_clues": [
    "RPM oscillation occurs without throttle input",
    "Fuel trim increases after RPM drop",
    "No misfire DTC observed during anomaly window"
  ]
}
```

#### 8.3.6 API Endpoints

**Pipeline endpoint:** `POST /v2/tools/summarize-log-raw`

The v2 endpoint accepts raw OBD TSV text and returns the full structured summary including all pipeline stages. The v1 endpoint remains for backward compatibility.

**v2 Response structure:**
```json
{
  "vehicle_id": "V123",
  "time_range": {...},
  "dtc_codes": [...],
  "value_statistics": {...},
  "anomaly_events": [...],
  "diagnostic_clues": [...],
  "pid_summary": {...}
}
```

#### 8.3.7 OBD Expert Diagnostic Web UI Endpoints (Session Persistence + Feedback)

These endpoints wrap the summarization pipeline with session persistence and expert feedback collection, serving the `obd-ui` frontend.

**Endpoint:** `GET /v2/obd/sessions`
- Returns a paginated list of `OBDSessionSummary` items for the authenticated user, sorted by `created_at` descending (newest first)
- Response: `SessionListResponse` containing `items` (list of `OBDSessionSummary`) and `total` count
- Each item includes: `session_id`, `vehicle_id`, `status`, `input_size_bytes`, `created_at`, `updated_at`, `has_diagnosis` (bool), `has_premium_diagnosis` (bool)
- `has_diagnosis` is `True` when the session's `diagnosis_text` is non-null; `has_premium_diagnosis` is `True` when `premium_diagnosis_text` is non-null
- Supports query filters: `status` (PENDING/COMPLETED/FAILED), `vehicle_id` (exact match), `created_after` (ISO 8601 lower bound), `created_before` (ISO 8601 upper bound)
- Supports pagination via `limit` (1-200, default 50) and `offset` (>=0, default 0)
- Scoped to the authenticated user (only returns sessions owned by the current JWT user)

**Endpoint:** `POST /v2/obd/analyze`
- Accepts raw OBD TSV text body (same format as `/v2/tools/summarize-log-raw`)
- **Dedup:** computes SHA-256 hash of the input; if an existing session with the same hash is found in the DB, returns the cached result immediately (no re-analysis)
- Creates a persisted `OBDAnalysisSession` **immediately in Postgres** (UUID, status, SHA-256 input hash, JSONB result). There is no in-memory cache layer; the DB is the sole source of truth for session lifecycle
- Stores raw OBD log to filesystem (`/app/data/obd_logs/{session_id}.txt`) and saves `raw_input_file_path` (relative path) on the session row; also stores `parsed_summary_payload` (structured parsed summary as JSONB)
- Runs `_run_pipeline()` internally (same 5-stage pipeline)
- Returns `session_id` + full `LogSummaryV2` result
- On failure, persists error state for debugging

**Endpoint:** `GET /v2/obd/{session_id}`
- Retrieves a persisted analysis session by UUID
- Returns the stored `LogSummaryV2` from JSONB

**Endpoint:** `POST /v2/obd/{session_id}/diagnose`
- **SSE-streaming AI diagnosis** powered by Ollama (local LLM)
- Streams diagnostic text tokens to the client in real time via Server-Sent Events
- Stores the final `diagnosis_text` on the session row upon completion
- Appends a row to `diagnosis_history` table (provider="local")
- Returns 404 if session not found

**Endpoint:** `POST /v2/obd/{session_id}/diagnose/premium`
- **SSE-streaming AI diagnosis** via premium cloud LLM (OpenRouter, multi-model)
- Accepts optional `model` query param (e.g., `?model=openai/gpt-5.2`); validated against admin-curated list (400 if not in list); defaults to `PREMIUM_LLM_MODEL`
- Feature-gated: returns 403 if `PREMIUM_LLM_ENABLED=false`; returns 503 if API key is missing
- Same SSE event format as `/diagnose` (token, done, cached, error, status)
- Stores `premium_diagnosis_text` and `premium_diagnosis_model` on the session row upon completion
- Appends a row to `diagnosis_history` table (provider="premium")
- Independent from local diagnosis — both can exist simultaneously on the same session
- Uses the same prompts and RAG context as the local endpoint

**Endpoint:** `GET /v2/obd/premium/models`
- Returns `{models: [...], default: "..."}` from admin-curated `PREMIUM_LLM_CURATED_MODELS` config
- Feature-gated: returns 403 if `PREMIUM_LLM_ENABLED=false`

**Endpoint:** `GET /v2/obd/{session_id}/history`
- Returns all `diagnosis_history` rows for a session, ordered by `created_at` descending
- Response: `DiagnosisHistoryResponse` containing `session_id`, `items` (list of `DiagnosisHistoryItem`), and `total` count
- Each item includes: `id`, `session_id`, `provider` ("local"/"premium"), `model_name`, `diagnosis_text`, `created_at`
- Returns 404 if session not found; returns 422 for invalid UUID

**Endpoint:** `GET /v2/obd/{session_id}/feedback`
- Returns all feedback rows across 5 feedback tables for a session, ordered by `created_at` descending
- Response: `FeedbackHistoryResponse` containing `session_id`, `items` (list of `FeedbackHistoryItem`), and `total` count
- Each item includes: `id`, `session_id`, `tab_name` (one of: summary, detailed, rag, ai_diagnosis, premium_diagnosis), `rating`, `is_helpful`, `comments`, `created_at`, `diagnosis_history_id` (nullable), `diagnosis_model_name` (nullable), `diagnosis_created_at` (nullable)
- Does NOT include snapshot columns (`retrieved_text`, `diagnosis_text`) — those are internal
- Supports pagination via `limit` (1-200, default 50) and `offset` (>=0, default 0) query parameters
- Returns 404 if session not found; returns 422 for invalid UUID

**Endpoint:** `POST /v2/obd/{session_id}/feedback/{feedback_type}`
- `feedback_type` is one of: `summary`, `detailed`, `rag`, `ai_diagnosis`, `premium_diagnosis`
- Accepts expert feedback: rating (1-5), is_helpful (bool), optional comments, optional `diagnosis_history_id` (for ai_diagnosis/premium_diagnosis only — validated against session + provider), plus type-specific fields (see table details below)
- **Multiple feedback per session allowed** (up to 10 per feedback type per session); returns 429 when the cap is reached
- Returns 404 if session not found

**Database tables:**
- `obd_analysis_sessions`: id (UUID PK), vehicle_id (indexed), status (indexed), input_text_hash (SHA-256, indexed, used for dedup), input_size_bytes, raw_input_file_path (String(500), relative path to OBD log file on disk), parsed_summary_payload (JSONB), diagnosis_text, premium_diagnosis_text, premium_diagnosis_model (String(200), latest model used), result_payload (JSONB), error_message, created_at, updated_at
- `diagnosis_history`: id (UUID PK), session_id (FK, indexed), provider (String(20), CHECK constraint: `'local'`/`'premium'`), model_name (String(200)), diagnosis_text (Text), created_at. Append-only log of every AI diagnosis generation (local + premium). Each regeneration creates a new row; session columns retain only the latest text for quick access.
- `obd_summary_feedback`: id (UUID PK), session_id (FK), rating, is_helpful, comments, extra_fields (JSONB), created_at
- `obd_detailed_feedback`: id (UUID PK), session_id (FK), rating, is_helpful, comments, extra_fields (JSONB), created_at
- `obd_rag_feedback`: id (UUID PK), session_id (FK), rating, is_helpful, comments, retrieved_text (snapshots the RAG-retrieved text at submission time), extra_fields (JSONB), created_at
- `obd_ai_diagnosis_feedback`: id (UUID PK), session_id (FK), rating, is_helpful, comments, diagnosis_text (snapshots the AI diagnosis at submission time), diagnosis_history_id (nullable FK to `diagnosis_history.id`, links feedback to specific generation), created_at
- `obd_premium_diagnosis_feedback`: id (UUID PK), session_id (FK), rating, is_helpful, comments, diagnosis_text (snapshots the premium AI diagnosis at submission time), diagnosis_history_id (nullable FK to `diagnosis_history.id`, links feedback to specific generation), created_at
## 9) diagnostic_api design (pilot interface contract)
### 9.1 Goals
•	Stable interface for internal FastAPI workflow orchestration.
•	Hide internal time-series complexity and inference details.
•	Enforce data boundaries via schema validation (only send LLM-safe summaries).
•	Be deterministic and testable (responses validate against an API schema).
### 9.2 Required endpoints (minimum)
•	GET /health
•	POST /v1/rag/retrieve
•	GET /v2/obd/sessions (auth required, paginated)
•	POST /v2/obd/analyze (auth required)
•	POST /v2/obd/{session_id}/diagnose (auth required, SSE streaming)
•	POST /v2/obd/{session_id}/diagnose/premium (auth required, SSE streaming)

*(V1 endpoints `/v1/vehicle/diagnose`, `/v1/diagnose/`, `/v1/feedback/`, `/v1/tools/summarize-log*`, `/v1/models` removed — replaced by V2 OBD endpoints.)*

Example request/response (illustrative):

**Request:**

```http
POST /v2/obd/analyze
```

```json
{
  "vehicle_id": "V12345",
  "time_range": {"start":"2026-01-20T00:00:00Z","end":"2026-01-20T01:00:00Z"},
  "question": "Driver reports abnormal vibration when accelerating",
  "dtc_codes": ["P0xxx"],
  "optional_context": {"route_type":"urban", "payload":"unknown"}
}
```

**Response:**

```json
{
  "subsystem_risk": [
    {"subsystem":"Engine System","risk":0.72},
    {"subsystem":"Transmission System","risk":0.41}
  ],
  "predicted_faults": [
    {"name":"engine_misfire","taxonomy":"17-class","confidence":0.62}
  ],
  "confidence": 0.68,
  "rul": {"value": 1200, "unit":"km"},
  "key_evidence": {
    "dtc_codes": ["P0xxx"],
    "obd_summary": {"rpm_range":[...], "coolant_temp_trend":"..."},
    "vibration_summary": {"rms":"...", "kurtosis":"..."},
    "acoustic_summary": {"spectral_shift":"..."}
  },
  "evidence_ids": ["ev_abc123","ev_def456"],
  "limitations": ["No recent coolant temperature data"]
}
```
### 9.3 Notes on taxonomy
Include a 'taxonomy' field and keep API changes additive. This prevents breaking the expert model when moving from 8-category to 17-class or 33-type outputs.

### 9.4 OBD telemetry ingestion (OBD Agent → diagnostic_api)
**Purpose:** ingest edge-collected OBD snapshots without exposing serial/adapter logic inside the cloud API.

**Endpoint:** `POST /v1/telemetry/obd_snapshot`

**Behavior (minimum):**
• Validate payload shape (Pydantic) and reject unexpected high-risk fields (raw logs, raw CAN frames, oversized arrays).
• Persist payload (JSONB) + metadata (vehicle_id, ts, adapter.type) with indexes for latest lookup.
• Return `{snapshot_id, stored_at}`.

**Companion endpoint:** `GET /v1/telemetry/obd_snapshot/latest?vehicle_id=...&max_age_seconds=...`

### 9.5 Pass‑1 mapper (OBD → subsystem shortlist)
Pass‑1 is a deterministic **rules + tables** pipeline that turns DTC(s) + freeze frame + supported PID list (+ symptom tags) into:
• `subsystem_shortlist` (ranked)
• `candidate_pid_shortlist` (10–25 signals that are both relevant **and supported** by this vehicle)

**Where it runs:** inside `diagnostic_api` (pure Python; no hardware calls).

**How it interacts with the expert model:**
• The LLM sees only the derived Pass‑1 summary (subsystems + candidate PIDs + freeze-frame highlights + limitations).
• The raw OBDSnapshot stays in Postgres and is never pasted into prompts.

**Additive response field (recommended):**
```json
{
  "pass1": {
    "subsystem_shortlist": [{"subsystem":"ignition","score":0.78}],
    "candidate_pid_shortlist": ["RPM","STFT1","COOLANT_TEMP"],
    "freeze_frame_highlights": ["RPM=850", "STFT1=+12.5%"],
    "limitations": ["vehicle does not support FUEL_PRESSURE PID"]
  }
}
```
## 10) Expert model system design (LLM + RAG + tool-calling)
### 10.1 Responsibilities
•	Translate diagnostic engine outputs into actionable steps aligned to SOPs/manuals.
•	Ask for missing information (‘what to collect next’) when evidence is insufficient.
•	Generate a structured report that can be logged and reviewed.
•	Never overclaim: every recommendation must be traceable to evidence or explicitly marked as uncertain.
### 10.2 Output contract (non-negotiable)

The assistant must output strict JSON with these fields (schema versioned):

```json
{
  "schema_version": "1.0",
  "triage_level": "STOP|CHECK_SOON|MONITOR",
  "likely_subsystem": "string",
  "likely_faults": [{"name":"string","confidence":0.0}],
  "recommended_actions": [
    {"action":"string","why":"string","source":"doc_id#section"}
  ],
  "what_to_collect_next": ["string"],
  "limitations": ["string"],
  "citations": [{"doc":"string","section":"string"}]
}
```
### 10.3 RAG knowledge sources (pilot)
•	Maintenance SOPs / workshop checklists.
•	Vehicle manuals and manufacturer fault-code behaviors.
•	Internal fault label mapping guidelines (taxonomy mapping).
•	Sanitized historical maintenance report excerpts (text-only).
•	Do not ingest raw sensor streams into the RAG store.

#### 10.3.1 PDF image parsing pipeline

Real-world service manual PDFs contain critical diagnostic information embedded in images (exploded-part diagrams, torque specification tables, tool catalogs, procedure illustrations) that is invisible to the PDF text layer. The RAG ingestion pipeline includes an optional multi-stage image parsing pipeline to extract this information:

**Pipeline stages (per page with images):**
1. **Individual image extraction** — PyMuPDF extracts embedded images, filtering by minimum dimensions (50x50 px) and byte size (5 KB) to skip icons and decorative elements.
2. **OCR on images** (`--enable-ocr`) — easyocr (Traditional Chinese `ch_tra` + English, CPU-only) extracts text from each image. Results are categorised into structured fields: part numbers (`\d{5}-[A-Z0-9]{5,7}`), torque values (`N·m`, `kgf·m`, `lb·ft`), and dimensions (`mm`, `cm`). A token-overlap deduplication step (80% threshold, CJK-aware) skips OCR text already present in the PDF text layer. Non-redundant results are inserted as `[OCR, Page M]` blocks.
3. **Vision model description** (`--describe-images`) — Images are sent (with OCR text as context) to the local Ollama vision model (llava) for spatial/procedural descriptions, inserted as `[Image N, Page M]` blocks. An injection fence prevents the vision model from following instructions found in page text.
4. **Full-page rendering** (`--enable-page-render`) — Entire pages are rendered at 150 DPI (~0.8 MB/page) and processed through OCR and/or vision for spatial context that individual image extraction misses. Results are inserted as `[Full Page, Page M]` blocks.

**Merge order per page:** text layer → OCR blocks → image descriptions → full-page description.

**Image-aware chunking:** The chunker treats image blocks (marker line + description) as atomic units that are never split mid-description. The `has_image` field on `ChunkedSection` propagates to `metadata_json` (JSONB) in the `rag_chunks` table, enabling image-aware retrieval filtering.

**Vision model pre-flight:** Before processing any PDFs, the ingestion pipeline verifies the vision model is available via the Ollama `/api/tags` endpoint. If unavailable, image description is disabled gracefully and ingestion proceeds with text-only extraction.

**CJK translation** (`--enable-translation`): Chinese/Traditional Chinese section text and titles are translated to English via the local Ollama LLM (qwen3.5:9b) before chunking and embedding, ensuring uniform English in the vector store. Uses the Ollama `/api/chat` endpoint with `"think": false` to disable hidden reasoning tokens in Qwen3 thinking models (critical: without this flag, each translation generates ~2000 wasted tokens of internal reasoning, causing an 80x slowdown). Translation is concurrent with bounded parallelism (`asyncio.Semaphore`). Image marker blocks are preserved as-is (already English). Sections exceeding 8000 characters are skipped. Sections with fewer than 4 CJK characters are skipped.

**Modules:**
| Module | Role |
|--------|------|
| `app/rag/ocr.py` | easyocr wrapper with structured extraction + CJK-aware overlap dedup |
| `app/rag/pdf_parser.py` | `render_page_image()`, `has_tables_on_page()`, extended `extract_pdf_sections_async()` |
| `app/rag/vision.py` | `check_model_ready()` health check, image description via Ollama |
| `app/rag/translator.py` | Chinese→English translation via Ollama `/api/chat` (think disabled), concurrent batch processing |
| `app/rag/chunker.py` | Image-marker atomic blocks, `has_image` field on `ChunkedSection` |
| `app/rag/ingest.py` | Pre-flight vision check, `--enable-ocr` / `--enable-page-render` / `--enable-translation` CLI flags |
### 10.4 Workflow ('golden workflow')
1.	Start: inputs = vehicle_id, question, optional time_range.
2.	HTTP Request → diagnostic_api `/v2/obd/analyze` (submit raw OBD log) then `/v2/obd/{session_id}/diagnose` (stream AI diagnosis).
3.	Knowledge Retrieval query = question + predicted fault keywords + DTCs + subsystem.
4.	LLM generation (system prompt enforces: use only diagnostic_api output + retrieved docs; produce schema-valid JSON).
5.	Schema validation + citation checks; if invalid, retry with repair prompt; else return output + short summary.
## 11) Training and improvement pipeline (Phase 1 → 1.5 → 2)
### 11.1 Phase 1: Baseline (no fine-tuning)
Goal: prove the workflow, RAG grounding, tool-calling reliability, and strict JSON output contract before investing in training.
•	Lock the output JSON schema and enforce validation in the workflow.
•	Tune prompts, retrieval chunking, and citation rules until KPIs pass.
•	Implement interaction logging to create future training data (see 11.3).
### 11.2 Why Phase 1.5 exists (what fine-tuning should and should not do)
Fine-tuning primarily improves behavior (format discipline, safe tool use, consistent triage language, better clarification questions). It does not replace grounding; factuality still depends on diagnostic outputs + RAG sources.
### 11.3 Data to log in Phase 1 (mandatory for Phase 1.5/2)
•	User input: question, role, vehicle context flags, time_range.
•	diagnostic_api request/response (include evidence_ids and limitations).
•	OBD telemetry: snapshot_id(s) used, Pass‑1 outputs (subsystems + candidate PIDs + highlights), and supported PID list summary.
•	Retrieved chunks: doc_id, section, chunk_id, and snippet hash (for traceability).
•	Assistant output JSON + validation result; retry count; latency breakdown.
•	Human feedback: rating, correction, and ‘ground truth’ maintenance outcome if available.
### 11.4 Phase 1.5: LlamaFactory-based LoRA/SFT
Use LlamaFactory to run parameter-efficient fine-tuning (LoRA/QLoRA) on curated pilot interactions. Start with SFT only.
•	Primary training targets:
•	Schema adherence (JSON always valid; correct fields; stable enum usage).
•	Citation discipline (recommendations include sources or explicit ‘no source’).
•	Tool-use patterns (call diagnostic_api early; do not invent missing fields).
•	Clarifying questions policy (ask for evidence when confidence/limitations demand it).
Recommended training example structure (SFT):

**INPUT (user message content):**
- technician_question
- diagnostic_api_response (JSON)
- retrieved_evidence (top-k snippets with doc_id#section)

**OUTPUT (assistant):**
- target_expert_output_json (schema-valid)
- optional short summary (can be derived later; keep JSON as the supervised target)
### 11.5 Phase 2: Preference optimization + hardening
Once you have reliable ratings/corrections, build preference pairs (chosen vs rejected) and apply preference tuning (e.g., DPO/KTO/ORPO). Gate Phase 2 on a locked regression set and SME safety review.
•	Build preference dataset from: (baseline output, SME-corrected output) and/or A/B answers ranked by SMEs.
•	Add canary deployment + rollback; compare live KPIs with baseline.
•	Introduce drift detection: rising invalid JSON, missing citations, or changed question distribution.
### 11.6 Model serving and 'model swap' procedure
•	Serve baseline and tuned models behind OpenAI-compatible endpoints.
•	Keep the FastAPI workflow unchanged; switch the model provider base URL and model name.
•	Maintain a model registry: (model_id, base model, adapter, training data version, evaluation results, deployment date).
## 12) Infrastructure and compute (pilot)
### 12.1 Compute assets
Run diagnostic_api + obd-ui + vector store and the model server on a secured on-prem host. Choose inference hardware based on target latency and concurrency (GPU preferred for interactive use; CPU-only may be acceptable for low volume).
### 12.2 Networking
Minimum network controls:
•	Internal-only access (VPN or intranet).
•	TLS termination at reverse proxy (e.g., nginx) and RBAC at the app layer.
•	Outbound allow-list: only diagnostic_api, model endpoint, and internal doc store. Deny all other egress by default.
•	Separate subnets/VLANs for data stores (Postgres) vs app tier where feasible.
## 13) Security, privacy, and compliance
### 13.1 Data handling commitments
Honor the project’s privacy posture: restricted access, locked storage, and defined retention. The expert layer should avoid surfacing sensitive identifiers in prompts or logs. (Note: automated PII redaction removed for R&D prototype; re-introduce for production.)
### 13.2 Endpoint security for model-serving and tuning tools
•	Do not expose tuning or model-management endpoints to the public internet.
•	Pin versions and track upstream security advisories; run vulnerability scans as part of CI/CD.
•	Apply SSRF protections: allow-list outbound hosts; disallow 127.0.0.1 and metadata IP ranges; restrict DNS rebinding.
•	Treat uploaded training data as sensitive; enforce access controls and audit logs.
### 13.3 API authentication
•	All `/v2/*` endpoints require a Bearer JWT token via `Authorization` header.
•	Tokens issued by `POST /auth/login` (HS256, 24-hour expiry, `sub` = username).
•	Registration via `POST /auth/register` (username: 3-50 chars alphanumeric/underscore/hyphen; password: 8-128 chars, bcrypt-hashed).
•	`get_current_user` FastAPI dependency decodes JWT, verifies user exists and is active; returns 401 otherwise.
•	Health (`GET /health`) and docs (`/docs`, `/redoc`) remain public.
•	Per-user session isolation: `OBDAnalysisSession.user_id` FK with `UniqueConstraint(user_id, input_text_hash)`. `_get_owned_session` returns 404 (not 403) to prevent session-ID enumeration.
## 14) Observability and monitoring
### 14.1 What to log (mandatory)
•	diagnostic_api requests/responses.
•	retrieval results (doc IDs, chunk IDs, similarity scores).
•	LLM output JSON + schema validation result + citation check result.
•	latency breakdown (API call / retrieval / generation / retries).
•	SME review tags and corrections.
### 14.2 Drift detection and rollback
•	Monitor distribution shift in question types and subsystems.
•	Detect rising invalid JSON or missing citations.
•	Roll back to last stable model if KPIs regress.
## 15) Testing plan (engineering checklist)
### 15.1 Unit tests
•	Preprocessing validators (drop duplicates/incomplete/outliers).
•	diagnostic_api schema validation (requests/responses).
•	JSON schema validation for model outputs.
### 15.2 Integration tests
•	End-to-end FastAPI workflow with mocked diagnostic_api and fixed retrieval set.
•	Network allow-list tests (only allowed targets reachable).
•	Model endpoint contract tests (OpenAI-style chat completion).
### 15.3 SME acceptance tests
•	Curated set of ‘gold’ incidents.
•	Acceptance rubric: actionable, SOP-aligned, no unsafe advice, limitations stated, citations present.
## 16) Implementation plan (work breakdown) and milestones
### 16.1 Repo layout (recommended)
•	/infra/ (docker compose, env templates, network policy)
•	/diagnostic_api/ (FastAPI app + schemas)
•	/rag/ (ingestion scripts, chunking config, doc registry, OCR module, PDF image parsing)
•	/expert_model/ (prompts, JSON schemas, validators)
•	/training/ (dataset builder, LlamaFactory configs, LoRA scripts)
•	/eval/ (offline eval harness, regression suite)
•	/docs/ (this design doc + API contract + schemas)
•	/obd_agent/ (edge collector service; reads ELM327 and posts OBDSnapshot)
•	/obd-ui/ (Next.js expert diagnostic web UI; port 3001; shadcn/ui + recharts)
•	/pass1/ (rules + tables: dtc_family→subsystem, symptom→subsystem, subsystem→PID priority)
### 16.2 Milestones (phase-gated)

| Milestone | Exit criteria |
|-----------|---------------|
| M0 | Schemas finalized (diagnostic_api + expert output JSON v1.0) |
| M1 | FastAPI workflow works with stub backend; schema validation + citations checks wired |
| M2 | diagnostic_api integrated with real diagnostic outputs (LLM-safe summaries) |
| M2.1 | OBD Agent posts snapshots; diagnostic_api stores OBDSnapshot + exposes latest lookup; Pass‑1 mapper returns subsystem+PID shortlist |
| M3 | RAG ingestion complete; doc_id/section anchors stable; citation coverage passes. Text extraction done (APP‑03, 2026-02-28); PDF image parsing done (APP‑22, 2026-03-01): OCR + vision + page render + image-aware chunking. |
| M4 | Phase 1 pilot run + SME evaluation; logging pipeline producing case packages |
| M5 | Phase 1.5: LoRA/SFT via LlamaFactory + offline regression suite; deploy tuned model behind OpenAI endpoint |
| M5.1 | OBD Expert Diagnostic Web UI: obd-ui serves on :3001; `/v2/obd/*` endpoints persist sessions + collect feedback; Docker service integrated |
| M6 | Phase 2: preference tuning + canary + drift/rollback + security review |
## 17) Open questions / TBD (must resolve early)
•	OBD Agent deployment model: host daemon vs container with /dev passthrough; Bluetooth vs USB; offline buffering behavior.
•	Licensing boundary decision for python‑OBD (GPL) and whether the agent ships as a separate artifact/service.
•	Pass‑1 taxonomy: define subsystem names (8 vs 17 vs 33 mapping) and PID shortlist table ownership (who curates + approves changes).
•	Label taxonomy for the pilot: 8 vs 17 vs 33 (and how to map between them).
•	Final dataset volume for extension vehicles (application vs deck mismatches).
•	Base LLM choice (language requirements, context length, latency on available GPUs).
•	Evidence requirements per recommendation (strict citations vs allow diagnostic output-only actions).
•	PII redaction policy for maintenance logs used in RAG/training (deferred; not implemented in R&D prototype).
•	Who signs off on SME acceptance and safety review.
•	Phase 1.5/2 serving choice: stay on Ollama with adapters vs move to vLLM/SGLang for tuned weights.
## 18) Appendices
### Appendix A — Phase 1.5 LlamaFactory integration checklist (practical)

Use this checklist to keep Phase 1.5 contained and predictable.
•	Freeze Phase 1 interfaces: output JSON schema v1.0, diagnostic_api contract, and doc_id/section anchors.
•	Export Phase 1 logs weekly into an immutable ‘training snapshot’ (versioned by date).
•	Strip sensitive data from logs before any training step; keep raw logs in restricted storage. (Automated PII redaction deferred for R&D prototype.)
•	Build SFT dataset: (question + diagnostic_api JSON + top-k retrieved snippets) → (gold JSON output).
•	Start LoRA/QLoRA with conservative settings (small rank, short training, early stopping); keep a baseline model for comparison.
•	Run offline regression suite (format/citation/tool-use checks) before any deployment.
•	Deploy tuned model behind OpenAI-compatible endpoint (prefer vLLM/SGLang for server use); keep baseline available for rollback.
•	Canary: route a small % of pilot traffic to tuned model; compare KPIs; rollback if regressions appear.
### Appendix B — Suggested dataset formats (SFT + preference)

**SFT example (single-turn):**

```json
{
  "id": "case_000123",
  "messages": [
    {"role": "system", "content": "<your system policy prompt>"},
    {"role": "user", "content": "<question>\n\n<diagnostic_api JSON>\n\n<retrieved snippets with doc_id#section>"},
    {"role": "assistant", "content": "<target schema-valid JSON>"}
  ]
}
```

**Preference example (chosen vs rejected) for Phase 2:**

```json
{
  "id": "pref_000123",
  "prompt": "<same user content as SFT>",
  "chosen": "<SME-approved schema-valid JSON>",
  "rejected": "<baseline output JSON (or unsafe/incorrect variant)>"
}
```
### Appendix C — Deployment notes (Ollama vs vLLM/SGLang)
•	If Phase 1 uses Ollama, keep diagnostic_api configured against its OpenAI-compatible base URL. This makes Phase 1.5 a model swap, not a workflow rewrite.
•	For Phase 1.5/2, serving tuned HF weights via vLLM/SGLang typically simplifies server inference and avoids extra conversion steps.
•	If you must stay on Ollama, prefer adapter-based workflows (LoRA adapters) and treat quantization/export steps as a separate risk item with its own validation.
— End of document —
