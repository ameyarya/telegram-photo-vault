"""Trash for duplicates you don't want: hidden from the gallery and moved to <drive>/.photo-vault-trash/<same path>.

The Telegram backup copy is never touched. Restore moves files back where they were. "Empty trash" is the only
permanent step, and it deletes just the trashed files from the drive (they stay hidden, and Telegram still has them).
A Live Photo's motion clip always goes (and comes back) with its photo.

Nothing waits for the drive: with it unplugged, trash, put back and empty trash take effect in the gallery right away and
the file changes are queued (drive_queue) and done the next time it's plugged in (sync_drive, run by the watcher). A
trashed photo still in the Mac's inbox is never copied to the drive, and emptying the trash deletes it from the Mac
once it's in Telegram, so as little as possible ends up on the drive.
"""
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import DRIVE, INBOX
from db import connect

router = APIRouter()
TRASH = ".photo-vault-trash"


class Ids(BaseModel):
    ids: list[int]


def _with_live_clips(con, ids):
    ids = list(dict.fromkeys(ids))
    if not ids:
        return []
    q = ",".join("?" * len(ids))
    clips = [r[0] for r in con.execute(f"SELECT live_id FROM media WHERE id IN ({q}) AND live_id IS NOT NULL", ids)]
    return ids + [c for c in clips if c not in ids]


def _paths(con, ids):
    q = ",".join("?" * len(ids))
    return {r["id"]: r["path"] for r in con.execute(f"SELECT id, path FROM media WHERE id IN ({q})", ids)}


def apply_pending(con=None):
    """Move files of items trashed while the drive was unplugged. Returns how many moved."""
    if not DRIVE.is_dir():
        return 0
    con = con or connect()
    rows = con.execute("""SELECT t.media_id, m.path FROM trash t JOIN media m ON m.id = t.media_id
                          WHERE t.moved = 0 AND t.emptied = 0""").fetchall()
    moved = 0
    for r in rows:
        src, dst = DRIVE / r["path"], DRIVE / TRASH / r["path"]
        if src.is_file():
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.rename(src, dst)          # same drive: instant, nothing copied
            moved += 1
        elif (INBOX / r["path"]).exists():
            continue                     # still on the Mac: it never goes to the drive (inbox.py skips it)
        con.execute("UPDATE trash SET moved = 1 WHERE media_id = ?", (r["media_id"],))
    con.commit()
    return moved


@router.post("/api/trash")
def trash(body: Ids):
    con = connect()
    ids = _with_live_clips(con, body.ids)
    ids = [i for i in ids if i in _paths(con, ids)] if ids else []
    con.executemany("INSERT OR IGNORE INTO trash(media_id) VALUES (?)", [(i,) for i in ids])
    con.commit()
    moved = apply_pending(con)
    return {"trashed": len(ids), "moved": moved, "drive": DRIVE.is_dir()}


@router.get("/api/trash")
def list_trash():
    import server
    con = connect()
    rows = con.execute(f"""SELECT {server.ITEM_COLS}, t.trashed_at, t.moved FROM media JOIN trash t ON t.media_id = media.id
                           WHERE t.emptied = 0 AND media.live_of IS NULL ORDER BY t.trashed_at DESC, media.id""").fetchall()
    size = con.execute("""SELECT coalesce(sum(m.size), 0) FROM trash t JOIN media m ON m.id = t.media_id
                          WHERE t.emptied = 0""").fetchone()[0]
    tracked = {r[0] for r in con.execute("SELECT m.path FROM trash t JOIN media m ON m.id = t.media_id WHERE t.emptied = 0")}
    others, other_bytes = _other_files(tracked)
    return {"items": [dict(r) for r in rows], "bytes": size + other_bytes, "other_files": len(others),
            "drive": DRIVE.is_dir()}


def _other_files(tracked):
    """Files in the trash folder that aren't gallery items (e.g. duplicates the gallery doesn't show): emptied too."""
    root, out, total = DRIVE / TRASH, [], 0
    if not root.is_dir():
        return out, 0
    for d, _, files in os.walk(root):
        for f in files:
            p = Path(d) / f
            rel = str(p.relative_to(root))
            if f.startswith("._") or f == ".DS_Store" or rel in tracked:
                continue
            out.append(p)
            total += p.stat().st_size
    return out, total


