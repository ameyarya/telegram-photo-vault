"""Favorites and your own albums. Stored only in the local index: nothing here touches the drive or Telegram."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import connect

router = APIRouter()


class Ids(BaseModel):
    ids: list[int]


class Name(BaseModel):
    name: str


def _existing(con, ids):
    if not ids:
        return []
    rows = con.execute(f"SELECT id FROM media WHERE id IN ({','.join('?' * len(ids))})", ids).fetchall()
    return [r["id"] for r in rows]


# --- favorites ---
@router.post("/api/favorites")
def add_favorites(body: Ids):
    con = connect()
    ids = _existing(con, body.ids)
    con.executemany("INSERT OR IGNORE INTO favorites(media_id) VALUES (?)", [(i,) for i in ids])
    con.commit()
    return {"favorited": len(ids)}


@router.post("/api/favorites/remove")
def remove_favorites(body: Ids):
    con = connect()
    con.executemany("DELETE FROM favorites WHERE media_id = ?", [(i,) for i in body.ids])
    con.commit()
    return {"ok": True}


# --- your albums ---
@router.get("/api/user-albums")
def list_albums():
    rows = connect().execute("""
        SELECT a.id, a.name, a.created, count(i.media_id) n,
               (SELECT i2.media_id FROM user_album_items i2 JOIN media m ON m.id = i2.media_id
                WHERE i2.album_id = a.id AND m.thumb != -1 ORDER BY m.taken_at LIMIT 1) cover
        FROM user_albums a LEFT JOIN user_album_items i ON i.album_id = a.id
        GROUP BY a.id ORDER BY a.created DESC, a.id DESC""").fetchall()
    return [dict(r) for r in rows]


def _album(con, album_id):
    row = con.execute("SELECT * FROM user_albums WHERE id = ?", (album_id,)).fetchone()
    if not row:
        raise HTTPException(404, "album not found")
    return row


@router.post("/api/user-albums")
def create_album(body: Name):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "album name is empty")
    con = connect()
    cur = con.execute("INSERT INTO user_albums(name) VALUES (?)", (name[:200],))
    con.commit()
    return {"id": cur.lastrowid, "name": name[:200]}


@router.post("/api/user-albums/{album_id}/rename")
def rename_album(album_id: int, body: Name):
    con = connect()
    _album(con, album_id)
    if not body.name.strip():
        raise HTTPException(400, "album name is empty")
    con.execute("UPDATE user_albums SET name = ? WHERE id = ?", (body.name.strip()[:200], album_id))
    con.commit()
    return {"ok": True}


@router.post("/api/user-albums/{album_id}/delete")
def delete_album(album_id: int):
    """Removes the album only. The photos stay on the drive, in Telegram and in the gallery."""
    con = connect()
    _album(con, album_id)
    con.execute("DELETE FROM user_album_items WHERE album_id = ?", (album_id,))
    con.execute("DELETE FROM user_albums WHERE id = ?", (album_id,))
    con.commit()
    return {"ok": True}


@router.post("/api/user-albums/{album_id}/add")
def add_to_album(album_id: int, body: Ids):
    con = connect()
    _album(con, album_id)
    ids = _existing(con, body.ids)
    con.executemany("INSERT OR IGNORE INTO user_album_items(album_id, media_id) VALUES (?, ?)",
                    [(album_id, i) for i in ids])
    con.commit()
    return {"added": len(ids)}


@router.post("/api/user-albums/{album_id}/remove")
def remove_from_album(album_id: int, body: Ids):
    con = connect()
    _album(con, album_id)
    con.executemany("DELETE FROM user_album_items WHERE album_id = ? AND media_id = ?",
                    [(album_id, i) for i in body.ids])
    con.commit()
    return {"ok": True}


# ---------- folder names: what the gallery calls a folder (the folder on the drive keeps its name) ----------
@router.get("/api/folder-names")
def folder_names():
    return dict(connect().execute("SELECT path, name FROM folder_names").fetchall())


class FolderName(BaseModel):
    path: str
    name: str


@router.post("/api/folder-names")
def set_folder_name(body: FolderName):
    """Rename a folder in the gallery only. An empty name goes back to the folder's own name."""
    path, name = body.path.strip("/"), body.name.strip()[:100]
    con = connect()
    if not con.execute("SELECT 1 FROM media WHERE album = ? OR album LIKE ? LIMIT 1", (path, path + "/%")).fetchone():
        raise HTTPException(404, "No such folder")
    if name:
        con.execute("INSERT OR REPLACE INTO folder_names(path, name) VALUES (?, ?)", (path, name))
    else:
        con.execute("DELETE FROM folder_names WHERE path = ?", (path,))
    con.commit()
    return {"path": path, "name": name or None}
