# Implementation Plan: Required Vehicle Identity on OBD Upload + Grounding

- **Date:** 2026-06-20
- **Design:** [2026-06-20-obd-vehicle-identity-design.md](./2026-06-20-obd-vehicle-identity-design.md)
- **Tickets:** APP-60 (infra), HARNESS-26 (grounding)
- **Branch:** `app-60-obd-vehicle-identity` (stacked on `app-59-manual-vehicle-identity` / PR #137)

Ordered steps; commit per logical unit (APP-60, then HARNESS-26).

## Known couplings / things to verify during implementation
- `/v2/obd/analyze` already resolves `vehicle_id` via a query param,
  `_extract_vehicle_id_from_body`, and an in-row VIN column — add make/model as
  query params alongside, reusing the `_validate_vehicle_id` normalization style.
- `vehicle_id` reaches `parsed_summary` via `obd_agent/summary_formatter.py`
  (`format_summary_flat_strings`) — thread make/model through the same path.
- Agent context: `harness/harness_prompts.build_user_message` renders
  `Vehicle: {vehicle_id}` (lines ~152/190) — change to include make/model.
- `jetson_uploader.upload_log` currently sends only the body — add query params.
- Locate the actual **web OBD upload component** (the one calling
  `analyzeOBDLog` in `api.ts`) — `AnalysisLayout.tsx` only displays
  `vehicle_id`; the upload form is elsewhere. Verify before editing.

## APP-60 — OBD upload infra

1. **DB model** (`models_db.py`): add `OBDAnalysisSession.manufacturer`
   (`String(100)`, nullable) + `vehicle_model` (`String(100)`, nullable). Add a
   `canonical_name` property (`f"{manufacturer} {vehicle_model}".strip()` or
   `None` when both unset). *(shared — both doc sets)*

2. **Alembic migration** (new revision, unique 12-char id, single head): add the
   two nullable columns to `obd_analysis_sessions`. No backfill, no NOT NULL.
   Verify single head before push.

3. **Endpoint** (`api/v2/endpoints/obd_analysis.py` `analyze_obd_log`): add
   required `manufacturer: str = Query(...)` + `vehicle_model: str = Query(...)`;
   trim + collapse + `422` on blank (reuse/extend `_validate_vehicle_id`-style
   helper). Persist on the `OBDAnalysisSession` row and into `result_payload` +
   `parsed_summary`. On dedup with differing make/model, log a mismatch warning.
   Surface make/model in `OBDAnalysisResponse`.

4. **Summary plumbing** (`obd_agent/summary_formatter.py` /
   `format_summary_flat_strings`): carry `manufacturer` + `vehicle_model` into
   the flat `parsed_summary` so they reach the agent and the UI.

5. **Web UI** (OBD upload component + `lib/api.ts` `analyzeOBDLog` + types): add
   required Manufacturer + Model inputs; send as query params; gate the Analyze
   button until both + a log are present. i18n EN / zh-CN / zh-TW.

6. **Edge agent** (`obd_agent/jetson_uploader.py`): add `--manufacturer` +
   `--model` argparse args + `STF_MANUFACTURER` / `STF_MODEL` env fallback;
   thread into `upload_trip` → `upload_log` as query params; error locally with
   a clear message when unset. Update the module docstring usage example.

7. **Tests** (`diagnostic_api/tests/` + `obd_agent` tests): analyze requires
   make+model (`422`); canonical values persisted + in `parsed_summary`;
   `jetson_uploader` builds the URL with make/model params and errors when unset.

8. **Docs (V1):** `dev_plan.md` (APP-60 + changelog), `design_doc.md`
   (`obd_analysis_sessions` schema §8.3.7, `/v2/obd/analyze` params, version bump,
   "New in this revision").

## HARNESS-26 — agent context grounding

9. **`build_user_message`** (`harness/harness_prompts.py`): render
   `Vehicle: {manufacturer} {vehicle_model} (VIN {vehicle_id})` when make/model
   present; fall back to `Vehicle: {vehicle_id or "unknown"}` otherwise.

10. **Tests** (`tests/harness/`): user-message renders make/model when present;
    falls back cleanly when absent (offline-safe — `build_user_message` is a pure
    function; avoid importing the tiktoken-loading harness modules).

11. **Docs (V2):** `v2_dev_plan.md` (HARNESS-26 + changelog), `v2_design_doc.md`
    (agent user-message vehicle grounding, version bump).

## Post-implementation verification (per CLAUDE.md)
- Commit per unit on the branch, push, open PR referencing the OBD-grounding work
  (+ APP-60, HARNESS-26). It stacks on #137 — retarget to main once #137 merges.
- Deploy the branch to PolyU (note current commit first); run the migration;
  restart; health-check; warm LLM.
- **E2E**: upload the P00AF log with `manufacturer=Toyota`, `model=Hiace`; run the
  agent; confirm its context reads `Vehicle: Toyota Hiace (VIN JTFHT02P…)` and it
  no longer reverse-reasons "Corolla" — it should either match a Hiace manual (if
  one is ingested) or say "no service manual is available for this vehicle".
- Report verdict; restore the server to main (after #137 + this merge) or leave
  the branch up per the user's call.

## Sequencing notes
- Steps 1→4 are a chain (model → migration → endpoint → summary plumbing).
- Step 9 (HARNESS-26) depends on step 4 (make/model in `parsed_summary`).
- Web UI (5) and edge agent (6) can proceed in parallel once the endpoint
  contract (3) is fixed.
