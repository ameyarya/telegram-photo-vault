#!/bin/zsh
# Resumable backup of your drive to a private Telegram channel, using tdl (https://github.com/iyear/tdl).
#
#   caffeinate -is zsh backup/upload.sh            # upload (re-run any time: finished files are skipped)
#   zsh backup/upload.sh --rescan                  # re-scan the drive for new files first
#   zsh backup/upload.sh --list-only               # just build the file list (for find_duplicates.py)
#
# Files go up in batches of 20; each finished file is recorded in state/done.log. Batches that fail or stall
# are retried in up to 3 passes. Each upload's caption is its path on the drive, which the gallery uses.
source "${0:A:h}/common.sh"
[[ -n "$CHANNEL" ]] || { echo "Set TG_CHANNEL_ID in config.env"; exit 1; }
[[ -x "$TDL" ]] || { echo "tdl not found (install it and run: tdl login)"; exit 1; }

DONE="$STATE/done.log"      # relative paths successfully uploaded
FAIL="$STATE/failed.log"    # batches that errored (retried on the next pass)
SKIP="$STATE/skip.txt"      # exact duplicates (find_duplicates.py): their identical twin is uploaded instead
BATCH="${BATCH:-20}"
touch "$DONE" "$SKIP"
cd "$DRIVE" || exit 1

# Build the list in folder order: no hidden or empty files, no sidecars (.aae/.xml/.srt/.thm), only the originals
# inside macOS Photos libraries, nothing over 2 GB (split_large.sh makes playable parts in _split/ instead).
# Scanning a big drive is slow, so a list from the last 24 hours is reused unless --rescan is given.
if [[ "$1" != "--rescan" && "$1" != "--list-only" && -s "$STATE/all.txt" && -n $(find "$STATE/all.txt" -mtime -1) ]]; then
  echo "$(date '+%F %T')  reusing file list from $(stat -f %Sm "$STATE/all.txt")"
else
  echo "$(date '+%F %T')  scanning ${folders[*]} ..."
  {
    for d in "${folders[@]}" _split; do
      [[ -d "$d" ]] || continue
      [[ "$d" == _split && ! -f _split/.complete ]] && continue  # parts may still be being written
      find "$d" -type f ! -name '.*' ! -empty \
        \( ! -path '*.photoslibrary/*' -o -path '*.photoslibrary/originals/*' \) \
        ! -iname '*.aae' ! -iname '*.xml' ! -iname '*.srt' ! -iname '*.thm' \
        ! -size +2G | sort
    done
  } > "$STATE/all.txt"
fi
total=$(wc -l < "$STATE/all.txt")
echo "$(date '+%F %T')  $total files in the list"
[[ "$1" == "--list-only" ]] && exit 0

# The watchdog kills a tdl batch that sends almost nothing for 5 minutes (it happens); the batch is retried later.
zsh "$ROOT/backup/watchdog.sh" $$ "$STATE/upload.log" >/dev/null 2>&1 &
WATCHDOG=$!
trap 'kill $WATCHDOG 2>/dev/null' EXIT

batch=()
flush() {
  (( ${#batch} == 0 )) && return
  # the experimental bot uploader works from the other end of the list; drop anything it already did
  batch=("${(@f)$(printf '%s\n' "${batch[@]}" | grep -vxFf "$DONE")}")
  [[ -z "${batch[1]}" ]] && { batch=(); return; }
  # tdl splits -p values on commas unless they are CSV-quoted, so quote every path ("day 1, 2 & 3" folders)
  args=(); for f in "${batch[@]}"; do args+=(-p "\"$DRIVE/${f//\"/\"\"}\""); done
  if "$TDL" up -c "$CHANNEL" "${args[@]}" -l 4 \
       --caption "replace(FilePath, \"$DRIVE/\", \"\")" --disable-progress-ps >/dev/null 2>"$STATE/last_error.txt"; then
    printf '%s\n' "${batch[@]}" >> "$DONE"
  else
    echo "$(date '+%F %T')  batch failed (first: ${batch[1]})" | tee -a "$FAIL"
    [[ -d "$DRIVE" ]] || { echo "Drive disconnected, stopping."; exit 1; }
    sleep "${RETRY_SLEEP:-30}"
  fi
  echo "$(date '+%F %T')  $(wc -l < "$DONE")/$total done"
  batch=()
}

for pass in 1 2 3; do
  grep -vxFf "$DONE" "$STATE/all.txt" | grep -vxFf "$SKIP" > "$STATE/todo.txt"
  left=$(wc -l < "$STATE/todo.txt")
  echo "$(date '+%F %T')  pass $pass: $left files to upload"
  (( left == 0 )) && break
  while IFS= read -r f; do
    batch+=("$f")
    (( ${#batch} >= BATCH )) && flush
  done < "$STATE/todo.txt"
  flush
done

left=$(grep -vxFf "$DONE" "$STATE/all.txt" | grep -vxFf "$SKIP" | wc -l | tr -d ' ')
echo "$(date '+%F %T')  Finished. Remaining: $left (re-run to retry)"
