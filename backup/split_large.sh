#!/bin/zsh
# Telegram's file limit is 2 GB (4 GB with Premium). This cuts every video over 2 GB into ~1.8 GB parts that
# each play on their own (stream copy: no re-encoding, no quality loss) under <drive>/_split/<same path>/.
# Originals are not touched. Needs free space on the drive equal to the size of those videos.
#   zsh backup/split_large.sh
source "${0:A:h}/common.sh"
OUT="$DRIVE/_split"
TARGET=${SPLIT_BYTES:-1800000000}
cd "$DRIVE" || exit 1
rm -f "$OUT/.complete"

for d in "${folders[@]}"; do
  [[ -d "$d" ]] || continue
  find "$d" -type f -size +2G \( -iname '*.mp4' -o -iname '*.mov' -o -iname '*.m4v' -o -iname '*.mkv' -o -iname '*.mts' \)
done | while IFS= read -r f; do
  size=$(stat -f %z "$f")
  dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$f")
  seg=$(echo "$dur * $TARGET / $size" | bc -l | cut -d. -f1)
  dir="$OUT/$(dirname "$f")"; base="${f:t:r}"; ext="${f:t:e}"
  mkdir -p "$dir"
  echo "== $f  ($((size / 1000000)) MB, ${dur%.*}s) -> parts of ${seg}s"
  ffmpeg -nostdin -hide_banner -loglevel error -y -i "$f" \
    -map '0:v' -map '0:a?' -c copy -map_metadata 0 \
    -f segment -segment_time "$seg" -reset_timestamps 1 \
    "$dir/${base}_part%02d.${ext}" || echo "!! FAILED: $f"
done

big=$(find "$OUT" -type f -size +1950M 2>/dev/null | wc -l | tr -d ' ')
(( big == 0 )) || echo "!! $big parts are still over 2 GB; lower SPLIT_BYTES and run again"
touch "$OUT/.complete"   # tells upload.sh the parts are ready
echo "Done: $(find "$OUT" -type f ! -name '.*' | wc -l | tr -d ' ') parts in $OUT"
