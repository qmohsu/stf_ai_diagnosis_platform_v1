#!/usr/bin/env bash
# Installs / checks the deploy watch (#272) on the PolyU server.
#
#   bash stf_v3/ops/install_deploy_watch.sh            # copy + units + timer + first check
#   bash stf_v3/ops/install_deploy_watch.sh --check    # checks only
#
# The timer runs an installed COPY in ~/.local/share/stf_deploy_watch (700),
# not the checkout: the checkout is what it watches, and branch verification
# switches it.  SRC_DIR (default: the checkout's stf_v3/) lets a branch be
# installed from a copied tree without switching the checkout.
# Checks (each fails the script): copy and units current, timer enabled with
# a next run, linger on, state dir 755 with files 644 (readable by
# collaborators, writable only by us), last check younger than 15 minutes.
set -euo pipefail
REPO_DIR="${REPO_DIR:-$HOME/stf_ai_diagnosis_platform_v1}"
SRC_DIR="${SRC_DIR:-$REPO_DIR/stf_v3}"
LIB_DIR="$HOME/.local/share/stf_deploy_watch"
STATE_DIR="${STF_DEPLOY_WATCH_DIR:-$HOME/stf_v3_status}"
UNIT_DIR="$HOME/.config/systemd/user"
CHECK_ONLY="${1:-}"
UNITS="stf-v3-deploy-watch.service stf-v3-deploy-watch.timer"
FAILS=0
say()  { echo "[deploy-watch] $*"; }
fail() { echo "[deploy-watch] FAIL  $*"; FAILS=$((FAILS+1)); }
ok()   { echo "[deploy-watch] PASS  $*"; }
render() { sed -e "s|@REPO@|$REPO_DIR|g" -e "s|@LIB@|$LIB_DIR|g" -e "s|@STATE@|$STATE_DIR|g" "$SRC_DIR/ops/$1"; }

if [ "$CHECK_ONLY" != "--check" ]; then
  install -d -m 700 "$LIB_DIR"
  install -m 600 "$SRC_DIR/scripts/deploy_watch.py" "$LIB_DIR/deploy_watch.py"
  mkdir -p "$UNIT_DIR"
  for u in $UNITS; do render "$u" > "$UNIT_DIR/$u"; done
  systemctl --user daemon-reload
  systemctl --user enable --now stf-v3-deploy-watch.timer >/dev/null 2>&1
  (umask 022; STF_V3_REPO="$REPO_DIR" STF_DEPLOY_WATCH_DIR="$STATE_DIR" \
    python3 "$LIB_DIR/deploy_watch.py" check)
fi

cmp -s "$SRC_DIR/scripts/deploy_watch.py" "$LIB_DIR/deploy_watch.py" \
  && ok "installed copy matches $SRC_DIR/scripts/deploy_watch.py" \
  || fail "installed copy missing or stale (re-run without --check)"
for u in $UNITS; do
  if cmp -s <(render "$u") "$UNIT_DIR/$u"; then ok "$u installed and current"
  else fail "$u missing or stale (re-run without --check)"; fi
done
systemctl --user is-enabled --quiet stf-v3-deploy-watch.timer && ok "timer enabled" || fail "timer not enabled"
next="$(systemctl --user list-timers stf-v3-deploy-watch.timer --no-pager 2>/dev/null | awk 'NR==2{print $1, $2, $3, $4}')"
[ -n "$next" ] && ok "next run: $next" || fail "timer has no next run"
linger="$(loginctl show-user "$USER" -p Linger 2>/dev/null | cut -d= -f2)"
[ "$linger" = "yes" ] && ok "linger enabled" || fail "linger=$linger: the timer stops with the last session"
if [ -d "$STATE_DIR" ] && [ "$(stat -c %a "$STATE_DIR")" = "755" ]; then ok "state dir mode 755"
else fail "state dir missing or not 755: $STATE_DIR"; fi
bad="$(find "$STATE_DIR" -maxdepth 1 -type f ! -perm 644 2>/dev/null || true)"
[ -z "$bad" ] && ok "state files mode 644" || fail "state files not 644: $bad"
python3 - "$STATE_DIR/current.json" <<'PY' && ok "last check is recent" || fail "no check in the last 15 minutes"
import datetime as dt, json, sys
at = dt.datetime.fromisoformat(json.load(open(sys.argv[1]))["at"])
sys.exit(0 if dt.datetime.now(dt.timezone.utc) - at < dt.timedelta(minutes=15) else 1)
PY
STF_DEPLOY_WATCH_DIR="$STATE_DIR" python3 "$LIB_DIR/deploy_watch.py" report | sed -n '1,3p' | sed 's/^/[deploy-watch] INFO  /'
if [ "$FAILS" -eq 0 ]; then say "ALL CHECKS PASS"; exit 0; fi
say "CHECKS FAILED ($FAILS)"; exit 1
