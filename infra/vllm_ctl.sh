#!/usr/bin/env bash
# Start / stop / inspect the vLLM model service (PROD-09 D1, issue #237).
#
#   bash infra/vllm_ctl.sh start          # up -d (cold start ≈ 10 min); then `wait`
#   bash infra/vllm_ctl.sh wait [SECONDS] # poll /v1/models until the model is listed (default 1200 s)
#   bash infra/vllm_ctl.sh status         # container / pod / health / served models / GPU summary
#   bash infra/vllm_ctl.sh stop           # down (frees both GPUs; e.g. before an Ollama fallback)
#   bash infra/vllm_ctl.sh logs [N]       # last N container log lines
#   bash infra/vllm_ctl.sh install-unit   # user systemd unit so the service comes back after a reboot
#
# ALWAYS the same project name (stf_llm → pod_stf_llm): without it
# podman-compose would put vLLM into V1/V2's pod_infra and `down` would
# tear that pod down (FM-36; the 2026-09-12 lesson).  Startup order after
# a reboot: this → stf-v3-gpu-worker → V3 containers (runbook §4).
set -uo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
PC="${PODMAN_COMPOSE:-$HOME/.local/bin/podman-compose}"
COMPOSE=("$PC" -p stf_llm -f "$DIR/docker-compose.vllm.yml")
BASE_URL="${VLLM_BASE_URL:-http://127.0.0.1:8010/v1}"
MODEL="${VLLM_MODEL:-Qwen/Qwen3.6-27B-FP8}"

gpu_summary() {
  nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader 2>/dev/null | sed 's/^/  gpu  /'
  nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv,noheader 2>/dev/null | sed 's/^/  proc /'
}

served_models() {
  curl -sf -m 10 "${BASE_URL%/}/models" 2>/dev/null \
    | python3 -c 'import json,sys; print(" ".join(m["id"] for m in json.load(sys.stdin)["data"]))' 2>/dev/null
}

case "${1:-}" in
  start)
    echo "GPU before start (vLLM needs ~37 GB per card at 0.80; see who holds it if start fails, FM-37):"
    gpu_summary
    busy="$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | awk '$1 > 8000 {c++} END {print c+0}')"
    [ "${busy:-0}" -gt 0 ] && echo "WARN: a GPU already holds > 8 GB — vLLM may fail to start (Ollama model resident? another tenant?)"
    podman rm -f stf-vllm >/dev/null 2>&1 || true   # a leftover from a manual `podman run`
    "${COMPOSE[@]}" up -d stf-vllm || exit 1
    echo "stf-vllm starting (cold start ≈ 10 min). Next: bash $0 wait"
    ;;
  stop)
    "${COMPOSE[@]}" down
    ;;
  wait)
    limit="${2:-1200}"
    started=$(date +%s)
    while :; do
      served="$(served_models)"
      case " $served " in *" $MODEL "*) echo "ready: $MODEL listed after $(( $(date +%s) - started ))s"; exit 0 ;; esac
      if [ $(( $(date +%s) - started )) -ge "$limit" ]; then
        echo "TIMEOUT after ${limit}s: served='${served:-<none>}'"; gpu_summary
        podman logs --tail 30 stf-vllm 2>&1 | sed 's/^/  log  /'; exit 1
      fi
      sleep 10
    done
    ;;
  status)
    if podman inspect stf-vllm >/dev/null 2>&1; then
      podman inspect -f 'container=stf-vllm state={{.State.Status}} pod={{.Pod}} restarts={{.RestartCount}} image={{.ImageName}}' stf-vllm
      echo "health: $(podman inspect -f '{{.State.Healthcheck.Status}}' stf-vllm 2>/dev/null || podman inspect -f '{{.State.Health.Status}}' stf-vllm 2>/dev/null || echo n/a)"
      podman pod ps --format '{{.Name}} {{.Id}}' | grep -q "$(podman inspect -f '{{.Pod}}' stf-vllm | cut -c1-12)" \
        && echo "pod name: $(podman pod ps --format '{{.Name}} {{.Id}}' | grep "$(podman inspect -f '{{.Pod}}' stf-vllm | cut -c1-12)" | awk '{print $1}')"
    else
      echo "container=stf-vllm not found"
    fi
    echo "served models: $(served_models || echo '<endpoint down>')"
    gpu_summary
    ;;
  logs)
    podman logs --tail "${2:-100}" stf-vllm
    ;;
  install-unit)
    unit_dir="$HOME/.config/systemd/user"; mkdir -p "$unit_dir"
    sed "s#@REPO@#$(cd "$DIR/.." && pwd)#g" "$DIR/stf-llm.service" > "$unit_dir/stf-llm.service"
    systemctl --user daemon-reload && systemctl --user enable stf-llm.service \
      && echo "installed $unit_dir/stf-llm.service (enabled; starts on login/boot with linger)"
    ;;
  *)
    echo "usage: $0 start|stop|wait [s]|status|logs [n]|install-unit"; exit 2 ;;
esac
