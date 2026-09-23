#!/usr/bin/env bash
# Run the PROD-10 golden eval in a ONE-OFF container (never inside stf-v3-api).
#
#   bash stf_v3/scripts/run_golden_eval.sh --purpose baseline|gate|calibration|comparison|demo|adhoc \
#        [--lanes manual,obd] [--thinking on] [--cloud] [--budget-scale 2] \
#        [--concurrency 6] [--ids a,b] [--image stf-v3:local] [--wait]
#
# Same image as the API (so the code under test is the deployed build),
# manual library mounted READ-ONLY, output on the host:
#   $STF_V3_EVAL_OUT (default ~/stf_v3_evals)/<container name>/
#     run.log  preflight.txt  exit_code  <base>.json  <base>.slim.json  <base>.md  <base>.progress.jsonl
# Follow a run:  tail -f ~/stf_v3_evals/<name>/run.log     (safe to disconnect SSH)
# A V3 redeploy (down/up of stf-v3-api/worker) does not touch the eval container.
#
# Refuses (exit 4) when: the checkout has uncommitted changes; the image was
# not built from this commit; another eval is running; a GPU holds more than
# vLLM's share (Ollama / MinerU resident); Ollama has a model loaded; vLLM is
# busy; the V3 manual copy differs from V2's.  Exit codes of the eval itself:
# 0 valid, 2 finished but invalid, 4 refused, 6 watchdog.
set -euo pipefail

REPO_DIR=$(cd "$(dirname "$0")/../.." && pwd)
OUT_ROOT=${STF_V3_EVAL_OUT:-$HOME/stf_v3_evals}
IMAGE=stf-v3:local
WAIT=0
CLOUD=0
PASS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --image) IMAGE=$2; shift 2 ;;
    --wait) WAIT=1; shift ;;
    --cloud) CLOUD=1; PASS+=("$1"); shift ;;
    *) PASS+=("$1"); shift ;;
  esac
done
RUN_DIR=""
fail() {
  echo "[run_golden_eval] REFUSED: $*" >&2
  [ -n "$RUN_DIR" ] && rm -rf "$RUN_DIR"   # a refused run leaves no output directory
  exit 4
}
cd "$REPO_DIR"

# 1. The code under test is exactly this commit (FM-58).
[ -z "$(git status --porcelain --untracked-files=no)" ] || fail "working tree has uncommitted changes"
HEAD=$(git rev-parse HEAD)
LABEL=$(podman image inspect "$IMAGE" --format '{{index .Labels "org.opencontainers.image.revision"}}' 2>/dev/null || true)
[ "$LABEL" = "$HEAD" ] || fail "image $IMAGE was built from '${LABEL:-?}', the checkout is $HEAD — rebuild the image first"

# 2. One eval at a time (FM-21).
mkdir -p "$OUT_ROOT"
LOCK="$OUT_ROOT/.lock"
if [ -f "$LOCK" ]; then
  OTHER=$(cat "$LOCK")
  if podman container exists "$OTHER" 2>/dev/null && [ "$(podman inspect -f '{{.State.Running}}' "$OTHER")" = "true" ]; then
    fail "another eval is running: $OTHER (tail -f $OUT_ROOT/$OTHER/run.log)"
  fi
fi

# 3. GPU + model service (FM-20 / FM-23 / FM-37).
NAME="stf-v3-eval-$(date -u +%Y%m%dT%H%M%SZ)"
RUN_DIR="$OUT_ROOT/$NAME"
mkdir -p "$RUN_DIR"
PRE="$RUN_DIR/preflight.txt"
{
  echo "commit=$HEAD image=$IMAGE args=${PASS[*]}"
  nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader | sed 's/^/gpu /'
} | tee "$PRE"
MAXUSED=$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | sort -n | tail -1)
[ "$MAXUSED" -le 42000 ] || fail "a GPU holds ${MAXUSED} MiB (> vLLM's 36.8 GB share): Ollama / MinerU / another tenant is resident"
if podman container exists stf-ollama 2>/dev/null && [ "$(podman inspect -f '{{.State.Running}}' stf-ollama)" = "true" ]; then
  LOADED=$(podman exec stf-ollama ollama ps 2>/dev/null | tail -n +2 | grep -c . || true)
  [ "$LOADED" -eq 0 ] || fail "Ollama has a model loaded — never together with vLLM (runbook §4.4)"
