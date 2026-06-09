# Codebase Cleanup & Refactoring Plan

**Date:** 2026-06-09 · **Scope:** whole repo · **Goal:** leaner, cleaner
codebase with **zero functional change**.

**Method.** Six parallel area audits (backend code, backend deps,
obd_agent, frontend, infra/docs, tests) produced 66 unique candidates;
every candidate was then adversarially reference-hunted across code,
Dockerfiles, compose files, scripts, docs, and policy (CLAUDE.md) before
receiving a verdict. Verdict totals: **37 safe-delete, 2
safe-with-companion-changes, 15 needs-decision, 10 keep (false
positives caught)**. Architecture context: [OVERVIEW.md](../../OVERVIEW.md).

**False positives the verification pass caught** (do NOT remove):
`structlog` (used by goldens/manuals/log_summary endpoints),
`python-dotenv` (required by `env_file=".env"` in both Settings classes),
`python-multipart` (FastAPI `UploadFile`), `log_summarizer.py` (Stage 0 of
the live pipeline), `tests/harness/evals/golden/v2/**` +
`golden/v1/yamaha_road_test_reference.json` (production runtime
dependencies), `pseudonymise_vin()` (APP-54 privacy gate),
`reports/`/`conftest.py` eval plumbing.

---

## Guardrails (apply to every phase)

1. **Never touch:** the golden corpus runtime paths, `pseudonymise_vin`,
   anything under `tests/harness/evals/golden/v2/locked/`.
2. **Verification gate after each phase** (all must pass before the
   commit is considered done):
   - `cd diagnostic_api && python -m pytest tests/ -q` (eval tests
     auto-skip without `--run-eval`)
   - `cd obd_agent && python -m pytest tests/ -q`
   - `docker build -f diagnostic_api/Dockerfile .` (from repo root) and
     `docker build obd-ui/`
   - `cd obd-ui && npm run build`
   - Alembic single-head check (per CLAUDE.md deploy preflight)
3. **Doc-update gate:** per CLAUDE.md, each phase's commit includes the
   matching `docs/dev_plan.md` / `docs/v2_dev_plan.md` changelog entry
   (routing: APP-XX for V1/shared, HARNESS-XX for harness items).
4. One commit per phase below — each is independently revertable.

---

## Phase 0 — Local junk sweep (no commit; nothing is git-tracked)

Delete from the working tree:

| Path | What it is |
|---|---|
| `diagnostic_api;C/`, `obd_agent;C/` | Empty dirs from a mangled Windows command |
| `steam-test/`, `scripts/` (repo root) | Empty, untracked |
| `.next/`, `.pytest_cache/` (repo root) | Build artifacts created at the wrong level |
| `infra/__pycache__/`, `obd_agent/__pycache__/`, `obd_agent/obd_agent.egg-info/` | Python build droppings |
| `infra/pdf_image_samples/` | Empty abandoned sample dir |

`.gitignore` already covers `.next/`, `*.egg-info/`, `__pycache__/`,
`*.pytest_cache` — no gitignore change needed.

## Phase 1 — Zero-risk tracked deletions (commit 1)

All verified zero-reference:

| Target | Notes |
|---|---|
| `diagnostic_api/data/2016_Jazz_Owners_Manual.pdf` | **51.7 MB**, git-tracked, copied into the Docker image (`Dockerfile:48` `COPY diagnostic_api/data/`), referenced nowhere. Biggest single win: ~52 MB off the repo checkout and ~50 MB off the image. Stays in git history unless history is rewritten (not proposed). If `data/` becomes empty, keep the dir with `.gitkeep` only if the Dockerfile COPY needs it — otherwise drop the COPY line too. |
| `docs/schema.html` | Stale generated V1-era schema page; superseded by `docs/database_schema.md`. |
| `infra/test_login.json`, `infra/test_request.json` | Manual-curl fixtures for deleted endpoints. |
| `infra/test_expert_prompts.py` | Not pytest-discoverable (bare main block), APP-05-era verification script. |
| `obd-ui/src/lib/api.ts::getManualStatus()` | Exported, zero callers. |
| `obd-ui/src/lib/types.ts::MANUAL_BUCKETS` | Zero imports (`goldens/manual/page.tsx` builds its own local array; leave that as is). |
| `obd-ui/src/components/ui/card.tsx::CardFooter` | Zero references. |
| `diagnostic_api/app/config.py::debug_mode`, `::vision_model`, `::allow_external_apis` | Settings fields never read anywhere (vision/llava leftovers from the pre-marker pipeline). |

