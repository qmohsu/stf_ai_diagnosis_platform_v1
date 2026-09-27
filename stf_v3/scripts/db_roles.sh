#!/usr/bin/env bash
# Applies the operational database roles (stf_v3/scripts/sql/ops_roles.sql)
# inside the database container as its superuser (unix socket, no password).
# Idempotent; part of the restore procedure (PROD-15A FM-24).
#
#   bash stf_v3/scripts/db_roles.sh
set -euo pipefail
PG_CONTAINER="${PG_CONTAINER:-stf-postgres}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SU="$(podman exec "$PG_CONTAINER" printenv POSTGRES_USER)"
podman exec -i "$PG_CONTAINER" psql -U "$SU" -d postgres -v ON_ERROR_STOP=1 -X -q \
  < "$HERE/sql/ops_roles.sql"
# Report shape only (never a password hash).
podman exec "$PG_CONTAINER" psql -U "$SU" -d postgres -X -A -t -c \
  "SELECT rolname || ' login=' || rolcanlogin || ' super=' || rolsuper || ' limit=' || rolconnlimit
          || ' password=' || (rolpassword IS NOT NULL)
     FROM pg_authid WHERE rolname LIKE 'stf_v3%' ORDER BY 1"
