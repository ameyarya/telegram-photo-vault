#!/bin/zsh
# One command to get going on a Mac:   ./setup.sh
# Installs what's missing (through Homebrew), prepares the project, then walks you through the rest.
# Safe to run again any time.
cd "${0:A:h}" || exit 1
say() { print -P "%F{green}==>%f $*"; }
die() { print -P "%F{red}✗%f $*"; exit 1; }

[[ "$(uname)" == "Darwin" ]] || die "This app runs on a Mac."

if ! command -v brew >/dev/null; then
  print "Homebrew (the Mac package manager) is needed first. Install it from https://brew.sh, then run ./setup.sh again."
  exit 1
fi

# A Python that's new enough (3.11+)
PYBIN=""
for c in python3.14 python3.13 python3.12 python3.11 python3; do
  p=$(command -v $c) || continue
  "$p" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null && { PYBIN=$p; break; }
done
if [[ -z "$PYBIN" ]]; then
  say "Installing Python"
  brew install python || die "Couldn't install Python. Try: brew install python"
  PYBIN=$(brew --prefix)/bin/python3
fi

# Tools the gallery calls: ffmpeg (videos), exiftool (dates, camera details)
need=()
for t in ffmpeg exiftool; do command -v $t >/dev/null || need+=($t); done
if (( ${#need} )); then say "Installing ${need[*]}"; brew install $need || die "brew install ${need[*]} failed"; fi

if [[ ! -x .venv/bin/python ]]; then say "Creating the project environment"; "$PYBIN" -m venv .venv || die "Couldn't create .venv"; fi
say "Installing the Python packages (a minute or two the first time)"
.venv/bin/pip install --quiet --upgrade pip && .venv/bin/pip install --quiet -r requirements.txt || die "pip install failed"

chmod +x vault
exec .venv/bin/python gallery/setup.py "$@"
