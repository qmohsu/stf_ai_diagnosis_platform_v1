#!/usr/bin/env bash
# Installs / checks the daily backup + weekly check (PROD-15A) on the PolyU server.
#
#   bash stf_v3/ops/install_backup.sh            # roles + dirs + units + timer, then checks
#   bash stf_v3/ops/install_backup.sh --check    # checks only
#
# Checks (each fails the script): backup role shape (FM-16), passphrase
# file 600 (FM-48/50), backup dir 700 (FM-49), network share is a network
# file system (FM-22), timer enabled with Persistent + zone (FM-30/31),
# linger on (the timers must survive logout), weekly check timer (FM-47).
# Reports (never fails on): maintenance mode (count-only until
# ~/stf_v3_backups/maintenance.apply exists), last maintenance and weekly check.
set -euo pipefail
REPO_DIR="${REPO_DIR:-$HOME/stf_ai_diagnosis_platform_v1}"
OFFSITE="${STF_V3_BACKUP_OFFSITE_DIR:-/localnvme/stf_v3_backups}"
UNIT_DIR="$HOME/.config/systemd/user"
CHECK_ONLY="${1:-}"
UNITS="stf-v3-backup.service stf-v3-backup.timer stf-v3-backup-verify.service stf-v3-backup-verify.timer"
FAILS=0
say()  { echo "[backup] $*"; }
fail() { echo "[backup] FAIL  $*"; FAILS=$((FAILS+1)); }
ok()   { echo "[backup] PASS  $*"; }

if [ "$CHECK_ONLY" != "--check" ]; then
  bash "$REPO_DIR/stf_v3/scripts/db_roles.sh" >/dev/null
  python3 "$REPO_DIR/stf_v3/scripts/backup.py" init
  mkdir -p "$UNIT_DIR"
  for u in $UNITS; do
    sed -e "s|@HOME@|$HOME|g" -e "s|@REPO@|$REPO_DIR|g" -e "s|@OFFSITE@|$OFFSITE|g" \
      "$REPO_DIR/stf_v3/ops/$u" > "$UNIT_DIR/$u"
  done
  systemctl --user daemon-reload
  systemctl --user enable --now stf-v3-backup.timer >/dev/null 2>&1
  systemctl --user enable --now stf-v3-backup-verify.timer >/dev/null 2>&1
fi

role="$(bash "$REPO_DIR/stf_v3/scripts/db_roles.sh" 2>/dev/null | grep '^stf_v3_backup ' || true)"
[ "$role" = "stf_v3_backup login=true super=false limit=4 password=false" ] \
  && ok "backup role: read-all, no password, limit 4" || fail "backup role shape: '${role:-missing}'"
pp="$HOME/.config/stf/backup_passphrase"
[ -s "$pp" ] && [ "$(stat -c %a "$pp")" = "600" ] && ok "passphrase file present, mode 600" \
  || fail "passphrase file missing or not 600: $pp"
[ -d "$HOME/stf_v3_backups" ] && [ "$(stat -c %a "$HOME/stf_v3_backups")" = "700" ] \
  && ok "backup dir mode 700" || fail "backup dir missing or not 700"
fstype="$(timeout 15 findmnt -n -o FSTYPE --target "$(dirname "$OFFSITE")" 2>/dev/null || true)"
case "$fstype" in cifs|smb3|nfs|nfs4) ok "network share $OFFSITE ($fstype)";;
  *) fail "offsite dir parent is not a network share (fstype=${fstype:-none})";; esac
for u in $UNITS; do
  if cmp -s <(sed -e "s|@HOME@|$HOME|g" -e "s|@REPO@|$REPO_DIR|g" -e "s|@OFFSITE@|$OFFSITE|g" \
      "$REPO_DIR/stf_v3/ops/$u") "$UNIT_DIR/$u"; then ok "$u installed and current"
  else fail "$u missing or stale (re-run without --check)"; fi
done
systemctl --user is-enabled --quiet stf-v3-backup.timer && ok "timer enabled" || fail "timer not enabled"
systemctl --user is-enabled --quiet stf-v3-backup-verify.timer && ok "weekly check timer enabled" \
  || fail "weekly check timer not enabled"
next="$(systemctl --user list-timers stf-v3-backup.timer --no-pager 2>/dev/null | awk 'NR==2{print $1, $2, $3, $4}')"
[ -n "$next" ] && ok "next run: $next" || fail "timer has no next run"
linger="$(loginctl show-user "$USER" -p Linger 2>/dev/null | cut -d= -f2)"
[ "$linger" = "yes" ] && ok "linger enabled" || fail "linger=$linger: the timer stops with the last session"
[ -f "$HOME/stf_v3_backups/maintenance.apply" ] && say "INFO  maintenance: deletes (maintenance.apply present)" \
  || say "INFO  maintenance: count-only (touch ~/stf_v3_backups/maintenance.apply after reviewing the counts)"
python3 - "$HOME/stf_v3_backups/status.json" <<'PY' || true
import json, sys
try:
    s = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    raise SystemExit(0)
m, v = s.get("maintenance") or {}, s.get("last_verify") or {}
if m:
    a, q = m.get("archive") or {}, m.get("queue") or {}
    print(f"[backup] INFO  last maintenance {m.get('at', '?')[:16]} {m.get('mode')}: "
          f"archive candidates={a.get('candidates')} rows={a.get('rows')} deleted={a.get('deleted')}; "
          f"queue candidates={q.get('candidates')} deleted={q.get('deleted')}"
          + (f"; ERROR {m['error']}" if m.get("error") else "") + (f"; {m['skipped']}" if m.get("skipped") else ""))
if v:
    print(f"[backup] INFO  last weekly check {v.get('at', '?')[:16]}: {'ok' if v.get('ok') else 'FAILED ' + str(v.get('error'))}")
PY
if [ "$FAILS" -eq 0 ]; then say "ALL CHECKS PASS"; exit 0; fi
say "CHECKS FAILED ($FAILS)"; exit 1
