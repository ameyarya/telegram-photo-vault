"""Trash for duplicates you don't want: hidden from the gallery and moved to <drive>/.photo-vault-trash/<same path>.

The Telegram backup copy is never touched. Restore moves files back where they were. "Empty trash" is the only
permanent step, and it deletes just the trashed files from the drive (they stay hidden, and Telegram still has them).
A Live Photo's motion clip always goes (and comes back) with its photo. If the drive is unplugged, items are hidden
right away and their files are moved the next time the drive is plugged in.
"""
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import DRIVE
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
    return {"items": [dict(r) for r in rows], "bytes": size, "drive": DRIVE.is_dir()}


@router.post("/api/trash/restore")
def restore_from_trash(body: Ids):
    con = connect()
    ids = _with_live_clips(con, body.ids)
    q = ",".join("?" * len(ids)) or "NULL"
    rows = con.execute(f"""SELECT t.media_id, t.moved, t.emptied, m.path FROM trash t JOIN media m ON m.id = t.media_id
                           WHERE t.media_id IN ({q})""", ids).fetchall()
    if any(r["emptied"] for r in rows):
        raise HTTPException(409, "Some of these were already deleted from the drive; use Restore (from Telegram) instead")
    if any(r["moved"] for r in rows) and not DRIVE.is_dir():
        raise HTTPException(409, "Plug in the drive to put these files back")
    for r in rows:
        if r["moved"]:
            src, dst = DRIVE / TRASH / r["path"], DRIVE / r["path"]
            if src.is_file() and not dst.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                os.rename(src, dst)
        con.execute("DELETE FROM trash WHERE media_id = ?", (r["media_id"],))
    con.commit()
    return {"restored": len(rows)}


@router.post("/api/trash/empty")
def empty_trash():
    """Permanently delete the trashed files from the drive. They stay hidden; Telegram keeps its copy."""
    if not DRIVE.is_dir():
        raise HTTPException(409, "Plug in the drive to empty the trash")
    con = connect()
    apply_pending(con)
    rows = con.execute("""SELECT t.media_id, m.path, m.size FROM trash t JOIN media m ON m.id = t.media_id
                          WHERE t.emptied = 0""").fetchall()
    freed = 0
    for r in rows:
        f = DRIVE / TRASH / r["path"]
        if f.is_file():
            freed += f.stat().st_size
            f.unlink()
        con.execute("UPDATE trash SET emptied = 1 WHERE media_id = ?", (r["media_id"],))
    con.commit()
    root = DRIVE / TRASH
    for d, _, _ in sorted(os.walk(root), key=lambda x: -len(x[0])) if root.is_dir() else []:
        if d != str(root) and not os.listdir(d):
            os.rmdir(d)          # tidy empty folders inside the trash
    return {"deleted": len(rows), "bytes": freed}
