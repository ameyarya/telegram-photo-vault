"""Real-time backup: new photos reach Telegram and the gallery within minutes, drive plugged in or not.

Runs all the time (launchd; turn on with install_autobackup.sh). Every minute:
  * iCloud: if the Mac's Photos library has new photos (a quick look at its database), export them into the inbox
    (icloud_export.py; only when ICLOUD_EXPORT_FOLDER is set)
  * inbox: new files from phones (Syncthing), the gallery's upload page or iCloud are uploaded to Telegram, added to the
    gallery and analysed
  * drive plugged in: backed-up inbox files move onto it (inbox.py); and the whole drive is rescanned when it's just
    been plugged in and every hour, for files copied straight onto it (e.g. Image Capture) - once copying has stopped
  * the Mac's disk running low (under 10 GB, e.g. the drive unplugged for long): a notification, once a day

    .venv/bin/python backup/watcher.py           # run (what launchd does)
    .venv/bin/python backup/watcher.py --once    # one round, then exit
Commands and timings can be overridden with WATCH_* variables (the tests use fakes). Log: watcher.log in the state folder.
"""
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "gallery"))
import config  # noqa: E402  (reads config.env)
import inbox  # noqa: E402
import phone_import  # noqa: E402

PY = sys.executable


def env(name, default):
    return os.environ.get("WATCH_" + name) or default


STATE = config.STATE
UPLOAD = shlex.split(env("UPLOAD", f"{shlex.quote(PY)} {shlex.quote(str(HERE / 'auto_upload.py'))}"))
INDEX = shlex.split(env("INDEX", f"{shlex.quote(PY)} {shlex.quote(str(HERE.parent / 'gallery' / 'build_index.py'))}"))
ML = shlex.split(env("ML", f"{shlex.quote(PY)} {shlex.quote(str(HERE.parent / 'gallery' / 'ml.py'))} run"))
ICLOUD = shlex.split(env("ICLOUD", f"{shlex.quote(PY)} {shlex.quote(str(HERE / 'icloud_export.py'))}"))
ICLOUD_ON = bool(os.environ.get("ICLOUD_EXPORT_FOLDER", "").strip()) or bool(os.environ.get("WATCH_ICLOUD"))
LIBRARY = Path(os.environ.get("PHOTOS_LIBRARY") or "~/Pictures/Photos Library.photoslibrary").expanduser()
BUSY = env("BUSY_PATTERN", "upload.sh|bot_upload.py run|auto_upload.py|autobackup.py")
INTERVAL = int(env("INTERVAL", 60))
RESCAN = int(env("RESCAN", 3600))          # full drive rescan every hour while plugged in
SETTLE = int(env("SETTLE", 60))            # the drive must stay unchanged this long before a rescan
LOW_GB = float(env("LOW_GB", 10))
LOG = STATE / "watcher.log"
SPLIT_TARGET = int(env("SPLIT_TARGET", 1800000000))   # parts of ~1.8 GB, under Telegram's 2 GB limit


def log(msg):
    with open(LOG, "a") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S  ") + msg + "\n")


def notify(msg):
    if env("NOTIFY", "1") == "1":
        subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "Photo Vault"'], capture_output=True)
    log(msg)


def run(cmd, logname):
    caff = ["caffeinate", "-is"] if shutil.which("caffeinate") else []
    with open(STATE / logname, "a") as out:
        return subprocess.run(caff + cmd, stdout=out, stderr=subprocess.STDOUT, cwd=HERE.parent / "gallery").returncode


