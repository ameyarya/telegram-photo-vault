#!/bin/zsh
# Auto-backup: runs whenever a drive is mounted, and every 30 minutes (launchd; turn on with install_autobackup.sh).
# If it's your photo drive (DRIVE in config.env), nothing is still being copied onto it (e.g. an Image Capture
# import), and no backup is running: rescan for new files, upload them, update the gallery index, analyse the new
# photos (if the optional models are installed), and show a notification when something was backed up.
# Safe to run by hand too.
# Paths/commands can be overridden with AUTOBACKUP_* variables (the tests use fakes).
ROOT="${0:A:h:h}"
CONFIG="${VAULT_CONFIG:-$ROOT/config.env}"
[[ -f "$CONFIG" ]] && { set -a; source "$CONFIG"; set +a; }
STATE=${AUTOBACKUP_STATE:-${STATE_DIR:-$ROOT/state}}; STATE="${STATE/#\~/$HOME}"
DRIVE=${AUTOBACKUP_DRIVE:-$DRIVE}
# Python + your bots: in the background macOS lets Homebrew's Python read the drive, but not zsh, find or tdl
UPLOAD=(${(z)AUTOBACKUP_UPLOAD:-$ROOT/.venv/bin/python $ROOT/backup/auto_upload.py})
INDEX=(${(z)AUTOBACKUP_INDEX:-$ROOT/.venv/bin/python $ROOT/gallery/build_index.py})
RUNNING_PATTERN=${AUTOBACKUP_RUNNING_PATTERN:-backup/upload.sh|bot_upload.py run|auto_upload.py}
INDEX_PATTERN=${AUTOBACKUP_INDEX_PATTERN:-build_index.py}
ML=(${(z)AUTOBACKUP_ML:-$ROOT/.venv/bin/python $ROOT/gallery/ml.py run})
MODELS=${GALLERY_MODELS:-$ROOT/gallery/models}
COOLDOWN=${AUTOBACKUP_COOLDOWN:-600}    # seconds; other drives mounting shouldn't trigger repeated rescans
SETTLE=${AUTOBACKUP_SETTLE:-60}         # seconds the drive must stay unchanged, so a half-copied file is never uploaded
mkdir -p "$STATE"
LOG=$STATE/autobackup.log
LOCK=$STATE/.autobackup.lock
LAST=$STATE/.autobackup.last

log() { echo "$(date '+%F %T')  $*" >> "$LOG"; }
notify() {
  [[ "${AUTOBACKUP_NOTIFY:-1}" == 1 ]] && osascript -e "display notification \"$1\" with title \"Photo Vault backup\"" 2>/dev/null
  log "$1"
}
used() {  # KB in use on the drive
  if [[ -n "$AUTOBACKUP_USED" ]]; then eval "$AUTOBACKUP_USED"; else df -k "$DRIVE" | awk 'NR==2 {print $3}'; fi
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
a=$(used); sleep $SETTLE; b=$(used)
if (( ${b:-0} - ${a:-0} > 1024 || ${a:-0} - ${b:-0} > 1024 )); then   # over 1 MB written or freed meanwhile
  log "waiting: files are still being copied to the drive; will look again in 30 min"; exit 0
fi

before=$(wc -l < "$STATE/done.log" 2>/dev/null || echo 0)
log "looking for new photos to back up"
caffeinate -is "${UPLOAD[@]}" --rescan >> "$STATE/upload.log" 2>&1
up=$?
after=$(wc -l < "$STATE/done.log" 2>/dev/null || echo 0)
new=$(( after - before ))
if pgrep -f "$INDEX_PATTERN" >/dev/null; then
  log "gallery index already updating; skipped (new files show up after the next index run)"
else
  "${INDEX[@]}" >> "$STATE/index.log" 2>&1 || log "gallery index update failed"
fi
if (( new > 0 )) && [[ -n "$AUTOBACKUP_ML" || -f "$MODELS/yunet.onnx" ]]; then
  if pgrep -f "${AUTOBACKUP_ML_PATTERN:-ml.py run}" >/dev/null; then
    log "photo analysis already running; it picks up the new photos next time"
  else
    (cd "$ROOT/gallery" && caffeinate -is "${ML[@]}") >> "$STATE/ml.log" 2>&1 || log "photo analysis failed"
  fi
fi
date +%s > "$LAST"
if (( up == 0 && new == 0 )); then
  log "nothing new"
elif (( up == 0 )); then
  notify "Backup finished: $new new file$([[ $new == 1 ]] || echo s) uploaded"
else
  notify "Backup stopped early ($new new files uploaded); it will continue next time"
fi
