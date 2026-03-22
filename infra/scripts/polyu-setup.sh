#!/usr/bin/env bash
# ============================================================================
# STF AI Diagnosis Platform - PolyU Server First-Time Setup
# Author: Li-Ta Hsu
# Date: March 2026
# ============================================================================
# Run this script once on the PolyU GPU server after cloning the repo.
# Prerequisites: Podman, podman-compose, NVIDIA Container Toolkit (CDI).
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INFRA_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_DIR="$(cd "${INFRA_DIR}/.." && pwd)"
COMPOSE_CMD="podman-compose -f ${INFRA_DIR}/docker-compose.yml -f ${INFRA_DIR}/docker-compose.polyu.yml"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

info()  { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
fail()  { echo -e "${RED}[FAIL]${NC}  $*"; exit 1; }

# ── Step 1: Check prerequisites ──────────────────────────────────────────────
info "Checking prerequisites..."

command -v podman >/dev/null 2>&1 || fail "podman not found. Install: sudo apt install -y podman"
ok "podman $(podman --version | head -1)"

command -v podman-compose >/dev/null 2>&1 || fail "podman-compose not found. Install: pip install podman-compose"
ok "podman-compose found"

# Check NVIDIA CDI
if nvidia-ctk cdi list 2>/dev/null | grep -q "nvidia.com/gpu"; then
    ok "NVIDIA CDI devices detected"
    nvidia-ctk cdi list 2>/dev/null | grep "nvidia.com/gpu" | while read -r line; do
        echo "       $line"
    done
else
    warn "No NVIDIA CDI devices found."
    echo "       Run: sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml"
    echo "       Then: nvidia-ctk cdi list"
    read -rp "Continue without GPU? (y/N) " answer
    [[ "${answer}" =~ ^[Yy]$ ]] || exit 1
fi

# Check GPU status
if command -v nvidia-smi >/dev/null 2>&1; then
    info "Current GPU status:"
    nvidia-smi --query-gpu=index,name,memory.used,memory.total \
        --format=csv,noheader,nounits 2>/dev/null | while IFS=',' read -r idx name used total; do
        if [[ "${total}" -gt 0 ]] 2>/dev/null; then
            pct=$((used * 100 / total))
        else
            pct=0
        fi
        echo "       GPU ${idx}: ${name} — ${used}/${total} MiB (${pct}% used)"
    done
fi

# ── Step 2: Environment file ─────────────────────────────────────────────────
info "Setting up environment..."

if [[ -f "${INFRA_DIR}/.env" ]]; then
    ok ".env already exists"
else
    if [[ -f "${INFRA_DIR}/.env.polyu.example" ]]; then
        cp "${INFRA_DIR}/.env.polyu.example" "${INFRA_DIR}/.env"
        ok "Copied .env.polyu.example -> .env"
    else
        cp "${INFRA_DIR}/.env.example" "${INFRA_DIR}/.env"
        ok "Copied .env.example -> .env"
    fi
    warn "IMPORTANT: Edit ${INFRA_DIR}/.env with production values before continuing."
    echo "       Required changes:"
    echo "         - POSTGRES_PASSWORD  (openssl rand -hex 24)"
    echo "         - APP_DB_PASSWORD    (openssl rand -hex 24)"
    echo "         - JWT_SECRET_KEY     (openssl rand -hex 32)"
    echo "         - PREMIUM_LLM_API_KEY (from OpenRouter)"
    echo ""
    read -rp "Press Enter after editing .env, or Ctrl+C to abort... "
fi

# Validate required env vars
set -a
source "${INFRA_DIR}/.env"
set +a

for var in POSTGRES_PASSWORD APP_DB_PASSWORD JWT_SECRET_KEY; do
    val="${!var:-}"
    if [[ -z "${val}" || "${val}" == CHANGE_ME* ]]; then
        fail "${var} is not set or still has placeholder value. Edit .env first."
    fi
done
ok "Required environment variables are set"

# ── Step 3: Build and start services ─────────────────────────────────────────
info "Building and starting services..."
cd "${INFRA_DIR}"

${COMPOSE_CMD} up -d --build

info "Waiting for services to start..."
sleep 10

# ── Step 4: Pull Ollama models ───────────────────────────────────────────────
info "Pulling Ollama models (this may take a while on first run)..."

MODELS=("${OLLAMA_DEFAULT_MODEL:-qwen3.5:9b}" "${EMBEDDING_MODEL:-nomic-embed-text}")

# Add vision model if set
if [[ -n "${VISION_MODEL:-}" ]]; then
    MODELS+=("${VISION_MODEL}")
fi

for model in "${MODELS[@]}"; do
    info "Pulling ${model}..."
    podman exec stf-ollama ollama pull "${model}" || warn "Failed to pull ${model}"
done

ok "Ollama models pulled"

# ── Step 5: Health checks ────────────────────────────────────────────────────
info "Running health checks..."

check_service() {
    local name="$1"
    local url="$2"
    local max_retries=12
    local retry=0

    while [[ ${retry} -lt ${max_retries} ]]; do
        if curl -sf "${url}" >/dev/null 2>&1; then
            ok "${name} is healthy"
            return 0
        fi
        retry=$((retry + 1))
        sleep 5
    done
    warn "${name} is not responding at ${url} after 60s"
    return 1
}

podman exec stf-postgres pg_isready -U "${POSTGRES_USER:-stf_user}" \
    -d "${POSTGRES_DB:-stf_diagnosis}" >/dev/null 2>&1 \
    && ok "PostgreSQL is ready" \
    || warn "PostgreSQL not ready"

check_service "Ollama"         "http://127.0.0.1:${OLLAMA_PORT:-11434}/api/version" || true
check_service "Diagnostic API" "http://127.0.0.1:${DIAGNOSTIC_API_PORT:-8000}/health" || true
check_service "Nginx"          "http://127.0.0.1:${NGINX_PORT:-80}/health" || true

# ── Step 6: Verify GPU inside Ollama ─────────────────────────────────────────
info "Verifying GPU access inside Ollama container..."
if podman exec stf-ollama nvidia-smi >/dev/null 2>&1; then
    ok "GPU is accessible inside Ollama container"
    podman exec stf-ollama nvidia-smi --query-gpu=index,name,memory.total \
        --format=csv,noheader 2>/dev/null | while read -r line; do
        echo "       ${line}"
    done
else
    warn "GPU not accessible inside Ollama container. Check CDI configuration."
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
info "========================================"
ok   "Setup complete!"
info "========================================"
echo ""
info "Access points:"
echo "       Web UI:  http://<server-ip>:${NGINX_PORT:-80}"
echo "       API:     http://<server-ip>:${NGINX_PORT:-80}/docs"
echo ""
info "Useful commands:"
echo "       Logs:    ${COMPOSE_CMD} logs -f"
echo "       Status:  ${COMPOSE_CMD} ps"
echo "       Stop:    ${COMPOSE_CMD} down"
echo "       Update:  bash ${SCRIPT_DIR}/polyu-deploy.sh"