def lines(name):
    try:
        with open(STATE / name) as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def photos_marker():
    """(number of photos, newest added) in the Mac's Photos library, or None if it can't be read."""
    if os.environ.get("WATCH_PHOTOS_MARKER"):                      # tests
        return os.environ["WATCH_PHOTOS_MARKER"]
    db = LIBRARY / "database" / "Photos.sqlite"
    if not db.exists():
        return None
    try:
        with tempfile.TemporaryDirectory() as tmp:                # a copy: never lock or touch Photos' own database
            for suffix in ("", "-wal", "-shm"):
                if Path(str(db) + suffix).exists():
                    shutil.copy2(str(db) + suffix, os.path.join(tmp, "Photos.sqlite" + suffix))
            con = sqlite3.connect(os.path.join(tmp, "Photos.sqlite"))
            row = con.execute("SELECT count(*), max(ZADDEDDATE) FROM ZASSET WHERE ZTRASHEDSTATE = 0").fetchone()
            con.close()
        return f"{row[0]}:{row[1]}"
    except Exception as e:                                          # e.g. no Full Disk Access
        log(f"can't read the Photos library ({e.__class__.__name__}: {e})")
        stamp = STATE / ".photos-unreadable-notified"
        if not stamp.exists() or time.time() - stamp.stat().st_mtime > 86400:   # tell you, at most once a day
            stamp.touch()
            notify("Can't read the Photos library, so iPhone photos aren't being backed up. Give Python Full Disk "
                   "Access again (System Settings > Privacy & Security), e.g. after a Python update")
        return None


def drive_used_kb():
    if os.environ.get("WATCH_USED"):
        return int(subprocess.run(os.environ["WATCH_USED"], shell=True, capture_output=True, text=True).stdout.strip())
    st = os.statvfs(config.DRIVE)
    return (st.f_blocks - st.f_bfree) * st.f_frsize // 1024


def split_video(rel):
    """Cut a video too big for Telegram into parts that each play on their own (stream copy: no quality loss), in
    <drive>/_split/<same folder>/, like split_large.sh. Written to a hidden folder first, so no half part is uploaded."""
    src = config.DRIVE / rel
    folder, name = os.path.split(rel)
    base, ext = name.rsplit(".", 1)
    out = config.DRIVE / "_split" / folder
    if out.is_dir() and any(n.startswith(base + "_part") for n in os.listdir(out)):
        return 0                                                    # already split
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(src)],
                               capture_output=True, text=True).stdout.strip() or 0)
    if not dur:
        log(f"can't read {rel} to split it")
        return 0
    seg = max(1, int(dur * SPLIT_TARGET / src.stat().st_size))
    tmp = out / f".{base}.splitting"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    r = subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
                        "-map", "0:v", "-map", "0:a?", "-c", "copy", "-map_metadata", "0",
                        "-f", "segment", "-segment_time", str(seg), "-reset_timestamps", "1",
                        str(tmp / f"{base}_part%02d.{ext}")], capture_output=True, text=True)
    if r.returncode:
        shutil.rmtree(tmp, ignore_errors=True)
        log(f"splitting {rel} failed: {r.stderr.strip()[-200:]}")
        return 0
    parts = sorted(p for p in os.listdir(tmp) if not p.startswith("."))   # not macOS's ._ companions (exFAT)
    for p in parts:
        os.rename(tmp / p, out / p)
    shutil.rmtree(tmp, ignore_errors=True)
    (config.DRIVE / "_split" / ".complete").touch()                 # the uploader and gallery include _split/
    log(f"split {rel} into {len(parts)} parts")
    return len(parts)


def backup_new(reason):
    """Upload what's new, add it to the gallery, analyse it."""
    if subprocess.run(["pgrep", "-f", BUSY], capture_output=True).returncode == 0:
        log("an upload is already running; next round")
        return 0
    before = lines("done.log")
    log(f"backing up ({reason})")
    if any(f not in inbox.backed_up() for f in inbox.files()):
        run(INDEX, "index.log")     # new phone photos show in the gallery now, not after a long upload (e.g. a big import)
    up = run(UPLOAD, "upload.log")
    new = lines("done.log") - before
    run(INDEX, "index.log")
    if new > 0 and (os.environ.get("WATCH_ML") or (config.MODELS / "yunet.onnx").exists()):
        run(ML, "ml.log")
    if new:
        notify(f"Backed up {new} new photo{'' if new == 1 else 's'}" + ("" if up == 0 else " (more next round)"))
    return new


