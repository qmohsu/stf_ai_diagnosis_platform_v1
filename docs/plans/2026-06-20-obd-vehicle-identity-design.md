# Design: Required Vehicle Identity on OBD Upload + Agent Grounding

- **Date:** 2026-06-20
- **Tickets:** APP-60 (OBD upload infra), HARNESS-26 (agent context grounding)
- **Status:** Approved (design phase)
- **Author:** Li-Ta Hsu
- **Branch:** `app-60-obd-vehicle-identity` (stacked on `app-59-manual-vehicle-identity` / PR #137)

## Motivation

Vehicle **model cannot be derived from an OBD log**. Verified on the real
P00AF Hiace data:

- The OBDWiz road-test CSVLog is a pure sensor time-series — 0 mentions of
  VIN / model / "Hiace"; none of its 78 columns is a VIN or model field.
- The VIN (`JTFHT02P500072677`) appears only in the *separate* OBDWiz report
  files (Mode 09), never in the sensor log.
- Even when a VIN is present, the pipeline stores it verbatim as `vehicle_id`
  and does not decode it to make/model — and the friendly model name is not
  encoded in the VIN anyway.

Consequence: the agent cannot know the vehicle from the log. In the HARNESS-25
re-runs it stopped confabulating a Yamaha scooter but then **reverse-reasoned
"Toyota Corolla"** from the only Toyota manual in the vault — the log is
actually a Toyota Hiace diesel. The fix is to make the uploader **state the
vehicle**, and to surface it to the agent so manual matching works positively.

## Scope

- **APP-60** — required `manufacturer` + `vehicle_model` on every OBD upload
  (`/v2/obd/analyze`), across the web UI and the `jetson_uploader` edge agent.
- **HARNESS-26** — surface make/model in the agent's context so it grounds on
  the stated vehicle instead of guessing from the manual shelf.

Mirrors APP-59 (manuals). Keyed by **make + model** to match the manuals'
canonical identity.

## APP-60 — required vehicle identity on OBD upload

### Data model (`obd_analysis_sessions`)
- Add `manufacturer` (`String(100)`) and `vehicle_model` (`String(100)`).
- Keep the existing `vehicle_id` (`String(50)`, VIN/label) for traceability.
- Canonical display/match value = `"{manufacturer} {vehicle_model}"`.
- **Enforcement: API-level required, DB columns nullable.** OBD sessions can be
  thousands of historical trips; backfilling them all to a placeholder then
  `NOT NULL` is lossy and meaningless. API-level `422` guarantees every *new*
  session carries make/model; historical rows stay valid. Alembic migration
  adds the two nullable columns only (no backfill, no NOT NULL).

### Endpoint (`POST /v2/obd/analyze`)
- New required query params `manufacturer` + `vehicle_model` (consistent with
  the existing `?vehicle_id=`), trimmed + whitespace-collapsed, `422` on blank.
- Persist both on the session and into `result_payload` / `parsed_summary`.
- Dedup re-upload with a different make/model logs a mismatch warning (same
  pattern APP-54 used for `vehicle_id`); the existing row stays source of truth.

### Web UI (OBD upload form)
- Add required **Manufacturer** + **Model** fields; gate the Analyze button
  until both + a log are present. i18n EN / zh-CN / zh-TW.

### Edge agent (`jetson_uploader`)
- New required `--manufacturer` + `--model` CLI args (and `STF_MANUFACTURER` /
  `STF_MODEL` env). The device is installed in one fixed vehicle, so it is
  configured once and sends them as query params on every upload. Missing
  values fail **locally** with a clear message (a misconfigured device fails
  loudly, not silently at the server).

## HARNESS-26 — agent context grounding

- Thread make/model through `format_summary_flat_strings` /
  `summary_formatter` into `parsed_summary`.
- `harness/harness_prompts.build_user_message` renders
  `Vehicle: {manufacturer} {vehicle_model} (VIN {vehicle_id})` instead of the
  bare `vehicle_id`.
- This makes the HARNESS-25 match-or-refuse rule work **positively**: the agent
  can affirmatively select the manual whose make/model matches the stated
  vehicle, or correctly say none matches — instead of reverse-reasoning the
  model from the only same-make manual.

## Data flow

Upload (make+model required, web or edge) → persisted on the session +
`parsed_summary` → agent user message reads `Vehicle: <Make> <Model> (VIN …)` →
manual matching (HARNESS-25) resolves the right manual or an honest "none
matches".

## Error handling

- Missing/blank make or model → `422` (API), disabled button (UI), local error
  (edge agent).
- Dedup unchanged (`input_text_hash`); make/model mismatch on re-upload is a
  logged warning, not an error.
- Historical sessions (null make/model) remain readable; the agent shows
  `Vehicle: unknown (VIN …)` for them, exactly as today.

## Testing

- **API:** analyze requires make + model (`422` on missing/blank); canonical
  values persisted on the session and in `parsed_summary`.
- **Edge agent:** `jetson_uploader` errors locally when make/model unset; sends
  them as query params when set.
- **Harness:** `build_user_message` renders `Vehicle: <Make> <Model> (VIN …)`;
  falls back to `unknown` when absent.
- **Frontend:** required-field validation gates the Analyze button.

## Documentation routing

- **V1 / APP-60** — `dev_plan.md` + `design_doc.md`: `obd_analysis_sessions`
  schema, `/v2/obd/analyze` params, web upload form, `jetson_uploader`.
- **V2 / HARNESS-26** — `v2_dev_plan.md` + `v2_design_doc.md`: agent user-message
  vehicle grounding.
- `models_db.py` change is shared (both sets).

## Out of scope (YAGNI)

- VIN decoding (the device / human supplies make/model directly — simpler and
  more reliable than a decoder + VIN-in-log dependency).
- A server-side device→vehicle registry.
- DB `NOT NULL` + historical backfill.
