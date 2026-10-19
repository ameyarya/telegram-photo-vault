"""Photo gallery over the Telegram backup channel.

Thumbnails and the index are local. Full-size photos and videos come from the drive when it is
plugged in, otherwise they are streamed from Telegram. Nothing here can change or delete anything on the
drive or in Telegram, except moving files you trash (see trash.py); other writes are local (favorites, albums, restores).
Run: ../.venv/bin/python serve.py   (https://photo-vault.local), or install_service.sh to keep it running.
"""
import asyncio
import mimetypes
import sqlite3
import os
import subprocess
import time
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from telethon.errors import FileReferenceExpiredError

import tg
from build_index import make_thumb, thumb_path
from config import BASE, BROWSER_IMAGE_EXT, CACHE, CACHE_MAX_BYTES, DRIVE, TELEGRAM, load_env
from db import connect
from search import VISIBLE, parse_query, selection

mimetypes.add_type("image/heic", ".heic")
mimetypes.add_type("image/heif", ".heif")
mimetypes.add_type("video/quicktime", ".mov")
REQ = 512 * 1024  # Telegram download chunk; offsets must be aligned to it

state = {"client": None, "cfg": load_env(), "msgs": {}, "last_sync": None, "tg_error": None}


async def sync_loop():
    while True:
        try:
            await asyncio.to_thread(upload.move_waiting)   # phone uploads made while the drive was unplugged
            await tg.sync(state["client"], state["cfg"])
            state["last_sync"], state["tg_error"] = time.time(), None
        except Exception as e:  # keep serving; show the error in /api/status
            state["tg_error"] = str(e)
        await asyncio.sleep(300)


@asynccontextmanager
async def lifespan(app):
    client, cfg = tg.client_from_env() if TELEGRAM else (None, state["cfg"])
    task = None
    try:  # files trashed while the drive was unplugged go to its trash folder now
        await asyncio.to_thread(trash.apply_pending)
    except Exception:
        pass
    if client:
        await client.connect()
        if await client.is_user_authorized():
            state["client"] = client
            task = asyncio.create_task(sync_loop())
        else:
            state["tg_error"] = "Telegram session not logged in. Run: ../.venv/bin/python tg.py login"
    elif TELEGRAM:
        state["tg_error"] = "No Telegram API credentials: fill in config.env, then run ../.venv/bin/python tg.py login"
    else:
        state["tg_error"] = "Telegram disabled"
    yield
    if task:
        task.cancel()
    if client:
        await client.disconnect()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)

import auth, backup, dates, library, ml, restore, trash, upload  # noqa: E402  (they import helpers defined in this module)
for _mod in (auth, dates, library, ml, restore, backup, trash, upload):
    app.include_router(_mod.router)


# --- who may use it: this Mac always; phones on the home Wi-Fi only when shared and signed in (see auth.py) ---
@app.middleware("http")
async def gatekeeper(request: Request, call_next):
    refused = await asyncio.to_thread(auth.gate, request)
    return refused or await call_next(request)


# --- index ---
@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/status")
def status(request: Request):
    con = connect()
    r = con.execute("""SELECT count(*) total, sum(msg_id IS NOT NULL) uploaded, sum(thumb=1) thumbs,
                       sum(date_src='file') undated FROM media""").fetchone()
    return {**dict(r), "drive": DRIVE.is_dir(), "telegram": state["client"] is not None,
            "telegram_error": state["tg_error"], "last_sync": state["last_sync"],
            "user": getattr(request.state, "user", None)}


@app.get("/api/albums")
def albums():
    con = connect()
    rows = con.execute(f"""
        SELECT album, count(*) n, sum(kind='video') videos, sum(msg_id IS NOT NULL) uploaded,
               min(taken_at) first, max(taken_at) last,
               (SELECT id FROM media c WHERE c.album = m.album AND c.thumb != -1 AND c.live_of IS NULL
                ORDER BY c.kind = 'video', c.taken_at LIMIT 1) cover
        FROM media m WHERE {VISIBLE} GROUP BY album ORDER BY album""").fetchall()
    return [dict(r) for r in rows]