class Watcher:
    def __init__(self):
        self.marker = None if ICLOUD_ON else "off"
        self.was_mounted = False
        self.last_scan = 0
        self.warned = 0
        self.retry_at = 0
        self.hold_seen = None

    def round(self):
        STATE.mkdir(parents=True, exist_ok=True)
        self.check_phone_folders()
        if ICLOUD_ON:
            m = photos_marker()
            if m is not None and m != self.marker and time.time() >= self.retry_at:
                log("checking iCloud Photos for new photos" if self.marker is None else "new photos in iCloud Photos: exporting")
                if run(ICLOUD, "icloud-export.log") == 0:
                    self.marker = m
                else:
                    self.retry_at = time.time() + 900               # try again in 15 minutes
                    log("iCloud Photos export failed (see icloud-export.log); trying again in 15 min")
        imported = phone_import.run(log)
        if imported:
            log(f"{imported} new photos from an iPhone's sync")
        mounted = config.DRIVE.is_dir()
        scan = mounted and (not self.was_mounted or time.time() - self.last_scan >= RESCAN)
        if scan:
            a = drive_used_kb()
            time.sleep(SETTLE)
            if abs(drive_used_kb() - a) > 1024:
                log("files are still being copied to the drive; rescanning when that's done")
                scan = False
        self.was_mounted = mounted
        done = inbox.backed_up()
        waiting = [f for f in inbox.files() if f not in done
                   and (config.INBOX / f).stat().st_size <= inbox.MAX]   # too big for Telegram: split on the drive
        if scan or waiting:
            backup_new("drive plugged in or hourly rescan" if scan else f"{len(waiting)} new in the inbox")
            if scan:
                self.last_scan = time.time()
        if mounted:
            big = [f for f in inbox.files() if (config.INBOX / f).stat().st_size > inbox.MAX]
            moved = inbox.move_to_drive(log)
            if moved:
                log(f"moved {moved} files from the inbox onto the drive")
            split = [f for f in big if (config.DRIVE / f).exists() and f.rsplit(".", 1)[-1].lower() in config.VIDEO_EXT]
            if split and sum(split_video(f) for f in split):
                backup_new(f"{len(split)} big video{'' if len(split) == 1 else 's'} split into parts")
        freed = inbox.free_cloud_only(log)
        if freed:
            log(f"removed {freed} Telegram-only files from the Mac (safely uploaded)")
        hold = STATE / "upload-hold.json"
        if hold.exists() and hold.stat().st_mtime != self.hold_seen:      # tell you once about held files
            self.hold_seen = hold.stat().st_mtime
            import json
            h = json.loads(hold.read_text())
            notify(f"{h['files']:,} new files appeared on the drive ({', '.join(h['folders'])}); they wait on the "
                   "Backup page for your OK. Renamed a folder in Finder? Rename it back instead.")
        free = inbox.mac_free_gb()
        if free < LOW_GB and time.time() - self.warned > 86400:
            self.warned = time.time()
            notify(f"The Mac has only {free:.0f} GB free; plug in the photo drive so new photos can move onto it")


def check_phone_folders():
    """A phone's sync folder (Syncthing) needs its hidden .stfolder marker; without it Syncthing stops syncing that phone,
    to be safe. Put it back if it ever goes missing."""
    for d in [d for r in (config.INBOX, phone_import.ROOT) if r.is_dir() for d in r.iterdir()]:
        if d.is_dir() and (d / ".stignore").exists() and not (d / ".stfolder").exists():
            (d / ".stfolder").mkdir()
            notify(f"Fixed the phone sync for {d.name}: its Syncthing marker was missing")


Watcher.check_phone_folders = staticmethod(check_phone_folders)


def main():
    w = Watcher()
    log("watcher started")
    while True:
        try:
            w.round()
        except Exception as e:                                      # never die: launchd would restart us anyway
            log(f"round failed: {e.__class__.__name__}: {e}")
        if "--once" in sys.argv:
            return 0
        time.sleep(INTERVAL)


if __name__ == "__main__":
    sys.exit(main())
