#!/usr/bin/env bash
# Storage report for deploy_check.sh (PROD-15A T-26; FM-6 / FM-8 / FM-44 /
# FM-49).  Prints sizes; WARN lines only, never fails a deploy:
#   * a V3 volume whose creation time changed since the last run was
#     re-created (pruned?) -- its data is gone, restore from backup;
#   * a log / backup / archive directory that is not mode 700.
# The network share is never walked (FM-32).
#
#   bash stf_v3/scripts/storage_report.sh [backup root]
set -uo pipefail
bk_root="${1:-${STF_V3_BACKUP_ROOT:-$HOME/stf_v3_backups}}"
echo "INFO  storage (not a check; WARN lines only):"
vol_seen="${STF_V3_VOLUME_STATE:-$HOME/.config/stf/volume_created.tsv}"
mkdir -p "$(dirname "$vol_seen")" && touch "$vol_seen" && chmod 600 "$vol_seen"
for v in stf_v3_obd_logs stf_v3_manuals stf_v3_logs stf_v3_backup_state; do
  mp="$(podman volume inspect "$v" --format '{{.Mountpoint}}' 2>/dev/null)"
  if [ -z "$mp" ]; then echo "WARN  volume $v missing"; continue; fi
  created="$(podman volume inspect "$v" --format '{{.CreatedAt}}' 2>/dev/null)"
  echo "      volume $v  $(podman unshare du -sh "$mp" 2>/dev/null | cut -f1)"
  before="$(awk -F'\t' -v v="$v" '$1 == v {print $2}' "$vol_seen")"
  if [ -z "$before" ]; then
    printf '%s\t%s\n' "$v" "$created" >> "$vol_seen"
  elif [ "$before" != "$created" ]; then
    echo "WARN  volume $v was re-created ($before -> $created): its old data is gone (FM-8); restore from backup, then: sed -i '/^$v\t/d' $vol_seen"
  fi
done
for d in "$HOME/stf_v3_logs" "$bk_root" "$bk_root/archive"; do
  [ -d "$d" ] || continue
  echo "      dir $d  $(du -sh "$d" 2>/dev/null | cut -f1)"
  [ "$(stat -c %a "$d")" = "700" ] || echo "WARN  $d is not mode 700 (FM-49)"
done
