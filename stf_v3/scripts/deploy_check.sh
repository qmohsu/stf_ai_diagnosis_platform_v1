#!/usr/bin/env bash
# Post-deploy verification for V3 on the PolyU server (code design §10).
# Answers "is what is running the thing I meant to deploy?"  Ten checks
# (containers fresh, image commit, alembic head, /v3/health via nginx,
# worker heartbeat, storage volume writable, host GPU worker alive on the
# same commit, disk headroom, model service local + serving + generating,
# last successful backup younger than 36 h); exits non-zero if any fails.
# Sizes only ever WARN (PROD-15A FM-44); the network share is never walked
# (FM-32) — only the status file the backup service writes is read.
#
#   bash stf_v3/scripts/deploy_check.sh [--max-age-min N] [--expect-commit SHA]
#   LLM_CHECK=skip LLM_CHECK_REASON="…" bash stf_v3/scripts/deploy_check.sh   # skip check 9, visibly
#
# Defaults: containers must have been created within 30 min; expected commit
# = `git rev-parse HEAD` of the checkout this script runs from.  The vLLM
# container is NOT part of the freshness check (its lifecycle is
# infra/vllm_ctl.sh, FM-39).
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

# 9. model service (PROD-09, FM-6/20/22/33/35/36/39): the endpoint the V3
#    containers are configured with is local, lists the configured model,
#    really generates (a hung TP pair passes /health but never answers),
#    and — when it is our vLLM container — lives in its own pod.
if [ "${LLM_CHECK:-run}" = "skip" ]; then
  echo "SKIP  model service  -- LLM_CHECK=skip (reason: ${LLM_CHECK_REASON:-none given})"
else
  llm_url="$(podman exec stf-v3-api printenv STF_V3_LLM_BASE_URL 2>/dev/null)"
  llm_model="$(podman exec stf-v3-api printenv STF_V3_LLM_MODEL 2>/dev/null)"
  llm_key="$(podman exec stf-v3-api printenv STF_V3_LLM_API_KEY 2>/dev/null)"
  llm_host="$(echo "$llm_url" | sed -E 's#^[a-z]+://##; s#[:/].*$##')"
  detail="source=local url=$llm_url model=$llm_model"
  ok=1; why=""
  case "$llm_host" in
    127.0.0.1|localhost|::1|host.containers.internal) ;;
    *) ok=0; why="model source is NOT local (host=$llm_host): cloud is comparison-only, never the product path (FM-33)" ;;
  esac
  if [ "$ok" = 1 ]; then
    served="$(curl -sf -m 10 -H "Authorization: Bearer $llm_key" "${llm_url%/}/models" 2>/dev/null       | python3 -c 'import json,sys; print(" ".join(m["id"] for m in json.load(sys.stdin)["data"]))' 2>/dev/null)"
    case " $served " in
      *" $llm_model "*) ;;
      *) ok=0; why="configured model not served (served: ${served:-<endpoint down>}); STF_V3_LLM_MODEL must equal vLLM --served-model-name (FM-35)" ;;
    esac
  fi
  if [ "$ok" = 1 ]; then
    gen="$(curl -sf -m 30 -H "Authorization: Bearer $llm_key" -H 'Content-Type: application/json'       "${llm_url%/}/chat/completions"       -d "{\"model\":\"$llm_model\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with the single word: ready\"}],\"max_tokens\":8}" 2>/dev/null       | python3 -c 'import json,sys; print(json.load(sys.stdin)["choices"][0]["message"]["content"].strip()[:40])' 2>/dev/null)"
    if [ -n "$gen" ]; then detail="$detail generated=\"$gen\""; else ok=0; why="no generation within 30 s (hung / tensor-parallel deadlock, FM-22)"; fi
  fi
  if [ "$ok" = 1 ] && podman inspect stf-vllm >/dev/null 2>&1; then
    vpod="$(podman inspect -f '{{.Pod}}' stf-vllm 2>/dev/null)"
    v3pod="$(podman inspect -f '{{.Pod}}' stf-v3-api 2>/dev/null)"
    v1pod="$(podman inspect -f '{{.Pod}}' stf-postgres 2>/dev/null)"
    if [ -n "$vpod" ] && { [ "$vpod" = "$v3pod" ] || [ "$vpod" = "$v1pod" ]; }; then
      ok=0; why="stf-vllm shares a pod with V3 / V1 — start it with infra/vllm_ctl.sh (FM-36)"
    fi
    detail="$detail pod=${vpod:0:12}"
  fi
  if [ "$ok" = 1 ]; then
    report "model service local + serving + generates" 1 "$detail"
  else
    report "model service local + serving + generates" 0 "$why; $detail"
    nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader 2>/dev/null | sed 's/^/      gpu  /'
    nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader 2>/dev/null | sed 's/^/      proc /'
  fi
