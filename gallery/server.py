"""Read-only photo gallery over the Telegram backup channel.

Thumbnails and the index are local. Full-size photos and videos come from the drive when it is
plugged in, otherwise they are streamed from Telegram. There are no write or delete endpoints.
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

mimetypes.add_type("image/heic", ".heic")
mimetypes.add_type("image/heif", ".heif")
mimetypes.add_type("video/quicktime", ".mov")
REQ = 512 * 1024  # Telegram download chunk; offsets must be aligned to it

state = {"client": None, "cfg": load_env(), "msgs": {}, "last_sync": None, "tg_error": None}


async def sync_loop():
    while True:
        try:
            await tg.sync(state["client"], state["cfg"])
            state["last_sync"], state["tg_error"] = time.time(), None
        except Exception as e:  # keep serving; show the error in /api/status
            state["tg_error"] = str(e)
        await asyncio.sleep(300)


@asynccontextmanager
async def lifespan(app):
    client, cfg = tg.client_from_env() if TELEGRAM else (None, state["cfg"])
    task = None
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


@app.middleware("http")
async def local_only(request: Request, call_next):
    # macOS only allows ports below 1024 on the wildcard address, so "this Mac only" is enforced here
    if request.client is None or request.client.host not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
        return Response("This gallery is only available on the Mac it runs on.", status_code=403)
    return await call_next(request)


# --- index ---
@app.get("/")
def index():
    return FileResponse(BASE / "static" / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/status")
def status():
    con = connect()
    r = con.execute("""SELECT count(*) total, sum(msg_id IS NOT NULL) uploaded, sum(thumb=1) thumbs,
                       sum(date_src='file') undated FROM media""").fetchone()
    return {**dict(r), "drive": DRIVE.is_dir(), "telegram": state["client"] is not None,
            "telegram_error": state["tg_error"], "last_sync": state["last_sync"]}


@app.get("/api/albums")
def albums():
    con = connect()
    rows = con.execute("""
        SELECT album, count(*) n, sum(kind='video') videos, sum(msg_id IS NOT NULL) uploaded,
               min(taken_at) first, max(taken_at) last,
               (SELECT id FROM media c WHERE c.album = m.album AND c.thumb != -1
                ORDER BY c.kind = 'video', c.taken_at LIMIT 1) cover
        FROM media m GROUP BY album ORDER BY album""").fetchall()
    return [dict(r) for r in rows]


@app.get("/api/items")
def items(album: str = "", sub: bool = True, offset: int = 0, limit: int = 500):
    con = connect()
    if not album:
        where, args = "1", []
    elif sub:
        where, args = "(album = ? OR album LIKE ? ESCAPE '\\')", [album, album.replace("%", "\\%").replace("_", "\\_") + "/%"]
    else:
        where, args = "album = ?", [album]
    total = con.execute(f"SELECT count(*) FROM media WHERE {where}", args).fetchone()[0]
    rows = con.execute(f"""SELECT id, name, album, kind, ext, taken_at, date_src, size, duration,
                                  CASE WHEN orient = 1 THEN height ELSE width END AS width,
                                  CASE WHEN orient = 1 THEN width ELSE height END AS height,
                                  thumb, msg_id IS NOT NULL AS cloud
                           FROM media WHERE {where} ORDER BY taken_at, id LIMIT ? OFFSET ?""",
                       args + [min(limit, 2000), offset]).fetchall()
    return {"total": total, "items": [dict(r) for r in rows]}


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
    p = DRIVE / row["path"]
    return p if p.is_file() else None


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
