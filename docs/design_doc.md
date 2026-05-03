# Pilot Expert Model Training Pipeline (LLM + RAG + Tooling) for AI-Assisted Vehicle Predictive Diagnosis

**Revised to include Phase 1 → Phase 1.5 → Phase 2 plan (incl. LlamaFactory)**

## Document control

| Field | Value |
|-------|-------|
| **Doc title** | Pilot Expert Model Training Pipeline (LLM + RAG + Tooling) for Vehicle Predictive Diagnosis |
| **Project** | AI-assisted vehicle self-diagnosis + fleet management (edge + cloud) |
| **Status** | Draft v4.5 (Generalized DTC Index for Manuals) |
| **Owner** | (You / ML Lead) |
| **Contributors** | ML engineers; data engineers; backend engineers; DevOps; security reviewer; workshop/technician SMEs |
| **Last updated** | 2026-05-02 (v5.1) |
| **Primary pilot stack** | FastAPI (diagnostic_api) + Ollama (`qwen3.5:27b-q8_0`) + Next.js (obd-ui) + pgvector (PostgreSQL) |
| **New in this revision** | Ingestion-quality fixes uncovered by the first end-to-end upload. APP-48: `parse_document` strips leading YAML frontmatter before heading extraction (no more junk first chunk) and uses frontmatter's `vehicle_model` as a high-priority fallback. APP-49: `_run_marker_convert` passes `original_filename` and `vehicle_model_override` to the worker; `_resolve_vehicle_model` skips UUID stems and only uses cleaned-stem fallback when the candidate plausibly looks like a model. User-supplied vehicle-model labels survive the conversion round-trip. APP-46 follow-up: tqdm hook patches all five tqdm namespaces (was silently no-op'ing because marker imports via `tqdm.auto`); throttle lowered to 0.3 s. APP-47a: new `manuals.warnings JSONB` column (Alembic `t4u5v6w7x8y9`); a `logging.Handler` captures marker's silent LLM-fallback events (malformed-JSON, low rewrite scores) and persists them so the UI can warn that some pages may have degraded extraction. |

### Revision history

