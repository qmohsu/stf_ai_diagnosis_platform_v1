#!/usr/bin/env bash
# ============================================================================
# STF AI Diagnosis Platform - PolyU Server Deploy/Update
# Author: Li-Ta Hsu
# Date: March 2026
# ============================================================================
# Run this script on the PolyU GPU server to pull latest code and redeploy.
# Usage: bash infra/scripts/polyu-deploy.sh
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

# ── Step 1: Pull latest code ─────────────────────────────────────────────────
info "Pulling latest code..."
cd "${REPO_DIR}"

# Refuse to deploy with dirty working directory
if [[ -n "$(git status --porcelain)" ]]; then
    fail "Working directory not clean. Commit or stash changes before deploying."
fi

git pull --ff-only || fail "git pull failed. Resolve conflicts manually."
ok "Code updated to $(git rev-parse --short HEAD)"

# Source .env for port/user variables used in health checks
if [[ -f "${INFRA_DIR}/.env" ]]; then
    set -a
    source "${INFRA_DIR}/.env"
    set +a
fi

# ── Step 2: Rebuild and restart ──────────────────────────────────────────────
info "Rebuilding and restarting services..."
cd "${INFRA_DIR}"

${COMPOSE_CMD} up -d --build

# ── Step 3: Wait and health check ────────────────────────────────────────────
info "Waiting for services to stabilize..."
sleep 15

info "Health checks..."
FAILED=0

# PostgreSQL
if podman exec stf-postgres pg_isready -U "${POSTGRES_USER:-stf_user}" \
    -d "${POSTGRES_DB:-stf_diagnosis}" >/dev/null 2>&1; then
    ok "PostgreSQL"
else
    warn "PostgreSQL not ready"
    FAILED=1
fi

# Ollama
if curl -sf "http://127.0.0.1:${OLLAMA_PORT:-11434}/api/version" >/dev/null 2>&1; then
    ok "Ollama"
else
    warn "Ollama not responding"
    FAILED=1
fi

# Diagnostic API
if curl -sf "http://127.0.0.1:${DIAGNOSTIC_API_PORT:-8000}/health" >/dev/null 2>&1; then
    ok "Diagnostic API"
else
    warn "Diagnostic API not responding"
    FAILED=1
fi

# Nginx (external gateway)
if curl -sf "http://127.0.0.1:${NGINX_PORT:-80}/health" >/dev/null 2>&1; then
    ok "Nginx gateway"
else
    warn "Nginx not responding"
    FAILED=1
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
if [[ ${FAILED} -eq 0 ]]; then
    ok "Deployment successful! All services healthy."
else
    warn "Deployment completed with warnings. Check logs:"
    echo "       ${COMPOSE_CMD} logs --tail=50"
fi

info "Container status:"
${COMPOSE_CMD} ps
