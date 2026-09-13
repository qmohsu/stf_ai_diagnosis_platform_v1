#!/usr/bin/env bash
# Isolation regression: proves V1/V2 were not touched by a V3 deployment.
#
#   bash stf_v3/scripts/isolation_check.sh snapshot   # before V3 work
#   bash stf_v3/scripts/isolation_check.sh compare    # after V3 work
#
# Runs on the PolyU server host (uses podman exec / curl on 127.0.0.1).
# Snapshot file: ${ISOLATION_SNAPSHOT:-/tmp/stf_v12_baseline.txt}
set -euo pipefail

MODE="${1:?snapshot|compare}"
SNAP="${ISOLATION_SNAPSHOT:-/tmp/stf_v12_baseline.txt}"
PG_CONTAINER="${PG_CONTAINER:-stf-postgres}"
API_CONTAINER="${API_CONTAINER:-stf-diagnostic-api}"
PG_SUPERUSER="${PG_SUPERUSER:-stf_user}"
V12_DB="${V12_DB:-stf_diagnosis}"

collect() {
  echo "alembic_v12=$(podman exec "$API_CONTAINER" alembic current 2>/dev/null | tail -1 | awk '{print $1}')"
  podman exec "$PG_CONTAINER" psql -U "$PG_SUPERUSER" -d "$V12_DB" -tA -c \
    "SELECT 'obd_analysis_sessions='||count(*) FROM obd_analysis_sessions
     UNION ALL SELECT 'manuals='||count(*) FROM manuals
     UNION ALL SELECT 'users='||count(*) FROM users
     UNION ALL SELECT 'harness_event_log='||count(*) FROM harness_event_log
     UNION ALL SELECT 'rag_chunks='||count(*) FROM rag_chunks
     UNION ALL SELECT 'tables='||count(*) FROM pg_tables WHERE schemaname='public'"
  # PROD-05: V3 has its own log volume; V1's must keep exactly its files.
  echo "v1_obd_log_files=$(podman exec "$API_CONTAINER" sh -c 'find /app/data/obd_logs -type f 2>/dev/null | wc -l' | tr -d ' ')"
  echo "health_v12=$(curl -sf http://127.0.0.1:8001/health | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])')"
  echo "nginx=$(curl -sf -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/health)"
}

case "$MODE" in
  snapshot)
    collect > "$SNAP"
    echo "baseline written to $SNAP:"; cat "$SNAP" ;;
  compare)
    [ -f "$SNAP" ] || { echo "no snapshot at $SNAP"; exit 2; }
    NOW="$(mktemp)"; collect > "$NOW"
    if diff -u "$SNAP" "$NOW"; then
      echo "PASS  V1/V2 unchanged"; rm -f "$NOW"
    else
      echo "FAIL  V1/V2 state differs from baseline"; rm -f "$NOW"; exit 1
    fi ;;
  *) echo "usage: $0 snapshot|compare"; exit 2 ;;
esac