fi
if [ $CLOUD -eq 0 ]; then
  METRICS=$(curl -sf http://127.0.0.1:8010/metrics) || fail "vLLM is not answering on 127.0.0.1:8010 (bash infra/vllm_ctl.sh status)"
  RUNNING=$(printf '%s\n' "$METRICS" | awk '/^vllm:num_requests_running/ {s+=$2} END {printf "%d", s}')
  [ "$RUNNING" -eq 0 ] || fail "vLLM is busy ($RUNNING requests running) — timings would not be comparable"
  PREEMPT=$(printf '%s\n' "$METRICS" | awk '/^vllm:num_preemptions_total/ {s+=$2} END {printf "%d", s}')
  echo "vllm requests_running=$RUNNING preemptions_before=$PREEMPT version=$(curl -sf http://127.0.0.1:8010/version || echo '?')" | tee -a "$PRE"
fi

# 4. The V3 manual copy is byte-identical to V2's (FM-9).
V3M=$(podman volume inspect stf_v3_manuals --format '{{.Mountpoint}}')
if podman volume exists infra_diagnostic_api_manuals 2>/dev/null; then
  V2M=$(podman volume inspect infra_diagnostic_api_manuals --format '{{.Mountpoint}}')
  DIFF=0
  while IFS= read -r f; do   # manual directory names contain spaces ("Corolla E11 Haynes")
    if [ -f "$V2M/$f" ]; then
      A=$(sha256sum "$V3M/$f" | cut -c1-16); B=$(sha256sum "$V2M/$f" | cut -c1-16)
      [ "$A" = "$B" ] || DIFF=1
      echo "manual $f v3=$A v2=$B $([ "$A" = "$B" ] && echo same || echo DIFFERENT)" | tee -a "$PRE"
    fi
  done < <(cd "$V3M" && find . -path ./uploads -prune -o \( -name '*.md' -o -name '*.index.yaml' \) -print)
  [ $DIFF -eq 0 ] || fail "the V3 manual copy differs from V2's (see DIFFERENT above) — scores would not be comparable"
fi

# 5. The same model / key settings as the running API; nothing else (no JWT
#    secret, never STF_V3_LLM_ALLOW_CLOUD — FM-34).  Values are passed by
#    name only, so they never appear on a command line.
for v in STF_V3_DATABASE_URL STF_V3_LLM_BASE_URL STF_V3_LLM_MODEL STF_V3_LLM_API_KEY STF_V3_LLM_PROFILE \
         STF_V3_CLOUD_LLM_MODEL STF_V3_CLOUD_LLM_API_KEY STF_V3_OPENROUTER_API_KEY; do
  export "$v=$(podman exec stf-v3-api printenv "$v" 2>/dev/null || true)"
done
export STF_V3_CLOUD_LLM_ENABLED=$([ $CLOUD -eq 1 ] && echo true || echo false)

echo "$NAME" > "$LOCK"
podman run -d --rm --name "$NAME" --network host \
  -e STF_V3_DATABASE_URL -e STF_V3_LLM_BASE_URL -e STF_V3_LLM_MODEL -e STF_V3_LLM_API_KEY -e STF_V3_LLM_PROFILE \
  -e STF_V3_CLOUD_LLM_ENABLED -e STF_V3_CLOUD_LLM_MODEL -e STF_V3_CLOUD_LLM_API_KEY -e STF_V3_OPENROUTER_API_KEY \
  -e STF_V3_MANUAL_STORAGE_PATH=/app/data/manuals -e STF_V3_EVAL_LOG_LEVEL=WARNING -e PYDANTIC_AI_NO_BANNER=1 \
  -v stf_v3_manuals:/app/data/manuals:ro -v "$RUN_DIR":/out \
  "$IMAGE" sh -c 'python -m stf_v3.evals run --out /out "$@" > /out/run.log 2>&1; echo $? > /out/exit_code' _ "${PASS[@]}" \
  > /dev/null
echo "[run_golden_eval] started $NAME"
echo "[run_golden_eval] follow: tail -f $RUN_DIR/run.log    result: cat $RUN_DIR/exit_code"

if [ $WAIT -eq 1 ]; then
  while podman container exists "$NAME" 2>/dev/null; do sleep 15; done
  CODE=$(cat "$RUN_DIR/exit_code" 2>/dev/null || echo 5)
  if [ $CLOUD -eq 0 ]; then
    AFTER=$(curl -sf http://127.0.0.1:8010/metrics | awk '/^vllm:num_preemptions_total/ {s+=$2} END {printf "%d", s}')
    echo "vllm preemptions_after=$AFTER (before ${PREEMPT:-?})" | tee -a "$PRE"
  fi
  tail -n 8 "$RUN_DIR/run.log"
  exit "$CODE"
fi