| Version | Date | Summary |
|---------|------|---------|
| v5.1 | 2026-05-02 | APP-50: fix `StringDataRightTruncation` on long Chinese service-manual ingestion.  Marker emits HTML page-anchor `<span>` tags inside markdown headings; one MWS-150-A heading exceeded the 500-char `section_title` cap, blowing up the chunk-batch INSERT.  Alembic `v6w7x8y9z0a1` widens `rag_chunks.section_title` to TEXT.  `parser._clean_section_title` strips empty `<span ...></span>` anchors and caps at 2000 chars.  Recovered the failed manual row via the existing reingest endpoint without re-running marker-pdf (47 min of conversion work preserved). |
| v5.0 | 2026-05-02 | APP-47b: stage-aware progress display.  The marker-pdf pipeline runs sequential stages (Layout → OCR → Recognition → LLM section header → LLM page correction → Table rewrite), each with its own `tqdm` bar over its own item count.  Without a stage label, the UI showed `283/434` then jumped to `526/555` between stages, looking like a regression.  New `manuals.pages_phase VARCHAR(50)` column (Alembic `u5v6w7x8y9z0`).  Worker's tqdm hook reads `bar.desc` and passes it as `phase` in `progress.json`.  `_ProgressReporter` bypasses the throttle on phase change so a stage transition (where `processed` resets to a low number) still triggers an immediate write.  API-side `_sync_progress_to_db` tracks both `last_processed` and `last_phase`, returns `(processed, phase)`.  `Manual.pages_phase` is cleared post-conversion so the UI doesn't show a stale stage during chunking/embedding.  Frontend renders `{phase} {processed}/{total}` (e.g. "OCR 283/434"). |
| v4.9 | 2026-05-02 | Ingestion-quality fixes uncovered after the first end-to-end upload exposed four data-quality regressions silently coexisting with `status='ingested'`. **APP-48**: marker-emitted YAML frontmatter was being treated as the document's first section — the leading `---...---\n` block is now stripped by `parse_document`/`parse_manual`/`parse_log` before heading extraction. Frontmatter `vehicle_model:` is also consulted as a high-priority fallback when body regex fails to match an OEM pattern. **APP-49**: the on-disk PDF filename is `{uuid}.pdf` (set by the upload pipeline), so marker's filename-based vehicle-model fallback was returning the UUID-with-spaces. `manual_pipeline._run_marker_convert` now passes both `original_filename` (the user's upload name) and `vehicle_model_override` (the user's form input) in the request JSON; `marker_convert._resolve_vehicle_model` honours the override, detects UUID stems and returns `Generic`, and only falls back to the cleaned-stem heuristic when the candidate contains a digit (vehicle models almost always do). `_build_frontmatter` writes `source_pdf` from the original filename. `run_conversion_and_ingestion` no longer overwrites a user-supplied `manual.vehicle_model` — the user's label survives. **APP-46 follow-up**: the tqdm hook only patched `tqdm.tqdm.update`, but marker imports via `from tqdm.auto import tqdm`; the patch silently produced zero progress writes. Hook now patches all known namespaces (`tqdm`, `tqdm.std`, `tqdm.auto`, `tqdm.notebook`, `tqdm.asyncio`) deduplicating by class identity. Throttle lowered from 1.5s to 0.3s so short bars on small PDFs still produce observable writes. **APP-47a**: silent LLM fallbacks (qwen3.5-flash returning malformed JSON, marker dropping back to non-LLM extraction) were undetectable. New `manuals.warnings JSONB` column (Alembic `t4u5v6w7x8y9`). A `logging.Handler` installed on the `marker` logger during conversion captures `OpenAI inference failed`, `did not return a valid response`, and `Table rewriting low score` records into a structured event list. Events ride in `result.json["warnings"]` and are persisted by the API. Frontend renders an amber `⚠ N` badge alongside the status pill on rows with non-empty warnings (i18n EN / zh-CN / zh-TW). |
| v4.8 | 2026-05-02 | APP-46: Per-page progress reporting during marker-pdf conversion. Adds `manuals.pages_processed` / `pages_total` columns (Alembic `s3t4u5v6w7x8`). The host worker installs a `tqdm.update` monkey-patch that mirrors each tick to `{manual_id}.progress.json` (atomic write to `.tmp` then `os.replace`; throttled to one write per page or per 1.5 s, whichever is longer). The API's `_run_marker_convert` polling loop reads the progress file on every tick and persists it via a short-lived `SessionLocal` (so a transient DB error during a progress write doesn't poison the long-running conversion task). `ManualSummary` and `ManualStatusResponse` expose `pages_processed` / `pages_total`. The frontend Service Manual Library shows `{processed}/{total}` instead of the bare "Converting…" pill, and renders dedicated `Chunking…` / `Embedding…` pills for the subsequent stages. Polling timer extended to also tick during chunking/embedding. i18n strings added (EN / zh-CN / zh-TW). |
| v4.7 | 2026-05-02 | APP-45 follow-up: drop the 30-min wall-clock timeout from `_run_marker_convert`. Was too aggressive for LLM-assisted marker-pdf on large multilingual manuals — would mark a manual `failed` while the worker was still progressing. Polling is now unbounded; cancellation happens via diagnostic-api container restart. `MARKER_TIMEOUT_SECONDS` env var and `marker_timeout_seconds` setting removed. |
| v4.6 | 2026-05-02 | APP-45: Redesign RAG ingestion as a single marker-pdf path. Removed CLI ingest entry point and the dual PDF parser stack — `app/rag/pdf_parser.py`, `ocr.py`, `vision.py`, `translator.py`, `md_export.py`, plus `scripts/rebuild_dtc_appendix.py` are deleted. Marker-pdf is now the only converter, with LLM-assisted mode always on (API refuses to boot if `PREMIUM_LLM_API_KEY` is missing). Pipeline: PDF upload → marker-pdf → structured `.md` → chunk → embed → pgvector. Five-stage status state machine (`uploading`/`converting`/`chunking`/`embedding`/`ingested`) for UI observability. New `POST /v2/manuals/{id}/reingest` endpoint re-runs chunk + embed from existing markdown without re-conversion (atomic delete-then-insert; 409 if `md_file_path IS NULL` or status is in-flight). Schema: `rag_chunks.manual_id UUID` FK with `ON DELETE CASCADE`, `CHECK (source_type = 'manual')`, expanded `Manual.status` CHECK. Alembic `r2s3t4u5v6w7`. Dropped PyMuPDF / easyocr / Pillow from `requirements.txt`. Kept `cjk_utils.py` (used by chunker). 8 obsolete test files removed. |
| v4.5 | 2026-05-02 | APP-44: Generalize DTC regex in marker-pdf converter (`diagnostic_api/scripts/marker_convert.py`). Widened `_DTC_RE` from `\b[PBCU]\d{4}\b` to a SAE J2012 / ISO 15031-6 / UDS-aware pattern that captures classic 5-char codes, manufacturer-specific hex variants (e.g. `P062F`, `B1A23`), 6/7-char extended codes, and FTB / sub-byte suffixes (`P0420-64`, `B1A21:08`, `C0561 87`), case-insensitive. Lookarounds replace `\b` so dash-separated codes are not split. New `_normalize_dtc()` canonicalizes to uppercase + dash-separated form for stable dedup. New `diagnostic_api/scripts/rebuild_dtc_appendix.py` helper re-applies the appendix logic to existing markdown files (single file or recursive directory) without re-running marker-pdf, supporting `--dry-run`. Verified on MWS150-A Yamaha service manual: surfaces previously-missed `P062F` (7 occurrences) and zero regressions on the 21 codes already indexed. |
| v4.4 | 2026-04-10 | V2 harness architecture design initiated (GitHub Issue #26). New `docs/v2_design_doc.md` and `docs/v2_dev_plan.md` define agent-driven diagnosis via harness loop, tool registry, session event log, graduated autonomy. V1 diagnosis orchestration (§10.4) preserved as fast-path fallback; V2 adds parallel agent endpoints. Shared components (infra, auth, RAG, OBD pipeline) remain in this document. |
| v4.3 | 2026-04-01 | Fix garbled symbols from custom PDF font encoding (GitHub Issue #44). New `_is_symbol_font()` skips known symbol/icon font spans (ZapfDingbats, Wingdings, etc.) during text extraction. New `_is_garbled_line()` heuristic detects short lines with no Unicode letters that aren't pure numbers. New `_clean_extracted_text()` post-processing removes garbled lines and normalizes safety labels ("3DANGER" → "DANGER"). New "garbled" classification in `_classify_line()` excludes garbled lines from section body. Applied in `extract_text_from_pdf()`, `extract_text_from_pdf_async()`, and `_fallback_page_sections()`. 24 new tests, 1 updated test (368 total). |
| v4.2 | 2026-03-31 | Vehicle model detection fix (GitHub Issue #43). New `_clean_filename_stem()` helper strips common manual suffixes (`Owners_Manual`, `Service_Manual`, `Workshop_Manual`, etc.) and normalises separators to spaces — `2016_Jazz_Owners_Manual` → `2016 Jazz`. `_resolve_vehicle_model()` priority chain updated: (1) `--vehicle-model` CLI override, (2) section metadata, (3) domain regex, (4) cleaned filename stem (was: raw stem). New `--vehicle-model` CLI flag on `md_export.py`. `_yaml_escape` hardened against newline injection in user-supplied values. 17 new tests (73 total in `test_md_export.py`). |
| v4.1 | 2026-03-31 | Static markdown manual viewer (APP-43, GitHub Issue #48). New `infra/nginx/manuals/index.html` single-page viewer at `/manuals/` with client-side markdown rendering via `marked.js`. Sidebar auto-discovers `.md` files via Nginx `autoindex`. YAML frontmatter displayed as metadata banner. Image paths rewritten for Nginx serving. New `diagnostic_api_manuals` named volume shared between diagnostic-api and nginx. Two Nginx location blocks added. Responsive CSS. No new containers — only Nginx restart required. |
| v4.0 | 2026-03-31 | PDF parser quality fixes (APP-42, GitHub Issues #41, #42). **Section extraction** (`_classify_line()` in `pdf_parser.py`): added `_STANDALONE_PAGE_NUM` filter for standalone digit-only lines misclassified as headings, `_BREADCRUMB_PATTERN` filter for Honda-style `uu...u` navigation headers, and `_HAS_LETTER_RE` Unicode-safe alphabetic guard preventing pure symbols/digits from becoming headings. Updated `_fallback_page_sections()` with same filters. E2E results on Honda Jazz 597-page PDF: sections 1,385→883 (-36%), garbage page-number headings eliminated, breadcrumb noise removed, `###` sub-heading hierarchy restored (0→311). **Image extraction** (`extract_images_from_page()`): changed CMYK detection from `pix.n > 4` to `pix.colorspace.n not in (1, 3)` — fixes confusion between CMYK without alpha (`pix.n=4`, `colorspace.n=4`) and RGBA (`pix.n=4`, `colorspace.n=3`). Added try/except fallback converting to RGB for Separation/DeviceN colorspaces that report `colorspace.n=1` but fail `tobytes("png")`. E2E results: images 8→1,015, extraction warnings ~800→0. 14 new section tests, 2 new image tests. All existing Yamaha MWS150-A tests pass. Filed follow-up issues: #43 (vehicle model fallback), #44 (garbled font symbols), #45 (TOC-based structure), #46 (cross-page merging), #47 (bullet-prefix stripping), #48 (static manual viewer). |
| v3.8 | 2026-03-30 | Phase 1b PDF-to-markdown converter (GitHub Issue #34, parent #32): New `app/rag/md_export.py` CLI module converts PDF service manuals to structured `.md` files per schema from #33. Reuses `extract_pdf_sections_async` (section extraction), `extract_images_from_page` (image saving), `translate_sections` (Chinese→English), and vision service (image descriptions). Produces YAML frontmatter, heading hierarchy with `<!-- page:N -->` markers, PNG images in `images/{stem}/` subdirectory, optional vision descriptions, and DTC cross-reference index appendix. CLI: `python -m app.rag.md_export --dir ... --output ... [--describe-images] [--enable-ocr] [--enable-translation]`. 41 new tests. Phase 1b of §10.3.2 marked DONE. |
| v3.7 | 2026-03-30 | Structured markdown manual schema (APP-40, GitHub Issue #33, parent #32): Schema spec (`docs/manual_markdown_schema.md`) defines file format for storing service manuals as structured `.md` files for agent-navigated retrieval. YAML frontmatter (`source_pdf`, `vehicle_model`, `language`, `page_count`, `section_count`), heading hierarchy (`#`→`####`), deterministic section anchor slugs, DTC subsections (`#### DTC: P0171 — Description`), image references with vision descriptions, page markers (`<!-- page:N -->`), and optional DTC cross-reference index appendix. Reference example at `docs/examples/manual_example.md`. New section 10.3.2 in design doc describes rationale and implementation phases. Compatibility mapping to existing `RagChunk` columns documented. Documentation only — no code changes. |
| v3.6 | 2026-03-28 | Flexible OBD log ingestion (APP-39, GitHub Issue #30): New `obd_agent/format_normalizer.py` preprocessing layer that auto-detects incoming file format and normalizes to internal TSV before the diagnostic pipeline. Supports 4 formats: native TSV (pass-through), OBDWIZ CSVLog (39 Chinese→English column mappings, 6 imperial→metric unit converters, Chinese AM/PM timestamp parsing, consecutive row deduplication), obd_maxlog (unit-suffix stripping from headers, `#`-metadata preservation, non-standard column filtering, millisecond truncation), and generic CSV (delimiter conversion + timestamp normalization). Integrated into `_run_pipeline()` with automatic normalized temp file cleanup. Frontend `FileDropZone.tsx` now accepts `.csv` file uploads. 2 test fixture files (`csvlog_sample.csv`, `maxlog_sample.csv`). 36 new tests. |
| v3.5 | 2026-03-25 | Permanent Cloudflare Tunnel (DO-08, GitHub Issue #24): Named tunnel `stf-diagnosis` on `stf-diagnosis.dev` replaces temporary quick tunnel (`trycloudflare.com`). Tunnel config at `~/.cloudflared/config.yml`, credentials at `~/.cloudflared/<TUNNEL_ID>.json`. DNS CNAME routes domain to tunnel. Systemd user service (`cloudflared.service`) with `Restart=on-failure` and `loginctl enable-linger` for persistence across reboots and logouts. Deployment guide updated with tunnel architecture diagram, management commands, and troubleshooting. README live demo URL updated. |
| v3.4 | 2026-03-24 | Region-blocked model handling (APP-38, GitHub Issue #23): `model_availability.py` probes curated models for 403 (PermissionDeniedError) and caches results with 1-hour TTL. `GET /v2/obd/premium/models` returns `{models, default, blocked}` filtered by availability. `POST /v2/obd/{session_id}/diagnose/premium` implements fallback loop — retries next available model on 403, emits structured SSE error events with `error_code`. Frontend shows localized region-specific error. Curated list expanded with 6 HK-accessible models (MiniMax m2.7/m2.5, GLM glm-5/glm-4.7, Kimi k2.5/k2). Regional restrictions documented in `.env.polyu.example`. 7 new tests. |
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
•	Model server (Phase 1): Ollama (OpenAI-compatible endpoints). Default model: `qwen3.5:27b-q8_0` (dense 27B, Q8 quantization, ~30 GB VRAM). Qwen3.5 is a thinking model — internal reasoning tokens are streamed as SSE keep-alive comments during diagnosis to prevent proxy idle timeouts. `OLLAMA_KEEP_ALIVE=-1` keeps the model loaded in VRAM permanently. `AsyncOpenAI` timeout set to 300s for cold-load tolerance.
•	Model server (Phase 1.5/2): tuned model served via vLLM/SGLang (OpenAI-compatible), or Ollama with adapters (if chosen).
•	Vector store: pgvector (PostgreSQL extension) for SOP/manual chunks and sanitized knowledge. Chunk metadata includes `has_image` flag and `metadata_json` (JSONB) for image-containing chunks.
•	Postgres: session persistence, diagnosis history, feedback tables, and OBD snapshot storage.
•	OBD Agent (edge collector): a separate service/daemon (python‑OBD or equivalent) that reads ELM327 OBD‑II and posts sanitized OBDSnapshot telemetry to diagnostic_api.
•	**OBD Expert Diagnostic Web UI (`obd-ui`)**: Next.js 15 (TypeScript, Tailwind CSS, shadcn/ui, recharts) on port 3001. Provides experts with a visual interface to submit OBD logs, view analysis results across five tabs (Summary, Detailed, RAG, AI Diagnosis, History), and submit structured feedback per tab (up to 10 submissions per tab per session). Session dashboard (`/sessions`) lists all past analysis sessions with status filter, pagination, and diagnosis indicators (`has_diagnosis`, `has_premium_diagnosis`). History tab displays all past AI diagnosis generations with provider badge, model name, timestamp, and expandable text. RAG tab displays retrieved context; AI Diagnosis tab contains Local LLM / Cloud LLM (OpenRouter) sub-tabs for side-by-side comparison — local streams via SSE from Ollama, premium streams via SSE from OpenRouter (opt-in, multi-model). Premium sub-tab includes a model selector dropdown populated from admin-curated list. Communicates with diagnostic_api via `/v2/obd/*` endpoints. Runs as a standalone Docker service.
•	**Manual Viewer**: Static single-page HTML app served by Nginx at `/manuals/`. Client-side markdown rendering via `marked.js` (CDN). Auto-discovers converted service manual `.md` files from the shared `diagnostic_api_manuals` volume via Nginx `autoindex`. No backend required — pure static serving.
•	**Premium LLM client (opt-in)**: `PremiumLLMClient` using OpenAI Python SDK (`AsyncOpenAI`) pointing at **OpenRouter** (`base_url=https://openrouter.ai/api/v1`) for cloud-based diagnosis. Supports any model available on OpenRouter; admin-curated model list configured via `PREMIUM_LLM_CURATED_MODELS` env var (16 models across 7 providers). Feature-gated (`PREMIUM_LLM_ENABLED=false` by default). The only component that requires internet access. Uses the same prompts and RAG context as the local Ollama client. **Region-block handling** (`model_availability.py`): probes each curated model with a minimal 1-token completion on first `/premium/models` request, caching results for 1 hour. Models returning `PermissionDeniedError` (HTTP 403) are marked blocked and filtered from the model list. The diagnosis endpoint implements a fallback loop — if the selected model returns 403, it marks it blocked, notifies the user via SSE status event, and retries with the next available model (up to 3 attempts). SSE error events are structured JSON with `error_code` field for frontend differentiation.
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

**PolyU server deployment (Podman):** The PolyU HK GPU server (2x RTX 6000 Ada, 92 GB VRAM) uses Podman instead of Docker for rootless multi-user access. GPU passthrough uses CDI (Container Device Interface) via `devices: ["nvidia.com/gpu=all"]` in the Podman compose override (`infra/docker-compose.polyu.yml`). Nginx is deployed as a container service on port 8080, proxying all external traffic to the frontend and API. SSE streaming endpoints have `proxy_buffering off` for real-time token delivery. Public access is provided via a permanent Cloudflare Tunnel (`stf-diagnosis.dev`) that routes HTTPS traffic to Nginx on `127.0.0.1:8080`. The tunnel runs as a systemd user service with auto-restart and linger. See `docs/deployment_polyu.md` for full setup instructions.
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
- **SSE-streaming AI diagnosis** powered by Ollama (local LLM, `qwen3.5:27b-q8_0`)
- Streams diagnostic text tokens to the client in real time via Server-Sent Events
- Qwen3.5 thinking mode enabled: during the internal reasoning phase (~1-2 min), SSE comments (`: thinking\n\n`) are streamed to keep the connection alive through Cloudflare Tunnel and Nginx proxies. A `status` event updates the UI to show "AI is reasoning..." (localized). Thinking tokens are detected via `delta.model_extra["reasoning"]` from the Ollama OpenAI-compatible API
- Stores the final `diagnosis_text` on the session row upon completion
- Appends a row to `diagnosis_history` table (provider="local")
- Returns 404 if session not found

**Endpoint:** `POST /v2/obd/{session_id}/diagnose/premium`
- **SSE-streaming AI diagnosis** via premium cloud LLM (OpenRouter, multi-model)
- Accepts optional `model` query param (e.g., `?model=openai/gpt-5.2`); validated against admin-curated list (400 if not in list); defaults to `PREMIUM_LLM_MODEL`
- Feature-gated: returns 403 if `PREMIUM_LLM_ENABLED=false`; returns 503 if API key is missing
- SSE event types: `token` (string), `done`/`cached` (JSON with `text`, `diagnosis_history_id`, `model_used`), `error` (JSON with `message`, `error_code`), `status` (string)
- **Fallback on region-block:** if the selected model returns HTTP 403, marks it blocked, emits SSE `status` event ("Model X is not available in your region. Trying next model..."), and retries with the next available model (up to 3 attempts). If all attempts fail, emits `error` event with `error_code: "all_models_blocked"`
- Stores `premium_diagnosis_text` and `premium_diagnosis_model` on the session row upon completion
- Appends a row to `diagnosis_history` table (provider="premium")
- Independent from local diagnosis — both can exist simultaneously on the same session
- Uses the same prompts and RAG context as the local endpoint

**Endpoint:** `GET /v2/obd/premium/models`
- Returns `{models: [...], default: "...", blocked: [...]}` — `models` contains only regionally-available models; `blocked` lists region-restricted models from curated list
- Triggers a lazy availability probe on first call (and when cache >1 hour old): sends minimal completion to each curated model, marks 403 responses as blocked
- If configured default model is blocked, falls back to first available model
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

#### 10.3.1 RAG ingestion pipeline (single-path, marker-pdf)

Manuals enter pgvector through one entry point: a PDF upload to `POST /v2/manuals/upload` triggers a background task that runs the full pipeline end-to-end. The previous CLI ingest script and dual PDF parser (PyMuPDF + bespoke OCR/vision/translation modules) were removed in APP-45; marker-pdf is now the only converter and produces the structured markdown that feeds both the vector store and the manual viewer.

**Five-stage state machine** (visible on `Manual.status` for UI polling):

```
uploading → converting → chunking → embedding → ingested
                                   └→ failed
```

| Stage | Trigger | Work | Failure mode |
|-------|---------|------|--------------|
| `uploading` | upload endpoint | save PDF to `uploads/{uuid}.pdf`, dedup by SHA-256 hash | 409 on duplicate, 413 on oversize |
| `converting` | bg task | request marker-pdf via filesystem `.queue/`, host-side worker (`scripts/marker_worker.py`) runs marker-pdf with LLM-assisted mode always on, writes `.md` + images.  Worker also writes `{id}.progress.json` (`{processed, total, phase}`) on each tqdm tick so the API can update `manuals.pages_processed` / `pages_total` for UI per-page progress.  Polling is unbounded — no API-side timeout (large LLM-assisted conversions can exceed an hour). | marker error in `result.json`, container restart |
| `chunking` | bg task | parse `.md` by markdown headings (`parser.parse_document`), split with `chunker.chunk_sections` | parse error, empty file |
| `embedding` | bg task | for each chunk: SHA-256 dedup check, Ollama embed, insert `RagChunk` row with `manual_id` FK | embedding-service failure (rollback retains old chunks) |
| `ingested` | terminal | `chunk_count` populated; manual is queryable via `/v1/rag/retrieve` | — |

**Always-on LLM-assisted conversion.** Marker-pdf runs with `use_llm=True` and the API refuses to boot if `PREMIUM_LLM_API_KEY` is missing. Vision descriptions and CJK normalisation that previously lived in bespoke modules are now produced by marker-pdf itself.

**Reingest endpoint** (`POST /v2/manuals/{id}/reingest`). Re-chunks and re-embeds from the existing `.md` without re-running marker-pdf. Used to recover manuals whose Stage 2/3 silently produced zero chunks, or to re-embed everything after the embedding model is changed. Atomicity: `BEGIN; DELETE rag_chunks WHERE manual_id = ?; <re-insert>; COMMIT`. A failure mid-embed rolls back to the prior chunks. Returns 409 if `md_file_path IS NULL` (re-upload the PDF instead) or if status is in-flight.

**One artefact, three consumers.** The structured `.md` produced by marker-pdf is the single source of truth:

| Consumer | Reads | Path |
|----------|-------|------|
| Vector RAG (`/v1/rag/retrieve`) | `rag_chunks` table | populated by Stage 3 |
| Static manual viewer | `.md` from disk | served by Nginx at `/manuals/` |
| Harness manual tools (`get_manual_toc`, `read_manual_section`, `search_manual`) | `.md` from disk | reads at request time, no embedding |

**Schema**:
- `rag_chunks.manual_id` UUID FK → `manuals.id`, `ON DELETE CASCADE` (deleting a manual auto-removes its chunks).
- `rag_chunks.source_type` CHECK constraint locked to `'manual'`. Future ingestion sources (logs, past sessions) require a migration that relaxes the constraint.
- Chunk checksum (`SHA-256(doc_id + section_title + text)`) provides per-chunk idempotency for the first ingestion path.

**Modules:**
| Module | Role |
|--------|------|
| `scripts/marker_convert.py` (host) | marker-pdf wrapper with LLM-assisted mode, image path rewrite, DTC index appendix builder |
| `scripts/marker_worker.py` (host) | watches `.queue/` for conversion requests, runs marker-pdf, writes results |
| `app/services/manual_pipeline.py` | `run_conversion_and_ingestion`, `run_reingestion`, status state machine, orphan-file cleanup |
| `app/rag/ingest.py` | `parse_and_chunk_md` (CPU phase), `embed_and_insert_chunks` (network phase) — split so the pipeline can mark distinct status transitions |
| `app/rag/chunker.py` | section-aware chunker, atomic markdown image blocks (`![alt](path)`), CJK fallback splitting via jieba |
| `app/rag/parser.py` | markdown heading parser (`parse_document`), section extraction |
| `app/api/v2/endpoints/manuals.py` | upload, list, get, delete, status, reingest endpoints |

#### 10.3.2 Structured markdown manuals (alternative retrieval path)

An alternative to vector-chunk retrieval: store service manuals as well-structured `.md` files and let an agentic LLM navigate them with tools (`list_manuals`, `list_sections`, `read_section`, `search_manual`) instead of relying on embedding similarity (GitHub Issues #32, #33).

**Rationale:** Chunking destroys the hierarchical structure that makes service manuals useful. A mechanic navigates by system -> subsystem -> procedure, not by embedding distance. At the current corpus scale (a handful of manuals), agent-navigated structured documents provide more precise retrieval with simpler infrastructure.

**Schema (v1.0, `docs/manual_markdown_schema.md`):**
- One `.md` file per source PDF, stored in `/app/data/manuals/`
- YAML frontmatter: `source_pdf`, `vehicle_model`, `language`, `translated`, `exported_at`, `page_count`, `section_count`
- Heading hierarchy: `#` (doc title) -> `##` (chapter) -> `###` (section) -> `####` (subsection/DTC)
- Deterministic section slug anchors for stable `read_section` tool references
- DTC subsections: `#### DTC: P0171 — Description` format
- Images: `![alt](images/{stem}/p{page}-{index}.png)` with vision descriptions
- Page markers: `<!-- page:N -->` HTML comments for PDF page traceability
- Optional DTC cross-reference index appendix. Appendix builder (`diagnostic_api/scripts/marker_convert.py::_build_dtc_index`) uses a SAE J2012 / ISO 15031-6 / UDS-aware regex that captures classic 5-char codes, manufacturer-specific hex variants (e.g. `P062F`, `B1A23`), 6/7-char extended codes, and FTB / sub-byte suffixes (`P0420-64`, `B1A21:08`). Codes are normalized to uppercase + dash-separated form for stable dedup across OEMs and languages. Existing converted manuals can be re-indexed in place (no LLM re-billing) via `diagnostic_api/scripts/rebuild_dtc_appendix.py`.

**Coexistence with vector RAG:** Both consumers read the same `.md` artefact (see §10.3.1 "One artefact, three consumers"). Structured-MD navigation uses the file directly; vector retrieval uses the chunks produced when ingestion runs. The two paths can be A/B compared per query without re-running conversion.

**Implementation phases:**
1. Phase 1a (APP-40): Schema specification (this section) — DONE.
2. Phase 1b (APP-45): PDF → structured markdown converter — marker-pdf is now the single converter. The earlier custom `md_export.py` (PyMuPDF + bespoke OCR/vision/translation) was retired in APP-45 alongside the dual ingestion path.
3. Phase 1c (APP-42): Parser quality fixes (GitHub Issues #41, #42) — historical, applied to the deprecated `pdf_parser.py`.
4. Static manual viewer (APP-43, GitHub Issue #48) — DONE. Single-page HTML viewer (`infra/nginx/manuals/index.html`) served by Nginx at `/manuals/` with client-side markdown rendering via `marked.js`. Auto-discovers `.md` files via Nginx `autoindex` on `/manuals/data/`. YAML frontmatter parsed for metadata display. Image paths rewritten for Nginx serving. Shared `diagnostic_api_manuals` volume between diagnostic-api and nginx. Responsive CSS, no new containers.
5. Phase 2a: Agent tool set (`list_manuals`, `list_sections`, `read_section`, `search_manual`).
6. Phase 2b: A/B comparison framework (vector RAG vs agent-navigated structured MD).

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
