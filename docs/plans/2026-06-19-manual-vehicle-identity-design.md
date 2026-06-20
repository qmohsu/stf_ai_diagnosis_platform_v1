# Design: Required Vehicle Identity on Manuals + Honest Manual Agent

- **Date:** 2026-06-19
- **Tickets:** APP-59 (manual/RAG infra), HARNESS-25 (manual-agent behavior)
- **Status:** Approved (design phase)
- **Author:** Li-Ta Hsu

## Motivation

The first real end-to-end agent run on a Toyota Hiace (DTC P00AF, GitHub issue
#135) exposed a manual-infrastructure failure. The agent ran a thorough 30-step
investigation, correctly read **P00AF — "Turbocharger/Supercharger Boost Control
'A' Module Performance"**, then concluded the vehicle was a **Yamaha TRICITY155
scooter** and that the code was "spurious" — because the only service-manual-like
content the RAG corpus could offer was the Yamaha MWS150-A manual.

Root cause: manuals are not reliably tied to a vehicle, and nothing stops the
agent from treating a non-matching manual as authoritative.

- `manuals.vehicle_model` is **optional and freeform**; there is **no
  manufacturer field at all**.
- The manual identity in practice is the raw PDF filename (e.g.
  `MWS150-A 中文SERVICE MANUAL.pdf`).
- The vault holds exactly two manuals — Yamaha **TRICITY155** (`MWS-150-A`) and
  Toyota **Corolla E11** (Haynes) — neither a Hiace. The agent picked the
  more-wrong one and built the whole diagnosis around it.

Goal (in the user's words): *improve the manual infrastructure so the manual
agent does better with it.*

## Scope

Approved scope is **piece 1 + piece 3**. Piece 2 (surfacing the session
vehicle's make/model to the agent, e.g. via VIN decode) is a deliberate
fast-follow and out of scope here.

Consequence of deferring piece 2: the only vehicle identity the agent has is the
session **VIN**. So piece 3 delivers **honest refusal** ("none of my manuals are
confirmed for this vehicle — I won't treat them as authoritative"), which is
exactly what would have prevented the Yamaha-scooter diagnosis. **Positive
auto-routing** to the correct manual arrives with piece 2.

## Piece 1 — Structured, required vehicle identity

### Data model (`manuals` table)
- Add `manufacturer` (`String(100)`).
- Make `vehicle_model` **required** (currently nullable).
- Alembic migration: backfill the two existing rows, then enforce `NOT NULL` on
  both columns.
  - `MWS-150-A` → manufacturer `Yamaha`, model `TRICITY155`.
  - `Corolla E11 Haynes` → manufacturer `Toyota`, model `Corolla E11`.
- **Canonical name** is a computed value `"{manufacturer} {vehicle_model}"`
  (e.g. `Toyota Hiace`). Not stored redundantly — single source of truth. This
  is the identity shown in the UI, in agent tool output, and in citations. The
  on-disk PDF filename is **not** renamed.

### Chunks (`rag_chunks` table)
- Add `manufacturer` (`String(100)`); `vehicle_model` already exists.
- Populate both from the parent manual at ingestion so manual search can filter
  by make + model.

### Upload API (`POST /v2/manuals`)
- `manufacturer: str = Form(...)` and `vehicle_model: str = Form(...)` become
  **required**.
- Validation: trim, collapse internal whitespace, reject empty → `422`.
- Responses include the canonical name.

### Frontend (manual upload form)
- Add a required **Manufacturer** field; make **Model** required.
- Keep **Upload Manual** disabled until both fields are non-empty and a PDF is
  selected. Update placeholder examples (e.g. `Toyota` / `Hiace`).

## Piece 3 — Manual agent won't use the wrong book

- `list_manuals` (harness tool) surfaces each manual's canonical
  **"Manufacturer Model"** prominently, so "what is on the shelf" is
  unambiguous.
- Manual-agent instructions + tool output add a **match check**: a manual is
  authoritative **only** if its make/model is consistent with the session
  vehicle. When nothing matches, the agent must state *"no manual available for
  this vehicle"* and must **not** adopt a vehicle identity that contradicts the
  session VIN.
- Enforcement is via the canonical labels in `list_manuals` output plus a
  system-prompt rule for the manual agent — not a hard block on reads (kept
  lightweight for v1).

## Data flow

Upload (make + model required) → stored on `manuals` + propagated to
`rag_chunks` → `list_manuals` shows canonical labels → manual agent reasons with
explicit match-or-refuse against the session VIN → citations carry the canonical
name.

## Error handling

- Missing/blank make or model → `422` (API) and disabled button (UI).
- Dedup unchanged (`file_hash` unique constraint).
- After the migration there are no untagged manuals; the `NOT NULL` constraints
  keep it that way.

## Testing

- **API:** upload requires both fields; `422` on missing/blank; canonical name
  present in responses.
- **Ingestion/retrieval:** chunks carry manufacturer + model; filtered retrieval
  by make + model returns only the matching manual.
- **Harness tool:** `list_manuals` shows canonical names; honest-refusal path
  returns the explicit "no manual for this vehicle" message when nothing
  matches.
- **Frontend:** required-field validation gates the upload button.

## Documentation routing

Touches both doc sets (per `CLAUDE.md`):
- **V1 / APP-59** — `docs/dev_plan.md` + `docs/design_doc.md`: `manuals` +
  `rag_chunks` schema, upload endpoint, frontend form, RAG ingestion.
- **V2 / HARNESS-25** — `docs/v2_dev_plan.md` + `docs/v2_design_doc.md`:
  manual-agent honest-matching behavior and `list_manuals` tool output.
- `models_db.py` change is shared (both sets).

## Out of scope (YAGNI)

- Renaming the PDF on disk.
- A fixed manufacturer dropdown / controlled vocabulary.
- VIN decoding and positive auto-routing (piece 2 — fast-follow).
