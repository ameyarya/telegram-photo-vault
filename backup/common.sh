# Shared setup for the backup scripts: reads ../config.env and sets DRIVE, STATE, TDL, CHANNEL, folders.
# Sourced, not run.
ROOT="${0:A:h:h}"
CONFIG="${VAULT_CONFIG:-$ROOT/config.env}"
[[ -f "$CONFIG" ]] || { echo "Missing $CONFIG (copy config.example.env and fill it in)"; exit 1; }
set -a; source "$CONFIG"; set +a

STATE="${STATE_DIR:-$ROOT/state}"; STATE="${STATE/#\~/$HOME}"
mkdir -p "$STATE"
CHANNEL="$TG_CHANNEL_ID"
TDL="${TDL:-$(command -v tdl || echo "$HOME/.local/bin/tdl")}"
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

[[ -d "$DRIVE" ]] || { echo "Drive not mounted at $DRIVE"; exit 1; }

# Top-level folders, in upload order. Empty FOLDERS = every top-level folder on the drive.
folders=("${(@s:|:)FOLDERS}")
folders=(${folders:#})  # drop empties
if (( ${#folders} == 0 )); then
  folders=()
  for d in "$DRIVE"/*(N/); do
    n="${d:t}"
    [[ "$n" == .* || "$n" == _* || "$n" == '$RECYCLE.BIN' || "$n" == "System Volume Information" ]] || folders+=("$n")
  done
fi