ITEM_COLS = """id, name, album, kind, ext, taken_at, date_src, size, duration,
               CASE WHEN orient = 1 THEN height ELSE width END AS width,
               CASE WHEN orient = 1 THEN width ELSE height END AS height,
               thumb, msg_id IS NOT NULL AS cloud, id IN (SELECT media_id FROM favorites) AS fav, live_id"""


@app.get("/api/items")
def items(album: str = "", sub: bool = True, offset: int = 0, limit: int = 500, order: str = "asc",
          filter: str = "", q: str = "", user_album: int | None = None, person: int | None = None):
    direction = "DESC" if order == "desc" else "ASC"
    where, args = selection(album, sub, filter, q, user_album, person)
    con = connect()
    total, first, last = con.execute(f"SELECT count(*), min(taken_at), max(taken_at) FROM media WHERE {where}",
                                     args).fetchone()
    rows = con.execute(f"""SELECT {ITEM_COLS} FROM media WHERE {where}
                           ORDER BY taken_at {direction}, id {direction} LIMIT ? OFFSET ?""",
                       args + [min(limit, 2000), max(offset, 0)]).fetchall()
    # first/last cover the whole selection (oldest and newest), not just this page
    return {"total": total, "first": first, "last": last, "query": parse_query(q)[2] if q else "",
            "items": [dict(r) for r in rows]}


@app.get("/api/months")
def months(album: str = "", sub: bool = True, order: str = "asc", filter: str = "", q: str = "",
           user_album: int | None = None, person: int | None = None):
    """How many items each month has, in display order: lets the date scrubber jump straight to a month."""
    where, args = selection(album, sub, filter, q, user_album, person)
    rows = connect().execute(f"""SELECT substr(taken_at, 1, 7) m, count(*) n FROM media WHERE {where}
                                 GROUP BY m ORDER BY m {"DESC" if order == "desc" else "ASC"}""", args).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/item/{media_id}")
def item(media_id: int):
    """Everything known about one photo or video, for the info panel."""
    con = connect()
    row = con.execute(f"SELECT {ITEM_COLS}, path, make, model, lens, iso, fnumber, exposure, focal, lat, lon "
                      "FROM media WHERE id = ?", (media_id,)).fetchone()
    if not row:
        raise HTTPException(404, "not found")
    albums_in = con.execute("""SELECT a.id, a.name FROM user_albums a JOIN user_album_items i ON i.album_id = a.id
                               WHERE i.media_id = ? ORDER BY a.name""", (media_id,)).fetchall()
    people_in = con.execute("""SELECT DISTINCT p.id, p.name FROM faces f JOIN people p ON p.id = f.person
                               WHERE f.media_id = ? ORDER BY p.name""", (media_id,)).fetchall()
    fix = con.execute("SELECT orig_taken_at, orig_src FROM date_fixes WHERE media_id = ?", (media_id,)).fetchone()
    sug = dates.suggestions(con, [media_id]) if row["date_src"] == "file" else []
    return {**dict(row), "user_albums": [dict(a) for a in albums_in], "people": [dict(p) for p in people_in],
            "date_original": dict(fix) if fix else None, "date_suggestion": sug[0] if sug else None}


def get_row(media_id):
    row = connect().execute("SELECT * FROM media WHERE id=?", (media_id,)).fetchone()
    if not row:
        raise HTTPException(404, "not found")
    return row


thumb_slots = asyncio.Semaphore(4)


def mark_thumb(media_id, status):
    try:
        con = connect(timeout=1)
        con.execute("UPDATE media SET thumb=? WHERE id=?", (status, media_id))
        con.commit()
    except sqlite3.OperationalError:  # indexer busy writing; the file on disk is what matters
        pass


