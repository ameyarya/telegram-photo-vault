"""Upload new files without a Terminal: what autobackup.sh runs in the background.

macOS only lets programs it has been given access to read an external drive. In the background (launchd), Python
from Homebrew usually has it, but zsh, find and tdl don't, and get "Operation not permitted". So this lists the drive
in Python and uploads with your bots (bot_upload.py, also Python), all of them at once. It uses the same rules and
the same done.log / skip.txt / all.txt as upload.sh, so the two can be mixed.

    .venv/bin/python backup/auto_upload.py            # list the drive, upload everything new (exit 0 when nothing's left)
    .venv/bin/python backup/auto_upload.py --list     # only rebuild all.txt
"""
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "gallery"))
import config  # noqa: E402  (reads config.env)

DRIVE, STATE = config.DRIVE, config.STATE
SIDECARS = (".aae", ".xml", ".srt", ".thm")
MAX = 2 * 1024**3  # bigger files are split into playable parts in _split/ (split_large.sh)


def list_files():
    """Relative paths to back up, in folder order, like upload.sh's find."""
    out = []
    for top in config.folders():
        if not (DRIVE / top).is_dir() or (top == "_split" and not (DRIVE / "_split/.complete").exists()):
            continue  # missing, or parts still being written
        found = []
        for dirpath, dirnames, filenames in os.walk(DRIVE / top):
            rel_dir = os.path.relpath(dirpath, DRIVE)
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            if ".photoslibrary" in rel_dir and ".photoslibrary/originals" not in rel_dir:
                continue  # a macOS Photos library: only its originals are photos
            for fn in filenames:
                if fn.startswith(".") or fn.lower().endswith(SIDECARS):
                    continue
                try:
                    size = os.stat(os.path.join(dirpath, fn)).st_size
                except OSError:
                    continue
                if 0 < size <= MAX:
                    found.append(os.path.join(rel_dir, fn))
        out += sorted(found)
    return out


def read_lines(name):
    p = STATE / name
    return set(p.read_text().splitlines()) if p.exists() else set()


def remaining(files):
    done, skip = read_lines("done.log"), read_lines("skip.txt")
    return [f for f in files if f not in done and f not in skip]


def bot_numbers():
    return [i for i in range(1, 10)
            if os.environ.get("TG_BOT_TOKEN" if i == 1 else f"TG_BOT_TOKEN_{i}", "").strip()]


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S  ") + msg, flush=True)


def main():
    if not DRIVE.is_dir():
        log("drive not plugged in")
        return 1
    files = list_files()
    if not files:
        log("found no files on the drive: not touching the list (no access to the drive?)")
        return 1
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = STATE / "all.txt.tmp"
    tmp.write_text("".join(f + "\n" for f in files))
    tmp.replace(STATE / "all.txt")
    todo = remaining(files)
    log(f"{len(files)} files on the drive, {len(todo)} to upload")
    if "--list" in sys.argv or not todo:
        return 0
    bots = bot_numbers()
    if not bots:
        log("no bot tokens in config.env: add a bot (see README), or run upload.sh from Terminal")
        return 1
    env = {**os.environ, "BOT_MARGIN": "0"}  # no account uploader running alongside: do every file
    procs = [subprocess.Popen([sys.executable, str(HERE / "bot_upload.py"), "run", "8", str(i)], env=env)
             for i in bots[:max(1, min(len(bots), len(todo)))]]
    for p in procs:
        p.wait()
    left = remaining(files)
    log(f"done: {len(todo) - len(left)} uploaded, {len(left)} left")
    return 0 if not left else 1


if __name__ == "__main__":
    sys.exit(main())
