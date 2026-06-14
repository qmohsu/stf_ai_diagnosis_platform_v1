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

### Vehicle identifier policy (APP-54, current internal-development stage)

- Raw VINs (17-char ISO 3779) **may** be used as `vehicle_id` end-to-end in the backend, including in DB columns, on-disk files, structured logs, and LLM context.  This is a deliberate relaxation of the earlier "no raw VINs ever" rule, justified by the experimental-vehicles / pre-customer stage and the need for stable per-vehicle traceability across pipeline refactors.
- The `pseudonymise_vin()` helper in `obd_agent/log_parser.py` is **dormant** on the upload hot path but retained for the corpus-export redactor.
- **Raw VINs MUST NOT leave the backend to external recipients** without first being pseudonymised.  Any data export, paper artefact, partner demo, or public release goes through `diagnostic_api/scripts/export_anonymised_corpus.py` (or equivalent) so every raw VIN is replaced with its `V-{8-hex}` pseudonym.
- Test fixtures and committed examples in this repo should still use **fake** VINs (e.g. the existing `JHMGK5830HX202404` / `1HGCM82633A123456`) — the policy is "raw VINs in our backend storage are fine"; "raw VINs in the public Git history" is a separate concern and the answer remains no.

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

## Post-Implementation Verification (Mandatory)

After implementing something new (a feature, endpoint, or bug fix that is observable in the running app), do NOT stop at "code written + unit tests pass". Carry it through this loop before declaring it done, then hand the merge decision to the user:

1. **Open a PR.** Commit on a feature branch (never `main`; branch from clean `main` before the first commit), push, and open a PR referencing the issue. Doc updates ride in the same commit (per the Documentation Update Rule above).
2. **Deploy the *branch* on the PolyU server.** Run the Server Deployment procedure below, but check out the feature branch instead of pulling `main` in step 3:
   ```
   ssh polyu-gpu "cd ~/stf_ai_diagnosis_platform_v1 && git fetch origin && git checkout <branch> && git pull origin <branch>"
   ```
   Then do the rebuild → force-recreate → Alembic → restart → health-check → LLM warm-up steps exactly as written. Note the current branch/commit first so you can restore it afterward.
3. **Test end-to-end with the Chrome MCP** against the live deployment (`https://stf-diagnosis.dev`). Drive the actual user flow the change touches — log in, exercise the new path, and confirm the observable behaviour with screenshots / network / DB rows. Test in **Incognito** (no extensions, DevTools closed): the Claude-in-Chrome extension buffers streaming fetch/SSE, so streams look stuck until close. For streaming endpoints, trust server-side signals (GPU util, `ollama` logs, `diagnosis_history` / `harness_event_log` rows) and `curl -N`, not in-extension probes.
4. **Report back to the user** with an explicit verdict: what you tested, what passed/failed (with evidence), and **"good to merge"** or **"not yet, because …"**. Do NOT merge yourself — the merge call is the user's.
5. **Restore the server to `main`** after testing (unless the user says to leave the branch up), so prod is never left on an unmerged branch:
   ```
   ssh polyu-gpu "cd ~/stf_ai_diagnosis_platform_v1 && git checkout main && git pull origin main"
   ```
   Redeploy `main` if you had rebuilt containers off the branch.

