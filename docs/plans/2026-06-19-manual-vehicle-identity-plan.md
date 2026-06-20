# Implementation Plan: Manual Vehicle Identity + Honest Manual Agent

- **Date:** 2026-06-19
- **Design:** [2026-06-19-manual-vehicle-identity-design.md](./2026-06-19-manual-vehicle-identity-design.md)
- **Tickets:** APP-59 (infra), HARNESS-25 (manual agent)
- **Branch:** `app-59-manual-vehicle-identity`

Ordered steps. Each step is independently testable; commit per logical unit.

## Key couplings discovered during exploration
- `rag_chunks.vehicle_model` is set at `rag/ingest.py:248` from the chunk object —
  `manufacturer` must be threaded through the same path.
- The harness `list_manuals` tool reads the **markdown frontmatter**
  (`harness_tools/manual_tools.py` → `parse_frontmatter` → `fm.get("vehicle_model")`),
  **not** the DB. So manufacturer + model must be written into each manual's `.md`
  frontmatter at conversion/ingestion, and backfilled for the 2 existing `.md` files.

## APP-59 — manual/RAG infra

1. **DB models** (`models_db.py`): add `Manual.manufacturer` (`String(100)`),
   change `Manual.vehicle_model` to `nullable=False`; add `RagChunk.manufacturer`
   (`String(100)`, nullable ok). Add a `Manual.canonical_name` computed property
   (`f"{manufacturer} {vehicle_model}"`). *(shared model — both doc sets)*

2. **Alembic migration** (new revision, unique 12-char id, single head):
   - add both columns (nullable first),
   - backfill: `MWS-150-A` → (`Yamaha`, `TRICITY155`); `Corolla E11 Haynes` →
     (`Toyota`, `Corolla E11`); copy each manual's manufacturer onto its chunks,
   - then `ALTER ... SET NOT NULL` on `manuals.manufacturer` + `manuals.vehicle_model`.
   - Verify single head before push (per CLAUDE.md preflight).

3. **Upload endpoint** (`api/v2/endpoints/manuals.py`):
   `manufacturer: str = Form(...)`, `vehicle_model: str = Form(...)` required;
   trim + collapse whitespace; reject blank → `422`. Return `canonical_name` in
   `ManualSummary` / responses. Thread `manufacturer` into the conversion/ingestion
   call.

4. **Ingestion + frontmatter** (`rag/ingest.py`, chunker, marker frontmatter writer):
   set `manufacturer` on the chunk object and the `RagChunk` insert; write
   `manufacturer:` and `vehicle_model:` into the generated markdown frontmatter.
   Backfill the frontmatter of the 2 existing `.md` files on the server.

5. **Frontend** (`obd-ui` manual upload component + `lib/api.ts` + types): add a
   required **Manufacturer** input; make **Model** required; disable **Upload
   Manual** until both non-empty + PDF selected; send both in `FormData`; show
   the canonical name in the manual list.

6. **Tests** (`diagnostic_api/tests/`): upload requires both fields (`422` on
   missing/blank); canonical name in response; chunk carries manufacturer;
   retrieval filtered by make+model returns only the matching manual.

7. **Docs (V1):** `dev_plan.md` (APP-59 ticket + changelog), `design_doc.md`
   (§8.3.7 `manuals`/`rag_chunks` + upload endpoint, version/date bump, "New in
   this revision").

## HARNESS-25 — honest manual agent

8. **`list_manuals` tool** (`harness_tools/manual_tools.py`): render each manual's
   canonical **"Manufacturer Model"** from frontmatter (`manufacturer` +
   `vehicle_model`); keep the existing model filter, add manufacturer awareness.

9. **Match-or-refuse:** add a manual-agent system-prompt rule (`harness/
   harness_prompts.py` or the manual sub-agent prompt) + `list_manuals` output
   note: a manual is authoritative only if its make/model is consistent with the
   session vehicle; when none matches, state *"no manual available for this
   vehicle"* and do not adopt a vehicle identity contradicting the session VIN.

10. **Tests** (`tests/harness_tools/`): `list_manuals` shows canonical names;
    honest-refusal message path when nothing matches.

11. **Docs (V2):** `v2_dev_plan.md` (HARNESS-25 ticket + changelog),
    `v2_design_doc.md` (manual-agent behavior + tool output, version/date bump).

## Post-implementation verification (per CLAUDE.md)
- Commit per unit on the branch, push, open PR referencing #135 (+ APP-59,
  HARNESS-25).
- Deploy the **branch** to the PolyU server (note current commit first); run the
  Alembic migration; restart `diagnostic-api`; health-check; warm the LLM.
- **Re-run the P00AF Hiace case** via the live API and confirm the agent now
  refuses to treat the Yamaha/Corolla manuals as authoritative (no
  "Yamaha scooter" confabulation) instead of the prior behavior.
- Report verdict to the user; restore the server to `main`.

## Sequencing notes
- Steps 1→4 are a dependency chain (model → migration → endpoint → ingestion).
- Step 8/9 depend on step 4 (frontmatter must carry manufacturer first).
- Frontend (5) can proceed in parallel once the endpoint contract (3) is fixed.
