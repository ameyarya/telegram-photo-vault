"""Back up a phone from its browser: photos and videos go into that person's own folder on the drive.

    PUT /api/upload?name=IMG_1.jpg&size=123&modified=1700000000000    (the file is the request body)

* Each account has its own top-level folder (users.folder, default "<name>-phone"; this Mac: "mac-uploads").
* Already there (same name and size in their folder): skipped, so picking everything again only sends what's new.
* Written to a .part file and renamed when complete: a half-sent file never shows up. Never overwrites: a different
  file with a taken name becomes "name (2).ext". The file keeps the phone's modified time.
* Drive unplugged: files wait in the Mac's inbox (INBOX/<folder>/), are backed up from there, and move to the drive
  when it's plugged in.
After a batch the gallery indexes the new files; auto-backup then sends them to Telegram like anything else.
"""
import asyncio
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from config import BASE, DRIVE, IMAGE_EXT, INBOX, VIDEO_EXT
from db import connect

router = APIRouter()
WAITING = INBOX   # drive unplugged: uploads wait in the inbox (inbox.py moves them over once they're backed up)
MAX = 2 * 1024**3  # Telegram's limit; bigger files would need splitting (split_large.sh)


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "phone"


def folder_for(user):
    if user.get("local"):
        return "mac-uploads"
    r = connect().execute("SELECT folder FROM users WHERE id = ?", (user["id"],)).fetchone()
    return (r and r["folder"]) or f"{slug(user['name'])}-phone"


def clean_name(name):
    name = os.path.basename(name.replace("\\", "/")).strip()
    name = re.sub(r'[\x00-\x1f<>:"|?*]', "_", name).lstrip(".")
    if not name or "." not in name:
        raise HTTPException(400, "Bad file name")
    ext = name.rsplit(".", 1)[1].lower()
    if ext not in IMAGE_EXT | VIDEO_EXT:
        raise HTTPException(415, f"Not a photo or video: {name}")
    return name


def root():
    """Where uploads go right now: the drive, or this Mac until it's plugged in."""
    return DRIVE if DRIVE.is_dir() else WAITING


def _listing(folder):
    d = root() / folder
    names = {n.lower(): n for n in os.listdir(d)} if d.is_dir() else {}
    if root() is DRIVE and (WAITING / folder).is_dir():
        names.update({n.lower(): n for n in os.listdir(WAITING / folder)})
    return names


def _existing(folder, name, size, names=None):
    """'same' if this file is already there (by name and size), else a free name to use."""
    d = root() / folder
    stem, ext = name.rsplit(".", 1)
    names = _listing(folder) if names is None else names
    k = 1
    while True:
        cand = name if k == 1 else f"{stem} ({k}).{ext}"
        hit = names.get(cand.lower())
        if hit is None:
            return None, cand
        for base in (d, WAITING / folder):
            p = base / hit
            if p.is_file() and p.stat().st_size == size:
                return "same", hit
        k += 1


@router.get("/api/upload/check")
def check(request: Request, name: str, size: int):
    """Before sending: is it already backed up? (lets a phone re-pick everything and only send what's new)"""
    folder = folder_for(request.state.user)
    status, _ = _existing(folder, clean_name(name), size)
    return {"exists": status == "same", "folder": folder}


@router.put("/api/upload")
async def upload(request: Request, name: str, size: int, modified: int = 0):
    folder = folder_for(request.state.user)
    name = clean_name(name)
    if size <= 0:
        raise HTTPException(400, "Empty file")
    if size > MAX:
        raise HTTPException(413, f"{name} is over 2 GB: copy it to the drive with a cable instead")
    status, final = await asyncio.to_thread(_existing, folder, name, size)
    if status == "same":
        async for _ in request.stream():   # drain it: the browser already sent it
            pass
        return {"saved": False, "skipped": True, "path": f"{folder}/{final}"}
    d = root() / folder
    d.mkdir(parents=True, exist_ok=True)
    part = d / f".{final}.{os.getpid()}.{int(time.time() * 1000)}.part"
    got = 0
    try:
        with open(part, "wb") as f:
            async for chunk in request.stream():
                got += len(chunk)
                if got > size:
                    raise HTTPException(400, "More data than announced")
                await asyncio.to_thread(f.write, chunk)
        if got != size:
            raise HTTPException(400, f"Upload interrupted ({got} of {size} bytes)")
        if modified:
            os.utime(part, (modified / 1000, modified / 1000))   # the phone's date: "Modified" in Finder
        target = d / final
        if target.exists():                                      # someone took the name meanwhile
            status, final = await asyncio.to_thread(_existing, folder, name, size)
            if status == "same":
                part.unlink()
                return {"saved": False, "skipped": True, "path": f"{folder}/{final}"}
            target = d / final
        os.rename(part, target)
    finally:
        if part.exists():
            part.unlink()
    return {"saved": True, "skipped": False, "path": f"{folder}/{final}", "waiting": root() is WAITING}


_index = {"proc": None}


@router.post("/api/upload/done")
def batch_done():
    """A batch finished: add the new files to the gallery now (the watcher sends them to Telegram)."""
    from config import roots
    p = _index["proc"]
    if p is None or p.poll() is not None:
        log = open(BASE / "index.log", "a")
        _index["proc"] = subprocess.Popen([sys.executable, str(BASE / "build_index.py")], cwd=BASE,
                                          stdout=log, stderr=log, start_new_session=True)
    return {"indexing": bool(roots())}


def move_waiting():
    """Files that waited in the inbox: onto the drive once they're backed up (see inbox.py)."""
    import inbox
    return inbox.move_to_drive()


@router.get("/api/upload/info")
def info(request: Request):
    folder = folder_for(request.state.user)
    return {"folder": folder, "drive": DRIVE.is_dir()}


class Files(BaseModel):
    files: list[dict]   # [{"name": ..., "size": ...}]


@router.post("/api/upload/status")
def status(request: Request, body: Files):
    """For each picked file: 'backed_up' (drive and Telegram), 'on_drive' (Telegram soon) or 'missing'."""
    folder = folder_for(request.state.user)
    con, names, out = connect(), _listing(folder), []
    for f in body.files[:20000]:
        try:
            name, size = clean_name(str(f.get("name", ""))), int(f.get("size", 0))
        except (HTTPException, ValueError):
            out.append("missing")
            continue
        state, found = _existing(folder, name, size, names)
        if state != "same":
            out.append("missing")
            continue
        row = con.execute("SELECT msg_id FROM media WHERE path = ?", (f"{folder}/{found}",)).fetchone()
        out.append("backed_up" if row and row["msg_id"] else "on_drive")
    return {"folder": folder, "status": out}
