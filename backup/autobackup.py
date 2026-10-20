"""Auto-backup: runs once a day at 3 AM (or when the Mac wakes, if it slept through it), and when the drive is plugged in
if it hasn't run that day (launchd; turn on with install_autobackup.sh).

If it's your photo drive (DRIVE in config.env), nothing is still being copied onto it (e.g. an Image Capture import), and no backup is
already running: rescan for new files, upload them, update the gallery index, analyse the new photos (faces, smart
search), and show a notification when something was backed up. Safe to run by hand too.

It's Python, not a shell script, because in the background macOS lets this Python read ~/Documents and the drive,
but not zsh (or find, tdl ...). Every path/command can be overridden with AUTOBACKUP_* variables (the tests use fakes).
"""
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent          # backup/
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "gallery"))
import config  # noqa: E402  (reads config.env)
PY = sys.executable  # launchd starts Homebrew Python with the packages on PYTHONPATH; children inherit it


def env(name, default):
    return os.environ.get("AUTOBACKUP_" + name) or default


STATE = Path(env("STATE", config.STATE))
DRIVE = Path(env("DRIVE", config.DRIVE))
UPLOAD = shlex.split(env("UPLOAD", f"{shlex.quote(PY)} {shlex.quote(str(HERE / 'auto_upload.py'))}"))
INDEX = shlex.split(env("INDEX", f"{shlex.quote(PY)} {shlex.quote(str(ROOT / 'gallery' / 'build_index.py'))}"))
ML = shlex.split(env("ML", f"{shlex.quote(PY)} {shlex.quote(str(ROOT / 'gallery' / 'ml.py'))} run"))
RUNNING_PATTERN = env("RUNNING_PATTERN", "upload.sh|bot_upload.py run|auto_upload.py")
INDEX_PATTERN = env("INDEX_PATTERN", "build_index.py")
ML_PATTERN = env("ML_PATTERN", "ml.py run")
ICLOUD = shlex.split(env("ICLOUD", f"{shlex.quote(PY)} {shlex.quote(str(HERE / 'icloud_export.py'))}"))
ICLOUD_ON = bool(os.environ.get("ICLOUD_EXPORT_FOLDER", "").strip()) or bool(os.environ.get("AUTOBACKUP_ICLOUD"))
COOLDOWN = int(env("COOLDOWN", 20 * 3600))  # at most once a day: plugging drives in doesn't trigger extra runs
SETTLE = int(env("SETTLE", 60))        # seconds the drive must stay unchanged: never upload a half-copied file
MAX_WAIT = int(env("MAX_WAIT", 2 * 3600))  # while files are still being copied, keep checking for up to this long
RECHECK = int(env("RECHECK", 300))         # ... every 5 minutes
DELAY = int(env("DELAY", 20))          # let the drive finish mounting
LOG, LOCK, LAST = STATE / "autobackup.log", STATE / ".autobackup.lock", STATE / ".autobackup.last"


def log(msg):
    with open(LOG, "a") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S  ") + msg + "\n")


def notify(msg):
    if env("NOTIFY", "1") == "1":
        subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "Photo Vault backup"'],
                       capture_output=True)
    log(msg)


def running(pattern):
    return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0


def used_kb():
    if os.environ.get("AUTOBACKUP_USED"):
        return int(subprocess.run(os.environ["AUTOBACKUP_USED"], shell=True, capture_output=True, text=True).stdout.strip())
    st = os.statvfs(DRIVE)
    return (st.f_blocks - st.f_bfree) * st.f_frsize // 1024


def lines(path):
    try:
        with open(path) as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def take_lock():
    """True if we got the lock (taking over one left by a crash)."""
    try:
        LOCK.mkdir()
    except FileExistsError:
        try:
            pid = int((LOCK / "pid").read_text())
            os.kill(pid, 0)
            return False                              # really running
        except (OSError, ValueError):
            shutil.rmtree(LOCK, ignore_errors=True)
            try:
                LOCK.mkdir()
            except FileExistsError:
                return False
    (LOCK / "pid").write_text(str(os.getpid()))
    return True


def caffeinated(cmd):
    return ["caffeinate", "-is", *cmd] if shutil.which("caffeinate") else cmd


def main():
    time.sleep(DELAY)
    if not DRIVE.is_dir():
        return                                        # some other drive, or not plugged in
    STATE.mkdir(parents=True, exist_ok=True)
    try:
        if time.time() - int(LAST.read_text()) < COOLDOWN:
            log(f"skipped: already backed up in the last {COOLDOWN // 3600} h" if COOLDOWN >= 3600
                else f"skipped: last auto-backup was less than {COOLDOWN // 60} min ago")
            return
    except (OSError, ValueError):
        pass
    if not take_lock():
        log("skipped: auto-backup already running")
        return
    try:
        backup()
    finally:
        shutil.rmtree(LOCK, ignore_errors=True)


def backup():
    if running(RUNNING_PATTERN):
        log("skipped: an upload is already running")
        return
    if ICLOUD_ON:                                     # first bring new iPhone photos from iCloud Photos onto the drive
        log("bringing new photos from iCloud Photos onto the drive")
        with open(STATE / "icloud-export.log", "a") as out:
            if subprocess.run(caffeinated(ICLOUD), stdout=out, stderr=subprocess.STDOUT).returncode:
                log("iCloud Photos export failed (see icloud-export.log); backing up the rest anyway")
    waited = 0
    while True:
        a = used_kb()
        time.sleep(SETTLE)
        if abs(used_kb() - a) <= 1024:                # under 1 MB written or freed meanwhile: copying has stopped
            break
        if waited >= MAX_WAIT:
            log("waiting: files are still being copied to the drive; will try again at the next run")
            return
        if not waited:
            log("files are still being copied to the drive; checking again every few minutes")
        time.sleep(RECHECK)
        waited += RECHECK + SETTLE
    before = lines(STATE / "done.log")
    log("looking for new photos to back up")
    with open(STATE / "upload.log", "a") as out:
        up = subprocess.run(caffeinated(UPLOAD), stdout=out, stderr=subprocess.STDOUT).returncode
    new = lines(STATE / "done.log") - before
    if running(INDEX_PATTERN):
        log("gallery index already updating; skipped (new files show up after the next index run)")
    else:
        with open(STATE / "index.log", "a") as out:
            if subprocess.run(INDEX, stdout=out, stderr=subprocess.STDOUT).returncode:
                log("gallery index update failed")
    if new > 0 and (os.environ.get("AUTOBACKUP_ML") or (config.MODELS / "yunet.onnx").exists()):
        if running(ML_PATTERN):
            log("photo analysis already running; it picks up the new photos next time")
        else:
            with open(STATE / "ml.log", "a") as out:
                if subprocess.run(caffeinated(ML), stdout=out, stderr=subprocess.STDOUT, cwd=ROOT / "gallery").returncode:
                    log("photo analysis failed")
    LAST.write_text(str(int(time.time())))
    if up == 0 and new == 0:
        log("nothing new")
    elif up == 0:
        notify(f"Backup finished: {new} new file{'' if new == 1 else 's'} uploaded")
    else:
        notify(f"Backup stopped early ({new} new files uploaded); it will continue next time")


if __name__ == "__main__":
    sys.exit(main())