@app.get("/thumb/{media_id}")
async def thumb(media_id: int):
    """Serve the thumbnail, making it on the spot (from the drive) if the indexer hasn't yet."""
    p = thumb_path(media_id)
    if not p.exists():
        row = await asyncio.to_thread(get_row, media_id)
        if row["thumb"] == -1 or local_file(row) is None:
            raise HTTPException(404, "no thumbnail")
        async with thumb_slots:
            if not p.exists():
                _, status = await asyncio.to_thread(make_thumb, {"id": media_id, "path": row["path"], "kind": row["kind"]})
                await asyncio.to_thread(mark_thumb, media_id, status)
        if not p.exists():
            raise HTTPException(404, "no thumbnail")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=31536000, immutable"})


# --- originals: drive first, Telegram otherwise ---
def local_file(row):
    for p in (DRIVE / row["path"], DRIVE / ".photo-vault-trash" / row["path"]):  # trashed files can still be viewed
        if p.is_file():
            return p
    return None


async def tg_message(row, refresh=False):
    if state["client"] is None or row["msg_id"] is None:
        raise HTTPException(503, "Not available: drive unplugged and file not in Telegram yet"
                            if row["msg_id"] is None else "Telegram not connected")
    msg = state["msgs"].get(row["msg_id"])
    if msg is None or refresh:
        channel = await tg.channel_entity(state["client"], state["cfg"])
        msg = await state["client"].get_messages(channel, ids=row["msg_id"])
        if not msg or not msg.document:
            raise HTTPException(404, "Telegram message missing")
        if len(state["msgs"]) > 500:
            state["msgs"].clear()
        state["msgs"][row["msg_id"]] = msg
    return msg


async def tg_bytes(row, start, end):
    """Yield bytes [start, end] of the file from Telegram."""
    msg = await tg_message(row)
    aligned = start - start % REQ
    skip, remaining = start - aligned, end - start + 1
    for attempt in (1, 2):
        try:
            async for chunk in state["client"].iter_download(msg.document, offset=aligned, request_size=REQ):
                if skip:
                    chunk, skip = chunk[skip:], 0
                if len(chunk) >= remaining:
                    yield chunk[:remaining]
                    return
                yield chunk
                remaining -= len(chunk)
                aligned += REQ
            return
        except FileReferenceExpiredError:
            if attempt == 2:
                raise
            msg = await tg_message(row, refresh=True)


def parse_range(header, size):
    if not header or not header.startswith("bytes="):
        return None
    a, _, b = header[6:].split(",")[0].partition("-")
    if a == "":
        start, end = max(0, size - int(b)), size - 1
    else:
        start, end = int(a), min(int(b) if b else size - 1, size - 1)
    if start > end or start >= size:
        raise HTTPException(416, "bad range")
    return start, end


async def serve_file(row, request, download=False):
    media_type = mimetypes.guess_type(row["name"].split(" (part")[0])[0] or "application/octet-stream"
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, max-age=86400"}
    if download:
        name = os.path.basename(row["path"])
        headers["Content-Disposition"] = f"attachment; filename*=UTF-8''{quote(name)}"
    lf = local_file(row)
    if lf:
        return FileResponse(lf, media_type=media_type, headers=headers)  # handles Range itself
    size = row["size"]
    rng = parse_range(request.headers.get("range"), size)
    start, end = rng or (0, size - 1)
    headers["Content-Length"] = str(end - start + 1)
    if rng:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    await tg_message(row)  # fail early with a clear error instead of a broken stream
    return StreamingResponse(tg_bytes(row, start, end), status_code=206 if rng else 200,
                             media_type=media_type, headers=headers)


@app.get("/original/{media_id}")
async def original(media_id: int, request: Request, download: bool = False):
    return await serve_file(await asyncio.to_thread(get_row, media_id), request, download)


