#!/bin/zsh
# Telegram keeps each file's bytes (so EXIF/video dates survive) but not the file system's created/modified dates.
# This saves them for every file in the backup, so they can be restored after downloading:
#   state/timestamps.tsv   path <TAB> size <TAB> created <TAB> modified
# Run it once before uploading (and after adding files). Upload the result yourself with
#   tdl up -c <channel id> -p state/timestamps.tsv      (when upload.sh is not running: tdl allows one session)
source "${0:A:h}/common.sh"
[[ -s "$STATE/all.txt" ]] || { echo "Run: zsh backup/upload.sh --list-only  first"; exit 1; }
cd "$DRIVE" || exit 1
{
  printf 'path\tsize\tcreated\tmodified\n'
  tr '\n' '\0' < "$STATE/all.txt" | xargs -0 stat -f "%N%t%z%t%SB%t%Sm" -t "%Y-%m-%dT%H:%M:%S"
} > "$STATE/timestamps.tsv"
echo "Saved $(( $(wc -l < "$STATE/timestamps.tsv") - 1 )) files' dates to $STATE/timestamps.tsv"