## Phase 2 — Dependency cleanup (commit 2)

`diagnostic_api/requirements.txt`:
- Remove `email-validator==2.1.0` (no `EmailStr` anywhere; auth is
  username/password).
- Remove `phonenumbers==8.13.28` (orphaned by the 2026-03-08 PII-redaction
  removal; zero imports).
- Remove the duplicate `httpx==0.26.0` at the bottom (resolves TODO(4)).

`obd_agent/pyproject.toml` — fix dependency drift (this is what
`pip install ./obd_agent` in the API image actually reads): add
`httpx`, `structlog`, `pydantic-settings`, `python-dotenv` to
`[project] dependencies` so the installed package matches its imports.
(If Phase 3 Option B is taken first, add only what survives.)

`obd_agent/requirements*.txt`: move `pytest`, `pytest-asyncio`, `respx`
out of the runtime lists into a `requirements-dev.txt` (they are test-only).

Keep (verified in use): `structlog`, `python-dotenv`, `python-multipart`,
`tiktoken`, `jieba`, everything else.

## Phase 3 — Kill the dead edge-transport path, APP-53 (commit 3)

> **STATUS: EXECUTED 2026-06-09 — Option B chosen by owner** (the #76
> 4G upload path `jetson_uploader` → `POST /v2/obd/analyze` is actively
> used by the team and was left untouched). Branch
> `app-53-remove-dead-edge-transport`. Deviations from the plan as
> written: `schemas.py` kept **in full** (execution-time audit
> confirmed `OBDSnapshot` is the live row model of
> `log_parser`/`log_summarizer` on the production path — only its
> stale deprecation docstring was replaced); `config.py` was
> additionally deleted (orphaned once the loop went); `httpx` was added
> to `pyproject.toml` (pre-existing gap, required by the surviving
> `jetson_uploader`).

The deprecated snapshot transport posts to `/v1/telemetry/obd_snapshot`,
an endpoint that **has never existed server-side**. `jetson_uploader.py`
(JWT login → `POST /v2/obd/analyze`) is the real path. Today
`agent_loop.py` still instantiates `APIPoster` — the default edge loop
posts into a void.

**Decision required — two options:**

- **Option A (minimal, keeps live capture):** delete `api_poster.py` +
  `tests/test_api_poster.py`; rewire `agent_loop.py` to append each
  snapshot as a TSV row to a trip log and invoke `jetson_uploader` on
  shutdown/trip-end. Keeps `reader/`, `snapshot_builder.py`, `schemas.py`,
  `fixtures/`, `Dockerfile`, `infra/obd-agent.compose.override.yml`.
- **Option B (recommended if no live-hardware capture is planned for the
  pilot):** delete the whole acquisition layer — `api_poster.py`,
  `agent_loop.py`, `__main__.py`, `snapshot_builder.py`, `reader/`
  (base/live/simulation), `fixtures/simulation_scenarios.json`,
  `obd_agent/Dockerfile`, `infra/obd-agent.compose.override.yml`, the
  `OBDSnapshot`/`AdapterInfo` schemas (keep `DTCEntry`/`PIDValue` if
  `log_parser`/`log_summarizer` still reference them — audit at execution
  time), and the paired tests (`test_api_poster.py`,
  `test_snapshot_builder.py`, snapshot fixtures in `tests/conftest.py`,
  affected `test_schemas.py` cases). `obd_agent/` then becomes what it
  already is in practice: the shared analysis library + `jetson_uploader`
  CLI. Also lets `pyproject.toml` drop `obd` (GPL) from every install
  path and removes the GPL-vs-non-GPL requirements split
  (`requirements-sim.txt` collapses into `requirements.txt`).

Either option also: update `infra/README_OBD_AGENT_SETUP.md` and the
APP-53 ticket entry in `docs/dev_plan.md`.

## Phase 4 — Expire the HARNESS-19 / v1.6.0 retention windows (commit 4)

Both unregistered legacy tools were kept "one release cycle" for graceful
degradation. The window has elapsed (v1.6.0 shipped 2026-05-24; repo is
now on v1.7.0). Delete in one commit, with the v2_dev_plan changelog
entry:

- `diagnostic_api/app/harness_tools/obd_data_tools.py` (legacy
  `read_obd_data`, superseded by the six primitives) +
  `tests/harness/test_obd_data_tools.py` (36 tests) +
  `ReadOBDDataInput` in `harness_tools/input_models.py:19-53`.