@app.get("/download")
async def download(request: Request, ids: str, name: str = "Photos"):
    """One photo or video as itself; several as one .zip (with Live Photos' motion clips), streamed as it's read.

    Files come from the drive when it's plugged in, otherwise from Telegram, so it works from a phone too."""
    wanted = [int(i) for i in ids.split(",") if i.strip().isdigit()][:5000]
    rows = await asyncio.to_thread(_download_rows, wanted)
    if not rows:
        raise HTTPException(404, "Nothing to download")
    if len(rows) == 1:
        return await serve_file(rows[0], request, download=True)
    unavailable = [r["name"] for r in rows if local_file(r) is None and (r["msg_id"] is None or state["client"] is None)]
    if unavailable:
        raise HTTPException(503, f"{len(unavailable)} of these can't be downloaded right now (drive unplugged and "
                                 f"not in Telegram yet, or Telegram offline), e.g. {unavailable[0]}")
    from datetime import datetime
    import zlib
    from stream_zip import ZIP_64, async_stream_zip

    async def chunks(row):
        lf = local_file(row)
        if lf:
            with open(lf, "rb") as f:
                while block := await asyncio.to_thread(f.read, 1 << 20):
                    yield block
        else:
            async for block in tg_bytes(row, 0, row["size"] - 1):
                yield block

    async def members():
        seen = set()
        for r in rows:
            base, dot, ext = os.path.basename(r["path"]).rpartition(".")
            fname, n = os.path.basename(r["path"]), 1
            while fname.lower() in seen:            # same name in different folders
                n += 1
                fname = f"{base} ({n}){dot}{ext}"
            seen.add(fname.lower())
            when = datetime.fromisoformat(r["taken_at"][:19]) if r["taken_at"] else datetime.now()
            yield fname, when, 0o644, ZIP_64, chunks(r)

    safe = " ".join("".join(c if c.isalnum() or c in "-_" else " " for c in name).split())[:60] or "Photos"
    return StreamingResponse(
        async_stream_zip(members(), get_compressobj=lambda: zlib.compressobj(0, zlib.DEFLATED, -15)),  # photos don't shrink
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(safe)}.zip", "Cache-Control": "no-store"})


def _download_rows(ids):
    if not ids:
        return []
    con = connect()
    q = ",".join("?" * len(ids))
    rows = {r["id"]: r for r in con.execute(f"SELECT * FROM media WHERE id IN ({q})", ids)}
    clips = {r["live_of"]: r for r in con.execute(f"SELECT * FROM media WHERE live_of IN ({q})", ids)}
    out = []
    for i in ids:
        if i in rows:
            out.append(rows[i])
            if i in clips and clips[i]["id"] not in rows:
                out.append(clips[i])
    return out


@app.get("/view/{media_id}")
async def view(media_id: int, request: Request):
    """Browser-viewable version: originals for videos and small JPEG/PNG; a 2048px JPEG for big photos and HEIC/RAW/TIFF."""
    row = await asyncio.to_thread(get_row, media_id)
    if row["kind"] == "video" or (row["ext"] in BROWSER_IMAGE_EXT and row["size"] < 3_000_000):
        return await serve_file(row, request)
    preview = CACHE / "preview" / f"{media_id}.jpg"
    if not preview.exists():
        preview.parent.mkdir(parents=True, exist_ok=True)
        src = local_file(row)
        if src is None:  # pull the original from Telegram into the cache first
            src = CACHE / "orig" / f"{media_id}.{row['ext']}"
            if not src.exists():
                src.parent.mkdir(parents=True, exist_ok=True)
                tmp = src.with_suffix(".part")
                with open(tmp, "wb") as f:
                    async for chunk in tg_bytes(row, 0, row["size"] - 1):
                        f.write(chunk)
                tmp.rename(src)
        proc = await asyncio.create_subprocess_exec(
            "sips", "-s", "format", "jpeg", "-s", "formatOptions", "85", "-Z", "2048", str(src), "--out", str(preview),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        await proc.wait()
        if not preview.exists():
            raise HTTPException(500, "could not convert this photo for viewing; use Download original")
        trim_cache()
    return FileResponse(preview, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


def trim_cache():
    files = [p for p in CACHE.rglob("*") if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    for p in sorted(files, key=lambda p: p.stat().st_atime):
        if total <= CACHE_MAX_BYTES:
            break
        total -= p.stat().st_size
        p.unlink(missing_ok=True)
