"""Daily copies of the gallery's index (favorites, albums, people names, categories ...) so it can't be lost.

The index is small and holds work you can't redo from the photos themselves. Once a day the watcher saves a copy
in three places, so tidying up one folder can't take them all: the state folder, the photo drive (when plugged in)
and ~/Library/Application Support/PhotoVault. The newest 7 are kept in each. If the index is ever missing or empty
while a copy has photos in it, the newest copy is put back automatically.

    python snapshots.py            # save a copy now
    python snapshots.py restore    # put the newest copy back (only if the index is empty)
"""
import json
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


def export_user_data(con):
    """What you made, by file path (not by id), so it can be put back even on a freshly built index."""
    def q(sql):
        return [list(r) for r in con.execute(sql)]
    return {
        "favorites": [r[0] for r in q("SELECT m.path FROM favorites f JOIN media m ON m.id = f.media_id")],
        "albums": [{"name": a[0], "cover": a[1], "items": [r[0] for r in q(
            f"SELECT m.path FROM user_album_items i JOIN media m ON m.id = i.media_id WHERE i.album_id = {a[2]}")]}
            for a in q("""SELECT a.name, (SELECT path FROM media WHERE id = a.cover_id), a.id FROM user_albums a""")],
        "trash": q("SELECT m.path, t.trashed_at, t.moved, t.emptied FROM trash t JOIN media m ON m.id = t.media_id"),
        "date_fixes": q("""SELECT m.path, m.taken_at, f.orig_taken_at, f.orig_src FROM date_fixes f
                           JOIN media m ON m.id = f.media_id"""),
        "folder_names": q("SELECT path, name FROM folder_names"),
        "settings": q("SELECT key, value FROM settings"),
    }


def import_user_data(con, data):
    """Put exported favorites, albums, trash, date fixes and folder names back (by path). Returns how many items."""
    ids = {r[0]: r[1] for r in con.execute("SELECT path, id FROM media")}
    n = 0
    for p in data.get("favorites", []):
        if p in ids:
            n += con.execute("INSERT OR IGNORE INTO favorites(media_id) VALUES (?)", (ids[p],)).rowcount
    for a in data.get("albums", []):
        cur = con.execute("INSERT INTO user_albums(name, cover_id) VALUES (?, ?)", (a["name"], ids.get(a.get("cover"))))
        for p in a["items"]:
            if p in ids:
                n += con.execute("INSERT OR IGNORE INTO user_album_items(album_id, media_id) VALUES (?, ?)",
                                 (cur.lastrowid, ids[p])).rowcount
    for p, at, moved, emptied in data.get("trash", []):
        if p in ids:
            n += con.execute("INSERT OR IGNORE INTO trash(media_id, trashed_at, moved, emptied) VALUES (?,?,?,?)",
                             (ids[p], at, moved, emptied)).rowcount
    for p, taken, orig, src in data.get("date_fixes", []):
        if p in ids:
            con.execute("INSERT OR IGNORE INTO date_fixes(media_id, orig_taken_at, orig_src) VALUES (?,?,?)", (ids[p], orig, src))
            n += con.execute("UPDATE media SET taken_at = ?, date_src = 'user' WHERE id = ?", (taken, ids[p])).rowcount
    for p, name in data.get("folder_names", []):
        n += con.execute("INSERT OR IGNORE INTO folder_names(path, name) VALUES (?,?)", (p, name)).rowcount
    for k, v in data.get("settings", []):
        con.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?,?)", (k, v))
    con.commit()
    return n


def restore_user_data(con, log=print):
    """After a rebuild: if nothing of yours is in the index but a saved copy has some, put it back."""
    if any(con.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone() for t in ("favorites", "user_albums", "trash", "folder_names")):
        return 0
    best = max((f for p in places() for f in p.glob("user-data-*.json")), key=lambda f: f.name, default=None)
    if best is None:
        return 0
    n = import_user_data(con, json.loads(best.read_text()))
    if n:
        log(f"put back {n:,} favorites, album items and other changes of yours from {best.name}")
    return n


def save(force=False, log=print):
    """Copy the index into each place (at most one per day each). Never saves an empty index."""
    if not config.DB_PATH.exists() or photos_in(config.DB_PATH) == 0:
        return 0
    name = f"index-{time.strftime('%Y%m%d')}.db"
    n = 0
    for p in places():
        try:
            p.mkdir(parents=True, exist_ok=True)
            if (p / name).exists() and not force and photos_in(p / name) >= photos_in(config.DB_PATH):
                continue                                     # today's copy is already as full as the index
            tmp = p / (name + ".part")
            tmp.unlink(missing_ok=True)
            _copy(config.DB_PATH, tmp)
            os.replace(tmp, p / name)
            con = sqlite3.connect(config.DB_PATH)
            try:
                (p / f"user-data-{name[6:14]}.json").write_text(json.dumps(export_user_data(con)))
            except sqlite3.Error as e:                      # the full copy above is what matters; this one is a bonus
                log(f"couldn't list your favorites and albums: {e}")
            finally:
                con.close()
            for pat in ("index-*.db", "user-data-*.json"):
                for old in sorted(p.glob(pat))[:-KEEP]:
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
