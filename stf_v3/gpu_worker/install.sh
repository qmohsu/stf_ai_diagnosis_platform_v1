#!/usr/bin/env bash
# Installs / updates the V3 host GPU worker on the PolyU server (PROD-06).
#
# The host has no root and only Python 3.10; V3 needs 3.11.  So we use a
# user-level `uv` to fetch a standalone 3.11, build a V3-only venv and
# install the V3 package + its [gpu] extra (PyMuPDF).  MinerU is NOT
# installed here: it is an external CLI in its own env (STF_V3_MINERU_BIN).
# The worker runs the SAME V3 code as the containers (editable install
# from the checkout), so "same task definition" is a mechanism, not a
# convention (FM-16).
#
#   bash stf_v3/gpu_worker/install.sh            # install/upgrade + self-checks
#   bash stf_v3/gpu_worker/install.sh --check    # self-checks only
#
# Self-checks (each fails the script): procrastinate version == pinned
# (FM-28), MinerU binary present and not under a scratch dir (FM-30),
# linger enabled for the user service (FM-19), volume readable/writable
# both ways (FM-6), env file present with mode 600 (FM-21).
set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/stf_ai_diagnosis_platform_v1}"
VENV="${STF_V3_VENV:-$HOME/venv-stf-v3}"
PY_VERSION="3.11"
PIN_PROCRASTINATE="3.9.0"
ENV_FILE="$REPO_DIR/infra/.env"
UNIT_SRC="$REPO_DIR/stf_v3/gpu_worker/stf-v3-gpu-worker.service"
UNIT_DST="$HOME/.config/systemd/user/stf-v3-gpu-worker.service"
VOLUME_DIR="${STF_V3_MANUAL_VOLUME:-$HOME/.local/share/containers/storage/volumes/stf_v3_manuals/_data}"
CHECK_ONLY="${1:-}"
FAILS=0

say()  { echo "[gpu-worker] $*"; }
fail() { echo "[gpu-worker] FAIL  $*"; FAILS=$((FAILS+1)); }
ok()   { echo "[gpu-worker] PASS  $*"; }

if [ "$CHECK_ONLY" != "--check" ]; then
  # 1. user-level uv
  if ! command -v uv >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/uv" ]; then
    say "installing uv (user-level)"
    curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null
  fi
  export PATH="$HOME/.local/bin:$PATH"
  # 2. standalone Python 3.11 + venv
  uv python install "$PY_VERSION" >/dev/null
  [ -d "$VENV" ] || uv venv --python "$PY_VERSION" "$VENV" >/dev/null
  # 3. V3 package (editable) + gpu extra
  uv pip install --python "$VENV/bin/python" -q -e "$REPO_DIR/stf_v3[gpu]"
  # 4. systemd user unit (EnvironmentFile = the server's single env file)
  mkdir -p "$(dirname "$UNIT_DST")"
  sed -e "s|@HOME@|$HOME|g" -e "s|@REPO@|$REPO_DIR|g" -e "s|@VENV@|$VENV|g" \
      -e "s|@VOLUME@|$VOLUME_DIR|g" "$UNIT_SRC" > "$UNIT_DST"
  systemctl --user daemon-reload
  systemctl --user enable stf-v3-gpu-worker.service >/dev/null 2>&1 || true
fi
export PATH="$HOME/.local/bin:$PATH"

# ---- self-checks ----
pyv="$("$VENV/bin/python" -c 'import sys; print(".".join(map(str, sys.version_info[:2])))' 2>/dev/null || echo none)"
[ "$pyv" = "$PY_VERSION" ] && ok "venv python $pyv" || fail "venv python is $pyv, expected $PY_VERSION"

pv="$("$VENV/bin/python" -c 'import procrastinate; print(procrastinate.__version__)' 2>/dev/null || echo none)"
[ "$pv" = "$PIN_PROCRASTINATE" ] && ok "procrastinate $pv == container pin" || fail "procrastinate $pv != $PIN_PROCRASTINATE (FM-28)"

"$VENV/bin/python" -c 'import fitz, yaml, stf_v3.knowledge.pipeline.build' 2>/dev/null && ok "pipeline deps import" || fail "pipeline deps (pymupdf/yaml) missing"

if [ -f "$ENV_FILE" ]; then
  mode="$(stat -c '%a' "$ENV_FILE")"
  [ "$mode" = "600" ] && ok "env file mode 600" || fail "env file mode $mode (want 600, FM-21)"
  set -a; . "$ENV_FILE"; set +a
else
  fail "env file $ENV_FILE missing"
fi
MINERU="${STF_V3_MINERU_BIN:-}"
if [ -z "$MINERU" ]; then fail "STF_V3_MINERU_BIN not set in $ENV_FILE (FM-30)";
elif [ ! -x "$MINERU" ]; then fail "MinerU binary $MINERU not executable";
elif echo "$MINERU" | grep -q "/bakeoff/"; then fail "MinerU path is under a scratch dir: $MINERU (FM-30)";
else ok "MinerU $("$MINERU" --version 2>/dev/null | tail -1) at $MINERU"; fi
[ -n "${OPENROUTER_API_KEY:-${STF_V3_OPENROUTER_API_KEY:-}}" ] && ok "summary API key present" || fail "OPENROUTER_API_KEY missing (FM-12)"
[ -n "${STF_V3_APP_DATABASE_URL:-}" ] && ok "database url present" || fail "STF_V3_APP_DATABASE_URL missing"

linger="$(loginctl show-user "$USER" -p Linger 2>/dev/null | cut -d= -f2)"
[ "$linger" = "yes" ] && ok "linger enabled" || fail "linger=$linger: user services die with the last SSH session (FM-19)"

# volume both ways (FM-6): host writes, container reads; container writes, host removes
if [ -d "$VOLUME_DIR" ]; then
  probe=".host_probe_$$"
  echo x > "$VOLUME_DIR/$probe"
  if podman exec stf-v3-api sh -c "cat /app/data/manuals/$probe" >/dev/null 2>&1; then ok "host→container read"; else fail "container cannot read host-written file (FM-6)"; fi
  rm -f "$VOLUME_DIR/$probe"
  if podman exec stf-v3-api sh -c "mkdir -p /app/data/manuals/.container_probe_$$/x" >/dev/null 2>&1 \
     && rm -rf "$VOLUME_DIR/.container_probe_$$"; then ok "container→host write+remove"; else fail "host cannot remove container-written dir (FM-6)"; fi
else
  fail "manual volume dir not found: $VOLUME_DIR (start stf-v3-api once first)"
fi

if [ "$FAILS" -eq 0 ]; then say "ALL CHECKS PASS"; exit 0; fi
say "CHECKS FAILED ($FAILS)"; exit 1
