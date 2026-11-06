#!/usr/bin/env bash
# ============================================================================
# STF AI Diagnosis Platform - PolyU Server Deploy/Update
# Author: Li-Ta Hsu
# Date: March 2026
# ============================================================================
# Legacy V1/V2 helper only. It does not build or cut over the independent
# V3 frontend. Follow CLAUDE.md "V3 frontend branch test and root cutover"
# for changes to the public site root.
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

warn "This helper updates the legacy stack only; deploy V3 frontend separately."

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

# Avoid reloading the V3-root nginx config before its separate frontend is
# actually serving on the loopback port.
if grep -q 'server 127.0.0.1:3002;' "${INFRA_DIR}/nginx/nginx.conf" && \
    ! curl -fsS http://127.0.0.1:3002/api/health >/dev/null; then
    fail "V3 frontend is not ready on 127.0.0.1:3002; deploy it before reloading nginx."
fi

# ── Step 2: Rebuild and restart ──────────────────────────────────────────────
info "Rebuilding and restarting services..."
cd "${INFRA_DIR}"

# Explicit service list keeps obd-ui stopped even if an older podman-compose
# ignores the PolyU legacy-ui profile. Podman 3.4 may retain old containers
# after --build; use CLAUDE.md's down/up procedure for changed images.
${COMPOSE_CMD} up -d --build postgres ollama diagnostic-api nginx
warn "Podman 3.4 may retain old containers after --build; verify image freshness and force-recreate changed services per CLAUDE.md."

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
if curl -sf "http://127.0.0.1:8001/health" >/dev/null 2>&1; then
    ok "Diagnostic API"
else
    warn "Diagnostic API not responding"
    FAILED=1
fi

# Nginx (external gateway)
if curl -sf "http://127.0.0.1:8080/health" >/dev/null 2>&1; then
    ok "Nginx gateway"
else
    warn "Nginx not responding"
    FAILED=1
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
if [[ ${FAILED} -eq 0 ]]; then
    ok "Legacy services responded. Verify image freshness; V3 frontend was not deployed by this helper."
else
    warn "Deployment completed with warnings. Check logs:"
    echo "       ${COMPOSE_CMD} logs --tail=50"
fi

info "Container status:"
${COMPOSE_CMD} ps
