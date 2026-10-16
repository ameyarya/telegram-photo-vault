#!/bin/zsh
# Started by upload.sh. Kills a tdl process that has sent under 5 MB in 10 minutes (a stalled upload), so the
# uploader moves on; that batch is retried in a later pass. Exits when the uploader does.
#   zsh watchdog.sh <uploader pid> <log file>
UPLOADER=$1 LOG=$2
WINDOW=${WATCHDOG_WINDOW:-600} MIN_BYTES=${WATCHDOG_MIN_BYTES:-5000000}
while kill -0 "$UPLOADER" 2>/dev/null; do
  p=$(pgrep -P "$UPLOADER" -f " up -c " | head -1)   # the running "tdl up" batch
  if [[ -z "$p" ]]; then sleep 10; continue; fi
  a=$(nettop -P -L 1 -J bytes_out -p "$p" 2>/dev/null | tail -1 | cut -d, -f2)
  sleep "$WINDOW"
  kill -0 "$p" 2>/dev/null || continue
  b=$(nettop -P -L 1 -J bytes_out -p "$p" 2>/dev/null | tail -1 | cut -d, -f2)
  if (( ${b:-0} - ${a:-0} < MIN_BYTES )); then
    echo "$(date '+%F %T')  watchdog: upload stalled (under $((MIN_BYTES / 1000000)) MB in $((WINDOW / 60)) min), restarting that batch later" >> "$LOG"
    kill "$p"
  fi
done
