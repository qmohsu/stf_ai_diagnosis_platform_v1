#!/usr/bin/env bash
# Run BEFORE `down`/`up` of the V3 containers or a restart of the host GPU
# worker (PROD-11, round-2 FM-34).  A redeploy kills whatever the workers
# are doing: an unfinished diagnosis ends as `diagnosis_interrupted`, a
# manual conversion is re-queued and rerun.  This check refuses (exit 3)
# while either is in flight, unless ALLOW_INTERRUPT=1 is set on purpose.
# PROD-15A (FM-28): it also refuses while the daily backup is running — a
# redeploy that migrates the schema would queue behind the dump.
#
#   bash stf_v3/scripts/predeploy_check.sh                    # refuse if busy
#   ALLOW_INTERRUPT=1 bash stf_v3/scripts/predeploy_check.sh  # proceed anyway
#
# Runs on the PolyU server host.  SQL goes through the Postgres container
# with the V3 owner role from infra/.env (password via the environment,
# never printed and never on a command line — PROD-15A).
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
set -a; . "$REPO_DIR/infra/.env"; set +a
PG_CONTAINER="${PG_CONTAINER:-stf-postgres}"
DB_URL="${STF_V3_DATABASE_URL:?set STF_V3_DATABASE_URL}"
. "$REPO_DIR/stf_v3/scripts/pg_env.sh"

q() { podman exec -e PGPASSWORD -i "$PG_CONTAINER" psql -h "$PGHOST" -p "$PGPORT" -U "$PGUSER" -d "$PGDATABASE" -X -q -t -A -c "$1"; }

DIAG="$(q "SELECT count(*) FROM diagnosis_conversations WHERE status IN ('queued','running')")"
CONV="$(q "SELECT count(*) FROM manuals WHERE status = 'converting'")"
BACKUP=0
systemctl --user is-active --quiet stf-v3-backup.service 2>/dev/null && BACKUP=1
echo "unfinished diagnoses: $DIAG · manuals converting: $CONV · backup running: $BACKUP"
if [ "$DIAG" != "0" ] || [ "$CONV" != "0" ] || [ "$BACKUP" != "0" ]; then
  if [ "${ALLOW_INTERRUPT:-0}" = "1" ]; then
    echo "WARN: proceeding with ALLOW_INTERRUPT=1 — unfinished diagnoses will end as 'diagnosis_interrupted', a converting manual will be rerun"
    exit 0
  fi
  echo "REFUSE: work in flight; wait for it (GET /v3/health → diagnosis / manuals; systemctl --user status stf-v3-backup) or rerun with ALLOW_INTERRUPT=1"
  exit 3
fi
echo "OK: nothing in flight"