- `diagnostic_api/app/harness_tools/rag_tools.py` (`search_manual`,
  removed from the registry in v1.6.0) +
  `tests/harness/test_rag_tools.py` + `SearchManualInput` in
  `input_models.py:55-86`.
- Sweep `tool_registry.py:409-413` retention comments and any
  `v2_design_doc.md` references (doc gate).

If the owner prefers to keep a re-registration path for `search_manual`
pending the hybrid-retrieval decision (see Deferred), delete only the
obd_data_tools bundle now and revisit rag_tools with that decision.

## Phase 5 — Consolidations & infra hygiene (commit 5)

1. **Relocate the orphan infra tests** (repo rule: tests live under
   `diagnostic_api/tests/`): move `infra/test_chunker.py`,
   `infra/test_parser.py`, `infra/test_ingest_idempotency.py` into
   `diagnostic_api/tests/rag/`, dropping their `sys.path` hacks. Prefer
   move over delete unless a diff shows their cases are fully duplicated
   by existing tests there.
2. **`infra/init-scripts/01-init-databases.sh`:** remove the legacy
   `interaction_logs` and `diagnostic_sessions` CREATE TABLE blocks
   (lines ~44-83) — both are zombie tables with no ORM model (V1 tables
   were dropped by migration `k2l3_drop_v1_tables`). Keep extensions
   (uuid-ossp, pgvector), roles, grants. Companion: confirm the deploy
   procedure's explicit `alembic upgrade head` step remains documented
   (it is, CLAUDE.md step 7).
3. **`infra/docker-compose.gpu-docker.yml`:** keep, but add a usage
   paragraph to `README_LOCAL_SETUP.md` (it is referenced only by a
   comment today). Delete instead if local-GPU Docker dev is declared
   out of scope.

## Phase 6 — Documentation refresh (commit 6)

- **`README.md` (root):** remove the phantom directory entries (`rag/`,
  `expert_model/`, `training/`, `eval/`, `tests/` at root — never
  created), the duplicated `docs/` line, and "Phase 1 stub" wording;
  point ports/URLs at current reality (local 8000, PolyU 8001 behind
  Nginx 8080 / stf-diagnosis.dev).
- **`infra/STARTUP_SUCCESS.md`:** delete (point-in-time launch report
  from February referencing legacy tables/endpoints).
- **`infra/QUICK_REFERENCE.md`:** fix the port-8000-vs-8001 and any
  Dify/Weaviate-era references, or fold the still-true content into
  `README_LOCAL_SETUP.md` and delete.

## Deferred — tracked but explicitly out of scope here

These came out of the audit but are product/architecture decisions, not
cleanup; each should be its own ticket:

| Item | Why deferred |
|---|---|
| Hybrid retrieval (APP-56) default flip vs. removal | Feature decision; needs golden-set A/B (see OVERVIEW.md §5.6). The code is live-but-dormant, not dead. |
| Golden corpus relocation out of `tests/` | Runtime-dependency untangling across Dockerfile, `golden_sync`, goldens endpoint, eval conftest (OVERVIEW.md §5.5). |
| `obd_analysis.py` god-module split | Behavioral-risk refactor; separate design (OVERVIEW.md §5.2). |
| 31 missing `goldens.*` keys in `zh-CN`/`zh-TW` locales | Translation content work; English fallback is functional. |
| `golden/v1/*.jsonl` historical corpora | Documented "historical eval comparability" policy; cheap to keep. **`v1/yamaha_road_test_reference.json` must stay regardless** (production dependency). |
| `golden/v2/candidates/` retention policy | Process gap (stale drafts accumulate); needs a documented promotion/expiry rule, not deletion. |

## Expected impact

- **Repo checkout:** ~52 MB smaller (Jazz PDF + misc); Docker image for
  diagnostic-api ~50 MB smaller.
- **Files removed:** ~20 tracked files in Phases 1–4 (plus ~10 more under
  Phase 3 Option B); 9 untracked junk dirs in Phase 0.
- **Dependencies:** 2 unused PyPI packages dropped, 1 duplicate pin
  resolved, obd_agent packaging metadata made truthful, test deps
  separated from runtime deps.
- **Risk:** every deletion verified zero-reference by an adversarial
  pass; each phase is one revertable commit behind the full test +
  build + doc gate.
