"""Bring back Telegram-only photos after the index was rebuilt.

Files in a CLOUD_ONLY folder (config.env) live only in Telegram: they were removed from the Mac once safely uploaded,
so indexing the drive can't see them. This reads each one's small thumbnail and the first part of the file (enough for
the camera's date and details) from the Telegram channel, a few at a time, and adds it to the gallery. Resumable: it
only handles what isn't in the gallery yet. Runs inside the gallery server, which already holds the Telegram login.
Dates: the camera's date when the file start has one, else the day it was uploaded (listed under "Fix dates").
"""
import asyncio
import json
import os
import subprocess
import tempfile
import time
from datetime import datetime

from telethon.errors import FloodWaitError

import config
import tg
from build_index import album_and_name, thumb_path, valid_date
from db import connect

HEAD = 512 * 1024          # bytes read from the start of each file (the camera's date lives there)
BATCH = 50
PACE = float(os.environ.get("CLOUD_SEED_PACE", "0.4"))   # seconds between files: a gentle trickle, never a burst
LOG = config.STATE / "cloud-seed.log"


def log(msg):
    with open(LOG, "a") as f:
        f.write(time.strftime("%Y-%m-%d %H:%M:%S  ") + msg + "\n")


def todo():
    """(path, msg_id) of Telegram-only files in CLOUD_ONLY folders that aren't in the gallery yet."""
    if not config.CLOUD_ONLY:
        return []
    con = connect()
    try:
        have = {r[0] for r in con.execute("SELECT path FROM media")}
        return [(p, m) for p, m in con.execute("SELECT path, msg_id FROM tg_msgs ORDER BY msg_id")
                if p.split("/", 1)[0] in config.CLOUD_ONLY and p not in have
                and p.rsplit(".", 1)[-1].lower() in config.IMAGE_EXT | config.VIDEO_EXT]
    finally:
        con.close()


def exif_of(path):
    out = subprocess.run(["exiftool", "-j", "-fast", "-api", "QuickTimeUTC", "-d", "%Y-%m-%dT%H:%M:%S",
                          "-DateTimeOriginal", "-CreationDate", "-CreateDate", "-MediaCreateDate", "-ImageWidth#",
                          "-ImageHeight#", "-Duration#", "-Orientation#", "-Make", "-Model", "-LensModel", "-ISO#",
                          "-FNumber#", "-ExposureTime#", "-FocalLength#", "-GPSLatitude#", "-GPSLongitude#", str(path)],
                         capture_output=True, text=True).stdout
    try:
        return json.loads(out or "[]")[0]
    except (ValueError, IndexError):
        return {}


def add(rel, msg_id, size, e, when, thumb_bytes, attrs):
    """Insert the row and its thumbnail. Runs in a thread."""
    ext = rel.rsplit(".", 1)[-1].lower()
    kind = "video" if ext in config.VIDEO_EXT else "image"
    date = next((e[k] for k in ("DateTimeOriginal", "CreationDate", "CreateDate", "MediaCreateDate")
                 if valid_date(e.get(k))), None)
    src = "exif" if date else "file"
    date = (date or when)[:19]
    album, name = album_and_name(rel)
    side = 1 if e.get("Orientation") in (5, 6, 7, 8) else 0
    con = connect(timeout=120)
    try:
        con.execute("""INSERT OR IGNORE INTO media(path, album, name, kind, ext, taken_at, date_src, camera_at, size, mtime,
                       width, height, duration, orient, thumb, exif2, msg_id, make, model, lens, iso, fnumber, exposure,
                       focal, lat, lon)
                       VALUES(?,?,?,?,?,?,?,?,?,0,?,?,?,?,0,1,?,?,?,?,?,?,?,?,?,?)""",
                    (rel, album, name, kind, ext, date, src, date, size,
                     e.get("ImageWidth") or attrs.get("w"), e.get("ImageHeight") or attrs.get("h"),
                     e.get("Duration") or attrs.get("d"), side, msg_id, e.get("Make"), e.get("Model"),
                     e.get("LensModel"), e.get("ISO"), e.get("FNumber"), e.get("ExposureTime"), e.get("FocalLength"),
                     e.get("GPSLatitude"), e.get("GPSLongitude")))
        con.commit()
        mid = con.execute("SELECT id FROM media WHERE path = ?", (rel,)).fetchone()[0]
        status = 0
        if thumb_bytes:
            out = thumb_path(mid)
            out.parent.mkdir(parents=True, exist_ok=True)
            raw = out.with_suffix(".raw")
            raw.write_bytes(thumb_bytes)
            ok = subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", "70", "-Z", "400", str(raw),
                                 "--out", str(out)], capture_output=True).returncode == 0 and out.exists()
            raw.unlink(missing_ok=True)
            status = 1 if ok else 0          # 0: the gallery makes it from Telegram when someone looks at it
        con.execute("UPDATE media SET thumb = ? WHERE id = ?", (status, mid))
        con.commit()
    finally:
        con.close()


async def one(client, msg, rel, sem):
    async with sem:
        doc = msg.document
        thumb = None
        try:
            thumb = await client.download_media(msg, file=bytes, thumb=-1)
        except Exception:
            pass
        ext = rel.rsplit(".", 1)[-1].lower()
        with tempfile.TemporaryDirectory() as d:
            head = os.path.join(d, "head." + ext)
            got = 0
            with open(head, "wb") as f:
                async for chunk in client.iter_download(doc, request_size=HEAD, limit=1):
                    f.write(chunk)
                    got += len(chunk)
            e = await asyncio.to_thread(exif_of, head)
            if not thumb and doc.size <= HEAD and ext in config.IMAGE_EXT:
                thumb = open(head, "rb").read()                  # a small photo: it is its own thumbnail
        attrs = {}
        for a in doc.attributes:
            attrs.update({k: v for k, v in (("w", getattr(a, "w", None)), ("h", getattr(a, "h", None)),
                                            ("d", getattr(a, "duration", None))) if v})
        when = msg.date.astimezone().strftime("%Y-%m-%dT%H:%M:%S")
        await asyncio.to_thread(add, rel, msg.id, doc.size, e, when, thumb, attrs)
        await asyncio.sleep(PACE)


async def run(client, cfg, stop=None):
    """Add every missing Telegram-only file, a few at a time. Returns how many were added."""
    pending = todo()
    if not pending:
        return 0
    log(f"{len(pending):,} Telegram-only files to add to the gallery")
    channel = await tg.channel_entity(client, cfg)
    sem = asyncio.Semaphore(3)
    added = 0
    for i in range(0, len(pending), BATCH):
        if stop is not None and stop.is_set():
            break
        chunk = dict((m, p) for p, m in pending[i:i + BATCH])
        while True:
            try:
                msgs = await client.get_messages(channel, ids=list(chunk))
                break
            except FloodWaitError as e:
                log(f"Telegram asked to wait {e.seconds}s")
                await asyncio.sleep(e.seconds + 1)
        jobs = [one(client, m, chunk[m.id], sem) for m in msgs if m and m.document]
        for r in await asyncio.gather(*jobs, return_exceptions=True):
            if isinstance(r, FloodWaitError):
                log(f"Telegram asked to wait {r.seconds}s")
                await asyncio.sleep(r.seconds + 1)
            elif isinstance(r, Exception):
                log(f"skipped one: {r.__class__.__name__}: {r}")
            else:
                added += 1
        log(f"{min(i + BATCH, len(pending)):,}/{len(pending):,} done")
    return added