Skip this loop only for changes with no observable runtime behaviour (pure docs, comments, or internal refactors that can't be exercised in the browser) — those still get a PR and a report, just no deploy/E2E.

## Server Deployment (PolyU GPU Server)

When the user says **"deploy to server"** or **"update the server"**, follow this procedure:

1. **Pre-flight: git state**: Run `git status` and `git log origin/main..HEAD` locally to verify all changes are committed and pushed to `origin/main`. If there are unpushed commits or uncommitted changes, warn the user and do NOT proceed until everything is pushed.
2. **Pre-flight: migration backlog check**: List Alembic migrations added since the last successful deploy. Catches schema drift like the HARNESS-20 backlog that silently 500'd `/goldens` on 2026-05-24:
   ```
   ssh polyu-gpu "podman exec stf-diagnostic-api alembic current 2>/dev/null | tail -1"  # what prod is on
   git log --oneline --name-only -- diagnostic_api/alembic/versions/ | head -30          # what's in the repo
   ```
   Any new versions files since the prod head mean migrations will need to run after rebuild. Also confirm a single Alembic head locally — duplicate revision ids (also seen in the HARNESS-20 backlog) make `alembic upgrade head` refuse silently:
   ```
   cd diagnostic_api && python -c "from alembic.script import ScriptDirectory; from alembic.config import Config; import os; c = Config('alembic.ini'); c.set_main_option('script_location', os.path.abspath('alembic')); print(ScriptDirectory.from_config(c).get_heads())"
   ```
   Expected output: a single-element list (`['<rev_id>']`). If you see two or more heads, fix the conflicting migration files before deploying.
3. **Pull on server**: `ssh polyu-gpu "cd ~/stf_ai_diagnosis_platform_v1 && git pull origin main"`
4. **Rebuild images**: `ssh polyu-gpu "cd ~/stf_ai_diagnosis_platform_v1/infra && ~/.local/bin/podman-compose -f docker-compose.yml -f docker-compose.polyu.yml build diagnostic-api obd-ui"`
5. **Force-recreate changed containers**: Podman 3.4 does NOT recreate containers when the image changes — `up -d --build` silently keeps old containers. You MUST use `down`+`up` for changed services:
   ```
   ssh polyu-gpu "cd ~/stf_ai_diagnosis_platform_v1/infra && \
     ~/.local/bin/podman-compose -f docker-compose.yml -f docker-compose.polyu.yml down && sleep 2 && \
     ~/.local/bin/podman-compose -f docker-compose.yml -f docker-compose.polyu.yml up -d postgres && sleep 5 && \
     ~/.local/bin/podman-compose -f docker-compose.yml -f docker-compose.polyu.yml up -d ollama && sleep 2 && \
     ~/.local/bin/podman-compose -f docker-compose.yml -f docker-compose.polyu.yml up -d diagnostic-api && sleep 5 && \
     ~/.local/bin/podman-compose -f docker-compose.yml -f docker-compose.polyu.yml up -d obd-ui && sleep 3 && \
     ~/.local/bin/podman-compose -f docker-compose.yml -f docker-compose.polyu.yml up -d nginx"
   ```
6. **Verify containers are fresh**: Check that `CREATED AT` timestamps are recent (within the last minute) for ALL rebuilt services. Old timestamps mean the container was NOT recreated:
   `ssh polyu-gpu "podman ps --format 'table {{.Names}} {{.CreatedAt}}'"`
7. **Run pending Alembic migrations**: After containers are up but BEFORE final verification. `alembic upgrade head` is a no-op if the DB is already current, but skipping this step is what silently leaves prod on an old schema while serving new code (HARNESS-20 surfaced this on 2026-05-24 — `/goldens` 500'd because the `is_locked` column didn't exist). Confirm the post-upgrade head matches what the repo expects:
   ```
   ssh polyu-gpu "podman exec stf-diagnostic-api alembic upgrade head 2>&1 | tail -3 && \
     podman exec stf-diagnostic-api alembic current 2>&1 | tail -1"
   ```
8. **Restart diagnostic-api after migrations**: Startup-time helpers that read from the DB (e.g. `golden_sync`) ran ONCE during the `down`+`up` in step 5 — if migrations applied in step 7 added/changed columns those helpers read, the cached state is stale. Restart so the helpers re-run against the migrated schema:
   ```
   ssh polyu-gpu "podman restart stf-diagnostic-api && sleep 5 && podman logs --tail 10 stf-diagnostic-api 2>&1 | grep -iE 'startup|golden_sync|complete' | tail -5"
   ```
   Skip only when step 7 reported "already at head" (no migrations ran).
9. **Health checks**: Verify all 5 services are healthy:
   - `curl -sf http://127.0.0.1:11434/api/version` (Ollama)
   - `curl -sf http://127.0.0.1:8001/health` (Diagnostic API)
   - `curl -sf http://127.0.0.1:3001` (OBD UI)
   - `curl -sf http://127.0.0.1:8080/health` (Nginx gateway)
10. **Warm up the local LLM** (APP-58, GitHub issue #128): The `down`+`up` in step 5 evicts the resident model, so the FIRST `/v2/obd/{id}/diagnose` triggers a multi-minute cold load. The API now auto-pre-warms in the background at startup (`LLM_PREWARM_ON_STARTUP`, default on) and every diagnose stream has a timer-based SSE keep-alive, so a real user request will survive the load — but a green step-9 health check does NOT yet mean "first generation will succeed", because `/health` returns before the model is resident. Force the load explicitly and confirm it sticks so the first expert request is instant:
    ```
    ssh polyu-gpu "curl -sf http://127.0.0.1:11434/api/generate -d '{\"model\":\"qwen3.5:27b-q8_0\",\"prompt\":\"hi\",\"stream\":false,\"keep_alive\":-1,\"options\":{\"num_predict\":5}}' >/dev/null && echo warmup_ok"
    ssh polyu-gpu "podman exec stf-ollama ollama ps"   # model should be listed as loaded (UNTIL = Forever with keep_alive=-1)
    ```
    On a shared GPU the warm-up POST can take a couple of minutes — that is the cold load happening here instead of in front of a user. If it times out, other tenants may be holding VRAM; re-run once the card frees up.
11. **Verify deployed commit**: Confirm the running code matches what was pushed:
    `ssh polyu-gpu "cd ~/stf_ai_diagnosis_platform_v1 && git log --oneline -1"`

**Server details**: Podman 3.4 (rootless), host networking, API on port 8001, Nginx on port 8080, `runtime: nvidia` for Ollama GPU.

**CRITICAL Podman 3.4 gotcha**: `podman-compose up -d --build` builds new images but does NOT recreate containers. Always use `down` + `up` to ensure containers run the latest image. Verify via `podman ps` creation timestamps.

**CRITICAL Alembic gotchas**:
- **Migrations don't auto-run on container start.** The image bakes in the migration files (under `/app/alembic/`), but no startup hook applies them. You MUST run `alembic upgrade head` after every rebuild that includes new migration files (step 7 above). The diagnostic-api container will happily boot against an out-of-date schema and serve 500s when endpoints touch missing columns. The startup guardrail in `app/services/alembic_check.py` now catches this immediately rather than at first-API-call, but the deploy procedure should still run the migrations explicitly.
- **Duplicate revision ids silently break the whole chain.** If two migration files declare the same `revision = "..."` value, Alembic refuses every upgrade with `Revision X is present more than once`. The wrong way to find out is when prod 500s. The preflight in step 2 catches this — always check `ScriptDirectory.get_heads()` before pushing a migration. When authoring a new migration, copy an unused 12-char hex/alphanum id; don't crib from an old filename.
- **Lossy downgrades are normal here.** Several migrations (including `b1c2d3e4f5a6` and `c2d3e4f5a6b7`) deliberately drop columns whose data has no meaningful pre-migration form. Don't expect `alembic downgrade` to round-trip cleanly. Use downgrades only to roll back schema, never to roll back data.

## Memory Management

When you discover something valuable for future sessions — architectural decisions, bug fixes, gotchas, environment quirks — immediately append it to .claude/memory.md

Don't wait to be asked. Don't wait for session end.

Keep entries short: date, what, why. Read this file at the start of every session.