@router.post("/api/trash/restore")
def restore_from_trash(body: Ids):
    con = connect()
    ids = _with_live_clips(con, body.ids)
    q = ",".join("?" * len(ids)) or "NULL"
    rows = con.execute(f"""SELECT t.media_id, t.moved, t.emptied, m.path FROM trash t JOIN media m ON m.id = t.media_id
                           WHERE t.media_id IN ({q})""", ids).fetchall()
    if any(r["emptied"] for r in rows):
        raise HTTPException(409, "Some of these were already deleted from the drive; use Restore (from Telegram) instead")
    for r in rows:
        if r["moved"]:                   # back out of the drive's trash folder, now or once it's plugged in
            con.execute("INSERT OR REPLACE INTO drive_queue(path, op) VALUES (?, 'untrash')", (r["path"],))
        con.execute("DELETE FROM trash WHERE media_id = ?", (r["media_id"],))
    con.commit()
    sync_drive(con)
    return {"restored": len(rows)}


@router.post("/api/trash/empty")
def empty_trash():
    """Permanently delete the trashed files: from the Mac now (once they're in Telegram), from the drive now or when it's
    next plugged in. They stay hidden; Telegram keeps its copy."""
    con = connect()
    apply_pending(con)
    rows = con.execute("""SELECT t.media_id, m.path, m.size FROM trash t JOIN media m ON m.id = t.media_id
                          WHERE t.emptied = 0""").fetchall()
    for r in rows:
        con.execute("INSERT OR REPLACE INTO drive_queue(path, op, size) VALUES (?, 'delete', ?)", (r["path"], r["size"]))
        con.execute("UPDATE trash SET emptied = 1 WHERE media_id = ?", (r["media_id"],))
    con.commit()
    freed = 0
    if DRIVE.is_dir():
        others, _ = _other_files(set())
        for f in others:
            freed += f.stat().st_size
            f.unlink()
    done = sync_drive(con)
    freed += done["bytes"]
    return {"deleted": len(rows), "bytes": freed, "waiting": done["waiting"], "drive": DRIVE.is_dir()}


def sync_drive(con=None, log=None):
    """Do the queued file changes: deletes on the Mac (once in Telegram) and, with the drive plugged in, on the drive."""
    import inbox
    con = con or connect()
    apply_pending(con)
    mounted, backed_up, freed = DRIVE.is_dir(), inbox.backed_up(), 0
    for r in con.execute("SELECT path, op, size FROM drive_queue").fetchall():
        path, op, size = r["path"], r["op"], r["size"]
        if op == "untrash":
            if not mounted:
                continue
            src, dst = DRIVE / TRASH / path, DRIVE / path
            if src.is_file() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.rename(src, dst)
            con.execute("DELETE FROM drive_queue WHERE path = ?", (path,))
            continue
        local = INBOX / path
        if local.is_file() and path in backed_up:            # not in Telegram yet: delete it once it is
            freed += local.stat().st_size
            local.unlink()
        if mounted:
            for f in (DRIVE / TRASH / path, DRIVE / path):
                if f.is_file() and (size is None or f.stat().st_size == size):   # never a newer file of that name
                    freed += f.stat().st_size
                    f.unlink()
        if mounted and not local.exists():
            con.execute("DELETE FROM drive_queue WHERE path = ?", (path,))
    con.commit()
    root = DRIVE / TRASH
    for d, _, _ in sorted(os.walk(root), key=lambda x: -len(x[0])) if mounted and root.is_dir() else []:
        if d != str(root) and not os.listdir(d):
            os.rmdir(d)          # tidy empty folders inside the trash
    waiting = con.execute("SELECT count(*) FROM drive_queue").fetchone()[0]
    if log and freed:
        log(f"trash: deleted {freed / 1e6:.0f} MB of emptied-trash files" + (f"; {waiting} wait for the drive" if waiting else ""))
    return {"bytes": freed, "waiting": waiting}


def queued_paths():
    """Paths that must not be copied onto the drive: trashed, or waiting to be deleted."""
    import sqlite3
    from config import DB_PATH
    if not DB_PATH.exists():
        return set()
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=30)
    try:
        return ({r[0] for r in con.execute("SELECT m.path FROM trash t JOIN media m ON m.id = t.media_id WHERE t.emptied = 0")}
                | {r[0] for r in con.execute("SELECT path FROM drive_queue WHERE op = 'delete'")})
    except sqlite3.OperationalError:          # an index made before the trash existed
        return set()
    finally:
        con.close()
