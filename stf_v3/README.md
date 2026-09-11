# stf_v3 — STF V3 backend

Self-contained V3 backend (dev plan D3): this directory has its own
dependencies, Dockerfile, Alembic chain and tests, and never imports
`diagnostic_api/`, `obd_agent/` or the V1/V2 `app` package.

Design references: `docs/v3_design_doc.md`, `docs/v3_dev_plan.md`,
`docs/plans/2026-09-08-v3-code-design.md`.

## Layout

```
src/stf_v3/          package (import as `stf_v3.<module>`)
  settings.py        all external connections (env prefix STF_V3_)
  db.py              async engine / session, declarative Base
  metadata.py        registers every model; EXPECTED_TABLES
  auth/ workshops/ vehicles/ ingest/ knowledge/ diagnosis/ jobs/
alembic/             migration chain (initial = a1b2c3d4e5f6)
scripts/             create_database.sh, check_schema.py
tests/               import hygiene + migration round-trip
```

## Database (PROD-02)

```bash
# on the PolyU server, once per database
STF_V3_DB_PASSWORD=... STF_V3_APP_PASSWORD=... \
  bash stf_v3/scripts/create_database.sh stf_v3
# apply schema (as owner role)
STF_V3_DATABASE_URL=postgresql+psycopg://stf_v3:...@127.0.0.1:5432/stf_v3 \
  alembic upgrade head
# acceptance checks
python scripts/check_schema.py --url <owner-url> --app-url <app-url>
```
