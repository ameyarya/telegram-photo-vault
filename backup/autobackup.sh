#!/bin/zsh
# Auto-backup: runs whenever a drive is mounted (launchd StartOnMount; turn on with install_autobackup.sh).
# If it's your photo drive (DRIVE in config.env) and no backup is running: rescan for new files, upload them,
# update the gallery index, and show a notification. Safe to run by hand too.
# Paths/commands can be overridden with AUTOBACKUP_* variables (the tests use fakes).
ROOT="${0:A:h:h}"
CONFIG="${VAULT_CONFIG:-$ROOT/config.env}"
[[ -f "$CONFIG" ]] && { set -a; source "$CONFIG"; set +a; }
STATE=${AUTOBACKUP_STATE:-${STATE_DIR:-$ROOT/state}}; STATE="${STATE/#\~/$HOME}"
DRIVE=${AUTOBACKUP_DRIVE:-$DRIVE}
UPLOAD=(${(z)AUTOBACKUP_UPLOAD:-zsh $ROOT/backup/upload.sh})
INDEX=(${(z)AUTOBACKUP_INDEX:-$ROOT/.venv/bin/python $ROOT/gallery/build_index.py})
RUNNING_PATTERN=${AUTOBACKUP_RUNNING_PATTERN:-backup/upload.sh}
INDEX_PATTERN=${AUTOBACKUP_INDEX_PATTERN:-build_index.py}
COOLDOWN=${AUTOBACKUP_COOLDOWN:-3600}   # seconds; other drives mounting shouldn't trigger repeated rescans
mkdir -p "$STATE"
LOG=$STATE/autobackup.log
LOCK=$STATE/.autobackup.lock
LAST=$STATE/.autobackup.last

log() { echo "$(date '+%F %T')  $*" >> "$LOG"; }
notify() {
  [[ "${AUTOBACKUP_NOTIFY:-1}" == 1 ]] && osascript -e "display notification \"$1\" with title \"Photo Vault backup\"" 2>/dev/null
  log "$1"
}

sleep ${AUTOBACKUP_DELAY:-20}                      # let the drive finish mounting
[[ -n "$DRIVE" && -d "$DRIVE" ]] || exit 0         # some other drive
if [[ -f "$LAST" ]] && (( $(date +%s) - $(cat "$LAST") < COOLDOWN )); then
  log "skipped: last auto-backup was less than $((COOLDOWN / 60)) min ago"; exit 0
fi
if ! mkdir "$LOCK" 2>/dev/null; then               # another auto-backup is running (or died: take over)
  pid=$(cat "$LOCK/pid" 2>/dev/null)
  if [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null; then log "skipped: auto-backup already running"; exit 0; fi
  rm -rf "$LOCK"; mkdir "$LOCK" || exit 0
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT
if pgrep -f "$RUNNING_PATTERN" >/dev/null; then log "skipped: an upload is already running"; exit 0; fi

before=$(wc -l < "$STATE/done.log" 2>/dev/null || echo 0)
notify "Drive connected: looking for new photos to back up"
caffeinate -is "${UPLOAD[@]}" --rescan >> "$STATE/upload.log" 2>&1
up=$?
after=$(wc -l < "$STATE/done.log" 2>/dev/null || echo 0)
new=$(( after - before ))
if pgrep -f "$INDEX_PATTERN" >/dev/null; then
  log "gallery index already updating; skipped (new files show up after the next index run)"
else
  "${INDEX[@]}" >> "$STATE/index.log" 2>&1 || log "gallery index update failed"
fi
date +%s > "$LAST"
if (( up == 0 )); then
  notify "Backup finished: $new new file$([[ $new == 1 ]] || echo s) uploaded"
else
  notify "Backup stopped early ($new new files uploaded); it will continue next time"
fi
