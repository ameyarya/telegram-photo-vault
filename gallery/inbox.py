"""The inbox: new photos wait on the Mac (INBOX/<folder>/...), laid out like the drive, until it's plugged in.

Phones (Syncthing), the iCloud export and the gallery's upload page put files here, so they're backed up to Telegram and
shown in the gallery right away, drive or not. Their path ("<folder>/<name>") is the same as it will be on the drive,
so moving them there changes nothing for the gallery or the backup. Only files already in Telegram are moved, so an
upload never loses its file halfway.
"""
import os
import shutil
from pathlib import Path

from config import DRIVE, INBOX, STATE

MAX = int(os.environ.get("SPLIT_LIMIT") or 2 * 1024**3)   # Telegram's per-file limit: bigger videos are split on the drive


def backed_up():
    """Drive paths that are in Telegram (or skipped on purpose)."""
    out = set()
    for name in ("done.log", "skip.txt"):
        f = STATE / name
        if f.exists():
            out.update(f.read_text().splitlines())
    return out


def files():
    """Relative paths of the photos waiting in the inbox (hidden and temporary files left out)."""
    out = []
    if not INBOX.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(INBOX):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for fn in filenames:
            if not fn.startswith(".") and not fn.endswith((".part", ".tmp")):
                out.append(os.path.relpath(os.path.join(dirpath, fn), INBOX))
    return sorted(out)


def move_to_drive(log=print):
    """Move backed-up inbox files onto the drive under the same path. Returns how many moved."""
    if not DRIVE.is_dir() or not INBOX.is_dir():
        return 0
    done, moved = backed_up(), 0
    for rel in files():
        src, dst = INBOX / rel, DRIVE / rel
        if rel not in done and src.stat().st_size <= MAX:
            continue                                   # not in Telegram yet: the uploader may be reading it
                                                       # (too big to upload whole: moves now, split on the drive)
        if dst.exists():
            if dst.stat().st_size == src.stat().st_size:
                src.unlink()                           # already there
            else:
                log(f"inbox: {rel} is already on the drive as a different file; left in the inbox")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f".{dst.name}.moving")
        shutil.copy2(src, tmp)                         # another disk: copy (keeping its date), check, then rename
        if tmp.stat().st_size != src.stat().st_size:
            tmp.unlink()
            log(f"inbox: copying {rel} to the drive came out short; will try again")
            continue
        os.rename(tmp, dst)
        src.unlink()
        moved += 1
    for dirpath, dirnames, filenames in sorted(os.walk(INBOX), key=lambda x: -len(x[0])):
        if Path(dirpath) != INBOX and not os.listdir(dirpath) and Path(dirpath).parent != INBOX:
            os.rmdir(dirpath)                          # tidy empty sub-folders (top folders stay: Syncthing uses them)
    return moved


def mac_free_gb():
    st = os.statvfs(INBOX if INBOX.is_dir() else STATE)
    return st.f_bavail * st.f_frsize / 1024**3