fi

# 10. backup freshness (PROD-15A, FM-40): judged by the time of the last
#     SUCCESSFUL backup, not by the absence of failures.
BACKUP_STALE_H="${BACKUP_STALE_H:-36}"
bk="$(curl -sf "$NGINX_URL/v3/health" 2>/dev/null | python3 -c '
import json, sys
b = json.load(sys.stdin).get("backup") or {}
o = b.get("offsite") or {}
print(b.get("state"), b.get("age_h"), o.get("state"), o.get("age_h"), b.get("last_result"))' 2>/dev/null || echo "unknown None unknown None None")"
read -r bk_state bk_age off_state off_age bk_last <<< "$bk"
if [ "$bk_state" = "ok" ]; then
  report "last backup < ${BACKUP_STALE_H} h" 1 "${bk_age} h ago; offsite ${off_state} (${off_age} h); last run ${bk_last}"
else
  report "last backup < ${BACKUP_STALE_H} h" 0 "state=${bk_state} age_h=${bk_age} last run ${bk_last} — see: python3 stf_v3/scripts/backup.py status"
fi
[ "$off_state" = "ok" ] || echo "WARN  offsite copy state=${off_state} age_h=${off_age} (network share)"
bk_root="${STF_V3_BACKUP_ROOT:-$HOME/stf_v3_backups}"
if [ -d "$bk_root" ]; then
  bk_gb="$(du -s --block-size=1G "$bk_root" 2>/dev/null | cut -f1)"
  [ "${bk_gb:-0}" -le "${BACKUP_WARN_GB:-50}" ] || echo "WARN  local backups use ${bk_gb} GB (> ${BACKUP_WARN_GB:-50} GB, FM-44)"
  [ "$(stat -c %a "$bk_root")" = "700" ] || echo "WARN  $bk_root is not mode 700 (FM-49)"
fi
# PROD-15A ②: the weekly light check and the after-backup maintenance (WARN only).
python3 - "$bk_root/status.json" <<'PY' 2>/dev/null || true
import datetime as dt, json, sys
try:
    s = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    raise SystemExit(0)
v, m = s.get("last_verify") or {}, s.get("maintenance") or {}
if not v:
    print("WARN  no weekly backup check yet (backup.py verify / stf-v3-backup-verify.timer, FM-47)")
else:
    age_d = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(v["at"])).days
    if not v.get("ok") or age_d > 8:
        print(f"WARN  weekly backup check: ok={v.get('ok')} {age_d} days ago {v.get('error') or ''}")
if m.get("error"):
    print(f"WARN  maintenance after backup failed: {m['error']}")
PY

# Storage report (PROD-15A T-26): sizes + re-created volumes + modes, WARN only.
bash "$REPO_DIR/stf_v3/scripts/storage_report.sh" "$bk_root"

if [ "$FAILS" -eq 0 ]; then echo "DEPLOY CHECK ALL PASS"; exit 0; fi
echo "DEPLOY CHECK FAILED ($FAILS)"; exit 1
