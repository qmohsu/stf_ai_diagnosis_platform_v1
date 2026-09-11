#!/usr/bin/env bash
# Creates the V3 database and its two roles inside the EXISTING Postgres
# container (shared instance, dev plan D3 constraint B).  Idempotent.
#
#   stf_v3   -- owner role; Alembic migrations run as this role
#   stf_v3_app -- runtime role; INSERT/SELECT only on audit_events
#
# Usage (on the PolyU server):
#   STF_V3_DB_PASSWORD=... STF_V3_APP_PASSWORD=... \
#     bash stf_v3/scripts/create_database.sh [db_name]
#
# Environment:
#   PG_CONTAINER   Postgres container name   (default: stf-postgres)
#   PG_SUPERUSER   superuser inside container (default: stf_user)
#   STF_V3_DB_PASSWORD / STF_V3_APP_PASSWORD  required
set -euo pipefail

DB_NAME="${1:-stf_v3}"
PG_CONTAINER="${PG_CONTAINER:-stf-postgres}"
PG_SUPERUSER="${PG_SUPERUSER:-stf_user}"
: "${STF_V3_DB_PASSWORD:?set STF_V3_DB_PASSWORD}"
: "${STF_V3_APP_PASSWORD:?set STF_V3_APP_PASSWORD}"

psql_super() {
  podman exec -i "$PG_CONTAINER" psql -U "$PG_SUPERUSER" -d postgres \
    -v ON_ERROR_STOP=1 -X -q "$@"
}

psql_super <<SQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stf_v3') THEN
    CREATE ROLE stf_v3 LOGIN PASSWORD '${STF_V3_DB_PASSWORD}';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'stf_v3_app') THEN
    CREATE ROLE stf_v3_app LOGIN PASSWORD '${STF_V3_APP_PASSWORD}';
  END IF;
END
\$\$;
SELECT 'CREATE DATABASE ${DB_NAME} OWNER stf_v3'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}')
\\gexec
SQL

# The owner must be able to grant on the schema it will populate.
psql_super -d "$DB_NAME" <<SQL
ALTER SCHEMA public OWNER TO stf_v3;
SQL

echo "database ${DB_NAME} ready (owner stf_v3, runtime role stf_v3_app)"
