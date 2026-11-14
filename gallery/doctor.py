"""Is everything in place? Prints what works, what doesn't and exactly how to fix it.

    ./vault doctor            (or: .venv/bin/python gallery/doctor.py)
    .venv/bin/python gallery/doctor.py --url     just print the address to open
"""
import os
import platform
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from config import BACKUP, DATA, DB_PATH, DRIVE, HOSTNAME, MODELS, ROOT, SESSION, TELEGRAM

LABEL = "local.photo-vault"
OK, BAD, OPT = "✓", "✗", "–"


def https_ready():
    from serve import https_ready as ready
    return ready()


def address():
    """Where to open the gallery on this Mac."""
    if https_ready():
        return f"https://{HOSTNAME}"
    from serve import LOCAL_PORT
    return f"http://localhost:{LOCAL_PORT}"


def listening(port):
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def service_loaded():
    """The background service is on, and it runs this copy of the project (not another one on the same Mac)."""
    plist = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    if not plist.exists() or str(ROOT / "gallery") not in plist.read_text():
        return False
    return subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{LABEL}"], capture_output=True).returncode == 0


def checks():
    """[(status, title, how to fix)]: status is OK, BAD (needed) or OPT (optional, not set up)."""
    out = []

    def add(ok, title, fix="", optional=False):
        out.append((OK if ok else (OPT if optional else BAD), title, "" if ok else fix))

    add(platform.system() == "Darwin", f"macOS ({platform.mac_ver()[0] or platform.system()})", "This app only runs on a Mac.")
    add(sys.version_info >= (3, 11), f"Python {platform.python_version()}", "Install Python 3.11 or newer: brew install python")
    add(sys.prefix != sys.base_prefix, "Project environment (.venv)", "Run ./setup.sh")
    for tool, why in (("ffmpeg", "video thumbnails"), ("ffprobe", "video details"), ("exiftool", "photo dates and camera details")):
        add(shutil.which(tool), f"{tool} ({why})", f"brew install {'ffmpeg' if tool != 'exiftool' else 'exiftool'}")
    add((ROOT / "config.env").exists(), "Settings file (config.env)", "Run ./setup.sh")
    have_drive = DRIVE.is_dir()
    add(have_drive, f"Photos folder: {DRIVE}", "Set DRIVE in config.env to your photos folder (or plug the drive in).")
    if have_drive:
        n = sum(1 for p in DRIVE.iterdir() if p.is_dir() and not p.name.startswith((".", "_")))
        add(n > 0, f"  {n} folder{'s' if n != 1 else ''} inside it (each shows as an album)",
            "The gallery shows the folders inside your photos folder as albums. Put photos in at least one subfolder.")
    indexed = 0
    if DB_PATH.exists():
        import sqlite3
        try:
            indexed = sqlite3.connect(DB_PATH).execute("SELECT count(*) FROM media").fetchone()[0]
        except sqlite3.Error:
            pass
    add(indexed > 0, f"Photos indexed: {indexed:,}", "Run: ./vault index")
    from serve import LOCAL_PORT
    up = listening(443) if https_ready() else listening(LOCAL_PORT)
    add(up, f"Gallery running at {address()}", "Run ./vault start (stays open) or ./vault service on (runs in the background)")
    add(service_loaded(), "Starts by itself at login (background service)", "./vault service on", optional=True)
    add(https_ready(), f"Padlock address https://{HOSTNAME}", "./vault https (asks for your Mac password once)", optional=True)
    if TELEGRAM and BACKUP:
        configured = bool(os.environ.get("TG_API_ID") and os.environ.get("TG_API_HASH") and os.environ.get("TG_CHANNEL_ID"))
        add(configured, "Telegram settings (TG_API_ID, TG_API_HASH, TG_CHANNEL_ID)", "./vault telegram")
        add(shutil.which("tdl"), "tdl (the uploader)", "Download it from https://github.com/iyear/tdl/releases and put it on your PATH")
        add(Path(SESSION + ".session").exists(), "Gallery signed in to Telegram", "./vault telegram")
    else:
        add(False, "Telegram backup (off: the gallery works without it)", "./vault telegram", optional=True)
    add((MODELS / "clip").exists(), "Smart search, people and similar shots (models downloaded)",
        "pip install -r requirements-ml.txt, then: cd gallery && ../.venv/bin/python ml.py download", optional=True)
    add(shutil.which("claude"), "Claude Code (only for the Sorting page)", "https://claude.com/claude-code, then run: claude auth login", optional=True)
    free = shutil.disk_usage(DATA if DATA.exists() else ROOT).free / 1e9
    add(free > 5, f"Free disk space for thumbnails: {free:,.0f} GB", "Free some space: thumbnails and caches live in " + str(DATA))
    return out


def main():
    if "--url" in sys.argv:
        print(address())
        return 0
    rows = checks()
    for status, title, fix in rows:
        print(f" {status}  {title}")
        if fix:
            print(f"      → {fix}")
    bad = [r for r in rows if r[0] == BAD]
    print()
    print("Everything needed is in place." if not bad else f"{len(bad)} thing{'s' if len(bad) != 1 else ''} to fix (marked ✗).")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
