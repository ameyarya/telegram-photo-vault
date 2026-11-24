"""Daily copies of the gallery's index (favorites, albums, people names, categories ...) so it can't be lost.

The index is small and holds work you can't redo from the photos themselves. Once a day the watcher saves a copy
in three places, so tidying up one folder can't take them all: the state folder, the photo drive (when plugged in)
and ~/Library/Application Support/PhotoVault. The newest 7 are kept in each. If the index is ever missing or empty
while a copy has photos in it, the newest copy is put back automatically.

    python snapshots.py            # save a copy now
    python snapshots.py restore    # put the newest copy back (only if the index is empty)
"""
import os
import sqlite3
import sys
import time
from pathlib import Path

import config

KEEP = 7


def places():
    extra = os.environ.get("DB_BACKUP_DIRS")
    if extra is not None:                                    # tests
        return [Path(p) for p in extra.split(os.pathsep) if p]
    out = [config.STATE / "db-backups", Path("~/Library/Application Support/PhotoVault/db-backups").expanduser()]
    if config.DRIVE.is_dir():
        out.append(config.DRIVE / ".photo-vault-db-backups")
    return out


def photos_in(path):
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            have = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"media", "tg_msgs"} <= have:                     # not a full index (e.g. left over from a test)
                return 0
            return c.execute("SELECT count(*) FROM media").fetchone()[0]
        finally:
            c.close()
    except sqlite3.Error:
        return 0


def _copy(src, dst):
    s, d = sqlite3.connect(src), sqlite3.connect(dst)
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def save(force=False, log=print):
    """Copy the index into each place (at most one per day each). Never saves an empty index."""
    if not config.DB_PATH.exists() or photos_in(config.DB_PATH) == 0:
        return 0
    name = f"index-{time.strftime('%Y%m%d')}.db"
    n = 0
    for p in places():
        try:
            p.mkdir(parents=True, exist_ok=True)
            if (p / name).exists() and not force:
                continue
            tmp = p / (name + ".part")
            tmp.unlink(missing_ok=True)
            _copy(config.DB_PATH, tmp)
            os.replace(tmp, p / name)
            for old in sorted(p.glob("index-*.db"))[:-KEEP]:
                old.unlink()
            n += 1
        except (OSError, sqlite3.Error) as e:
            log(f"index copy to {p} failed: {e.__class__.__name__}: {e}")
    if n:
        log(f"saved a copy of the index ({photos_in(config.DB_PATH):,} photos) in {n} place{'s' if n != 1 else ''}")
    return n


def restore(log=print):
    """If the index is missing or empty, put back the newest copy that has photos. Returns its photo count or 0."""
    if config.DB_PATH.exists() and photos_in(config.DB_PATH) > 0:
        return 0
    best = None
    for p in places():
        for f in p.glob("index-*.db"):
            if best is None or f.name > best.name:
                if photos_in(f) > 0:
                    best = f
    if best is None:
        return 0
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    _copy(best, config.DB_PATH)
    n = photos_in(config.DB_PATH)
    log(f"the index was empty: restored it from {best} ({n:,} photos)")
    return n


if __name__ == "__main__":
    if sys.argv[1:] == ["restore"]:
        print("restored" if restore() else "nothing to restore (the index has photos, or no copy exists)")
    else:
        print(f"saved in {save(force=True)} place(s)")
