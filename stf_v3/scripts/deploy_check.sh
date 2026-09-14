#!/usr/bin/env bash
# Post-deploy verification for V3 on the PolyU server (code design §10).
# Answers "is what is running the thing I meant to deploy?"  Eight checks
# (containers fresh, image commit, alembic head, /v3/health via nginx,
# worker heartbeat, storage volume writable, host GPU worker alive on the
# same commit, disk headroom); exits non-zero if any fails.
#
#   bash stf_v3/scripts/deploy_check.sh [--max-age-min N] [--expect-commit SHA]
#
# Defaults: containers must have been created within 30 min; expected commit
# = `git rev-parse HEAD` of the checkout this script runs from.
set -uo pipefail

MAX_AGE_MIN=30
EXPECT_COMMIT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --max-age-min) MAX_AGE_MIN="$2"; shift 2 ;;
    --expect-commit) EXPECT_COMMIT="$2"; shift 2 ;;
    *) echo "unknown arg $1"; exit 2 ;;
  esac
done
REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
[ -n "$EXPECT_COMMIT" ] || EXPECT_COMMIT="$(git -C "$REPO_DIR" rev-parse HEAD)"
NGINX_URL="${NGINX_URL:-http://127.0.0.1:8080}"
FAILS=0

report() {  # name ok detail
  if [ "$2" = 1 ]; then echo "PASS  $1  -- $3"; else echo "FAIL  $1  -- $3"; FAILS=$((FAILS+1)); fi
}

# 1. containers running and fresh
for c in stf-v3-api stf-v3-worker; do
  if ! podman inspect "$c" >/dev/null 2>&1; then report "container $c running" 0 "not found"; continue; fi
  state="$(podman inspect -f '{{.State.Status}}' "$c")"
  # Podman prints e.g. "2026-09-12 23:05:12.123456789 +0800 HKT"; keep the
  # numeric offset, drop nanoseconds and the zone name so GNU date parses it.
  created="$(podman inspect -f '{{.Created}}' "$c" | sed -E 's/\.[0-9]+//' | awk '{print $1" "$2" "$3}')"
  age_sec=$(( $(date +%s) - $(date -d "$created" +%s) ))
  if [ "$state" = "running" ] && [ "$age_sec" -le $(( MAX_AGE_MIN * 60 )) ]; then
    report "container $c fresh" 1 "running, created ${age_sec}s ago"
  else
    report "container $c fresh" 0 "state=$state, created ${age_sec}s ago (limit ${MAX_AGE_MIN} min)"
  fi
done

# 2. image commit label == expected commit
label="$(podman inspect -f '{{index .Config.Labels "org.opencontainers.image.revision"}}' stf-v3-api 2>/dev/null)"
if [ "$label" = "$EXPECT_COMMIT" ]; then
  report "image commit matches checkout" 1 "$label"
else
  report "image commit matches checkout" 0 "image=$label expected=$EXPECT_COMMIT"
fi

# 3. alembic current == head (run inside the API container: it has the chain)
current="$(podman exec stf-v3-api alembic current 2>/dev/null | tail -1 | awk '{print $1}')"
head="$(podman exec stf-v3-api alembic heads 2>/dev/null | tail -1 | awk '{print $1}')"
if [ -n "$current" ] && [ "$current" = "$head" ]; then
  report "db at alembic head" 1 "$current"
else
  report "db at alembic head" 0 "current=$current head=$head"
fi

# 4. /v3/health through nginx
body="$(curl -sf "$NGINX_URL/v3/health" 2>/dev/null)"
if echo "$body" | grep -q '"db":"ok"'; then
  report "/v3/health via nginx" 1 "$body"
else
  report "/v3/health via nginx" 0 "response: ${body:-<none>}"
fi

# 5. worker heartbeat within the last 3 minutes (task runs every minute)
for _ in 1 2 3 4 5 6; do
  beats="$(podman logs --since 3m stf-v3-worker 2>&1 | grep -c 'worker.heartbeat')"
  [ "$beats" -ge 1 ] && break
  sleep 15
done
if [ "${beats:-0}" -ge 1 ]; then
  report "worker heartbeat" 1 "$beats heartbeat(s) in last 3 min"
else
  report "worker heartbeat" 0 "no heartbeat in last 3 min"
fi

# 6. raw-log storage volume mounted and writable (PROD-05)
STORAGE_PATH="${STORAGE_PATH:-/app/data/obd_logs}"
mount_src="$(podman inspect -f '{{range .Mounts}}{{if eq .Destination "'"$STORAGE_PATH"'"}}{{.Name}}{{end}}{{end}}' stf-v3-api 2>/dev/null)"
if [ -n "$mount_src" ] && podman exec stf-v3-api sh -c "touch $STORAGE_PATH/.deploy_check && rm $STORAGE_PATH/.deploy_check" >/dev/null 2>&1; then
  report "storage volume writable" 1 "volume $mount_src at $STORAGE_PATH"
else
  report "storage volume writable" 0 "mount=${mount_src:-<none>} at $STORAGE_PATH"
fi

# 7. host GPU worker alive and on the same commit (PROD-06, FM-16/18/19)
if systemctl --user is-active --quiet stf-v3-gpu-worker 2>/dev/null; then
  gw="$(curl -sf "$NGINX_URL/v3/health" 2>/dev/null | python3 -c 'import json,sys; d=json.load(sys.stdin)["gpu_worker"]; print(d["alive"], d["age_s"], d["commit"])' 2>/dev/null)"
  set -- $gw
  if [ "${1:-}" = "True" ] && [ "${3:-}" = "$EXPECT_COMMIT" ]; then
    report "host gpu worker alive + commit" 1 "heartbeat ${2}s ago, commit ${3:0:12}"
  else
    report "host gpu worker alive + commit" 0 "alive=${1:-?} age=${2:-?} commit=${3:-?} expected=${EXPECT_COMMIT:0:12}"
  fi
else
  report "host gpu worker alive + commit" 0 "systemd user service stf-v3-gpu-worker not active"
fi

# 8. disk headroom for conversions (PROD-06, FM-13): shared disk with V1/V2
MIN_FREE_GB="${MIN_FREE_GB:-30}"
free_gb="$(curl -sf "$NGINX_URL/v3/health" 2>/dev/null | python3 -c 'import json,sys; print(int(json.load(sys.stdin)["disk_free_gb"]))' 2>/dev/null || echo 0)"
if [ "$free_gb" -ge "$MIN_FREE_GB" ]; then
  report "disk free >= ${MIN_FREE_GB} GB" 1 "${free_gb} GB free"
else
  report "disk free >= ${MIN_FREE_GB} GB" 0 "${free_gb} GB free (conversions will refuse to start)"
fi

if [ "$FAILS" -eq 0 ]; then echo "DEPLOY CHECK ALL PASS"; exit 0; fi
echo "DEPLOY CHECK FAILED ($FAILS)"; exit 1
