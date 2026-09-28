#!/usr/bin/env bash
# Connection-limit drill (PROD-15A T-22 / T-23; FM-45 / FM-59 / FM-60).
#
#   bash stf_v3/scripts/conn_limit_drill.sh           # throwaway container (never production)
#   bash stf_v3/scripts/conn_limit_drill.sh --live    # production, low traffic only
#
# Throwaway: a no-network container of the SAME Postgres image; a V3-like
# role with stf_v3_app's limit is filled to the limit, one more connection
# must be refused, while a V1/V2-like role (no limit) still connects and
# writes.  Nothing touches stf-postgres.
#
# --live: refuses while a diagnosis is queued / running.  Opens just enough
# stf_v3_app sessions (inside the database container, 3 s each) to reach the
# limit, then ONE more, which must be refused; everything closes by itself.
# Prints counts only.
set -euo pipefail
PG_CONTAINER="${PG_CONTAINER:-stf-postgres}"
DRILL=stf-v3-conn-drill
NGINX_URL="${NGINX_URL:-http://127.0.0.1:8080}"
SU="$(podman exec "$PG_CONTAINER" printenv POSTGRES_USER)"
LIMIT="$(podman exec "$PG_CONTAINER" psql -U "$SU" -d postgres -XAtc \
  "SELECT rolconnlimit FROM pg_roles WHERE rolname = 'stf_v3_app'")"
[ "${LIMIT:-0}" -gt 0 ] || { echo "stf_v3_app has no connection limit (rolconnlimit=$LIMIT) — run db_roles.sh first"; exit 1; }

# Fills ROLE to LIMIT with 3-20 s sleepers, then tries one more; prints one line.
fill_and_probe() {  # container user db hold_s n_to_open
  podman exec "$1" sh -c "
    for i in \$(seq $5); do psql -U $2 -d $3 -XAtc 'SELECT pg_sleep($4)' >/dev/null 2>&1 & done
    sleep 2
    psql -U $2 -d $3 -XAtc 'SELECT 1' 2>&1 | grep -o 'too many connections for role[^,]*' || echo 'EXTRA CONNECTION ACCEPTED'
    wait"
}

if [ "${1:-}" = "--live" ]; then
  busy="$(curl -sf "$NGINX_URL/v3/health" | python3 -c '
import json, sys
d = json.load(sys.stdin)["diagnosis"]
print(d["queued"] + d["running"])')"
  [ "$busy" = "0" ] || { echo "refused: $busy diagnosis queued/running — run at low traffic"; exit 3; }
  cur="$(podman exec "$PG_CONTAINER" psql -U "$SU" -d postgres -XAtc \
    "SELECT count(*) FROM pg_stat_activity WHERE usename = 'stf_v3_app'")"
  need=$((LIMIT - cur))
  echo "stf_v3_app: $cur of $LIMIT connections in use; opening $need for 3 s, then one more"
  out="$(fill_and_probe "$PG_CONTAINER" stf_v3_app stf_v3 3 "$need")"
  echo "extra connection: $out"
  [[ "$out" == too\ many\ connections* ]] && { echo "LIVE LIMIT PASS"; exit 0; }
  echo "LIVE LIMIT FAIL"; exit 1
fi

IMAGE="$(podman inspect "$PG_CONTAINER" --format '{{.ImageName}}')"
podman rm -f -v "$DRILL" >/dev/null 2>&1 || true
trap 'podman rm -f -v "$DRILL" >/dev/null 2>&1 || true' EXIT
podman run -d --rm --name "$DRILL" --network none -e POSTGRES_HOST_AUTH_METHOD=trust \
  -e POSTGRES_USER=postgres "$IMAGE" >/dev/null
for _ in $(seq 60); do
  if podman exec "$DRILL" pg_isready -U postgres -q 2>/dev/null; then
    sleep 3                                  # the image restarts once after init
    podman exec "$DRILL" pg_isready -U postgres -q 2>/dev/null && break
  fi
  sleep 2
done
podman exec "$DRILL" psql -U postgres -XAtq -v ON_ERROR_STOP=1 -c "CREATE DATABASE sim" \
  -c "CREATE ROLE v3sim LOGIN NOSUPERUSER CONNECTION LIMIT $LIMIT" -c "CREATE ROLE v12sim LOGIN NOSUPERUSER"
podman exec "$DRILL" psql -U postgres -d sim -XAtq -c "GRANT ALL ON SCHEMA public TO v12sim"
echo "drill: V3-like role at the production limit ($LIMIT), V1/V2-like role without one"
out="$(podman exec "$DRILL" sh -c "
  for i in \$(seq $LIMIT); do psql -U v3sim -d sim -XAtc 'SELECT pg_sleep(20)' >/dev/null 2>&1 & done
  sleep 4
  echo held=\$(psql -U postgres -d sim -XAtc \"SELECT count(*) FROM pg_stat_activity WHERE usename = 'v3sim'\")
  psql -U v3sim -d sim -XAtc 'SELECT 1' 2>&1 | grep -o 'too many connections for role[^,]*' || echo 'v3 extra: ACCEPTED'
  echo v12=\$(psql -U v12sim -d sim -XAtc 'CREATE TABLE t (x int); INSERT INTO t VALUES (1); SELECT count(*) FROM t' 2>&1 | tail -1)
  wait")"
echo "$out" | sed 's/^/  /'
if echo "$out" | grep -q "held=$LIMIT" && echo "$out" | grep -q "too many connections for role" \
    && echo "$out" | grep -q "v12=1"; then
  echo "DRILL PASS: V3 refused at $LIMIT, V1/V2 still connects and writes"
  exit 0
fi
echo "DRILL FAIL"
exit 1
