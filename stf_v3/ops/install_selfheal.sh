#!/usr/bin/env bash
# Installs / checks the self-heal timer (2026-11-21 outage) on the PolyU server.
#
#   bash stf_v3/ops/install_selfheal.sh            # copy + units + timer + first pass
#   bash stf_v3/ops/install_selfheal.sh --check    # checks only
#
# The timer runs an installed COPY in ~/.local/share/stf_selfheal (700), not
# the checkout: branch verification switches the checkout.  SRC_DIR (default:
# the checkout's stf_v3/) lets a branch be installed from a copied tree.
# Checks (each fails the script): copy and units current, timer enabled with
# a next run, linger on, last pass younger than 10 minutes.  A pause flag is
# printed as a warning: it is legitimate during maintenance, wrong afterwards.
set -euo pipefail
REPO_DIR="${REPO_DIR:-$HOME/stf_ai_diagnosis_platform_v1}"
SRC_DIR="${SRC_DIR:-$REPO_DIR/stf_v3}"
LIB_DIR="$HOME/.local/share/stf_selfheal"
STATE_DIR="${STF_SELFHEAL_DIR:-$HOME/stf_v3_status}"
UNIT_DIR="$HOME/.config/systemd/user"
CHECK_ONLY="${1:-}"
UNITS="stf-v3-selfheal.service stf-v3-selfheal.timer"
FAILS=0
say()  { echo "[selfheal] $*"; }
fail() { echo "[selfheal] FAIL  $*"; FAILS=$((FAILS+1)); }
ok()   { echo "[selfheal] PASS  $*"; }
render() { sed -e "s|@LIB@|$LIB_DIR|g" -e "s|@STATE@|$STATE_DIR|g" "$SRC_DIR/ops/$1"; }
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

if [ "$CHECK_ONLY" != "--check" ]; then
  install -d -m 700 "$LIB_DIR"
  install -m 600 "$SRC_DIR/scripts/selfheal.py" "$LIB_DIR/selfheal.py"
  mkdir -p "$UNIT_DIR"
  for u in $UNITS; do render "$u" > "$UNIT_DIR/$u"; done
  systemctl --user daemon-reload
  systemctl --user enable --now stf-v3-selfheal.timer >/dev/null 2>&1
  (umask 022; STF_SELFHEAL_DIR="$STATE_DIR" python3 "$LIB_DIR/selfheal.py" run >/dev/null)
fi

cmp -s "$SRC_DIR/scripts/selfheal.py" "$LIB_DIR/selfheal.py" \
  && ok "installed copy matches $SRC_DIR/scripts/selfheal.py" \
  || fail "installed copy missing or stale (re-run without --check)"
for u in $UNITS; do
  if cmp -s <(render "$u") "$UNIT_DIR/$u"; then ok "$u installed and current"
  else fail "$u missing or stale (re-run without --check)"; fi
done
systemctl --user is-enabled --quiet stf-v3-selfheal.timer && ok "timer enabled" || fail "timer not enabled"
next="$(systemctl --user list-timers stf-v3-selfheal.timer --no-pager 2>/dev/null | awk 'NR==2{print $1, $2, $3, $4}')"
[ -n "$next" ] && ok "next run: $next" || fail "timer has no next run"
linger="$(loginctl show-user "$USER" -p Linger 2>/dev/null | cut -d= -f2)"
[ "$linger" = "yes" ] && ok "linger enabled" || fail "linger=$linger: the timer stops with the last session"
python3 - "$STATE_DIR/selfheal.json" <<'PY' && ok "last pass is recent" || fail "no pass in the last 10 minutes"
import datetime as dt, json, sys
at = dt.datetime.fromisoformat(json.load(open(sys.argv[1]))["at"])
sys.exit(0 if dt.datetime.now(dt.timezone.utc) - at < dt.timedelta(minutes=10) else 1)
PY
[ -e "$STATE_DIR/selfheal.pause" ] && say "WARN  paused: nothing will be restarted until 'selfheal.py resume'"
STF_SELFHEAL_DIR="$STATE_DIR" python3 "$LIB_DIR/selfheal.py" status 2>/dev/null | sed -n '1,4p' | sed 's/^/[selfheal] INFO  /'
if [ "$FAILS" -eq 0 ]; then say "ALL CHECKS PASS"; exit 0; fi
say "CHECKS FAILED ($FAILS)"; exit 1
