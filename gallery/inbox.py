"""The inbox: new photos wait on the Mac (INBOX/<folder>/...), laid out like the drive, until it's plugged in.

Phones (Syncthing), the iCloud export and the gallery's upload page put files here, so they're backed up to Telegram and
shown in the gallery right away, drive or not. Their path ("<folder>/<name>") is the same as it will be on the drive,
so moving them there changes nothing for the gallery or the backup. Only files already in Telegram are moved, so an
upload never loses its file halfway.
"""
import os
import shutil
from pathlib import Path

from config import CLOUD_ONLY, DB_PATH, DRIVE, INBOX, STATE, THUMBS

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
    import trash
    done, moved, held = backed_up(), 0, trash.queued_paths()
    for rel in files():
        if rel.split("/", 1)[0] in CLOUD_ONLY:
            continue                                   # Telegram only: never on the drive
        if rel in held:
            continue                                   # trashed: kept off the drive (emptying the trash deletes it)
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
        d = Path(dirpath)
        if (d != INBOX and d.parent != INBOX and not os.listdir(d)
                and not any(part.startswith(".") for part in d.relative_to(INBOX).parts)):
            os.rmdir(d)          # tidy empty sub-folders; top folders and hidden ones stay (Syncthing's .stfolder marker:
                                 # without it Syncthing stops syncing that phone, to be safe)
    return moved


def free_cloud_only(log=print):
    """Delete Telegram-only files from the Mac once they're safe: uploaded, linked in the gallery (so it streams them
    from Telegram) and with a thumbnail made. Returns how many were removed."""
    if not CLOUD_ONLY or not DB_PATH.exists():
        return 0
    import sqlite3
    done, removed = backed_up(), 0
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    for rel in files():
        if rel.split("/", 1)[0] not in CLOUD_ONLY or rel not in done:
            continue
        row = con.execute("SELECT id, msg_id FROM media WHERE path = ?", (rel,)).fetchone()
        if not row or not row[1]:
            continue                                   # the gallery hasn't linked it to Telegram yet
        thumb = THUMBS / f"{row[0] % 256:02x}" / f"{row[0]}.jpg"
        if not thumb.exists():
            continue
        (INBOX / rel).unlink()
        removed += 1
    con.close()
    return removed


def mac_free_gb():
    st = os.statvfs(INBOX if INBOX.is_dir() else STATE)
    return st.f_bavail * st.f_frsize / 1024**3
