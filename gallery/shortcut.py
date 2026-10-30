"""Back up an iPhone with a Shortcuts automation, no app needed (iOS can't sync a folder in the background).

The shortcut, run by an automation (closing Camera, joining the home Wi-Fi, charging, set times), does:
    GET  /api/shortcut/since          -> the date to send photos from (plain text); the Mac asleep: it just stops
    POST /api/shortcut/upload         -> each photo/video created since then (form field "file")
    POST /api/shortcut/done           -> moves the date forward, only after everything was sent
each with the header "Authorization: Bearer <token>". The token is made on this Mac (POST /api/shortcut/token) and
belongs to one drive folder.

Photos land in <state>/phone-sync/<folder>, where backup/phone_import.py gives each a name never used in that folder
(iPhone numbers wrap around) and sets aside ones already backed up; the watcher then uploads them like the rest.
A photo sent twice (the next run starts a little before the last one, to be safe) is only kept once.
"""
import hashlib
import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse

from config import IMAGE_EXT, STATE, VIDEO_EXT
from db import connect

router = APIRouter()
ROOT = STATE / "phone-sync"
OVERLAP = timedelta(minutes=10)


def _settings(con, key):
    r = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return json.loads(r[0]) if r else None


def _put(con, key, value):
    con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES (?, ?)", (key, json.dumps(value)))
    con.commit()


def token_folder(request):
    """The folder a request's shortcut token belongs to, or None."""
    h = request.headers.get("authorization", "")
    if not h.lower().startswith("bearer "):
        return None
    tokens = _settings(connect(), "shortcut_tokens") or {}
    return tokens.get(hashlib.sha256(h[7:].strip().encode()).hexdigest())


def _folder(request):
    f = getattr(request.state, "shortcut_folder", None)
    if not f:
        raise HTTPException(401, "Needs the shortcut's token")
    return f


@router.post("/api/shortcut/token")
def new_token(request: Request, body: dict):
    """A new token for a folder (this Mac only); the folder's earlier tokens stop working."""
    if not (getattr(request.state, "user", None) or {}).get("local"):
        raise HTTPException(403, "Only on the Mac the gallery runs on")
    folder = re.sub(r"[^A-Za-z0-9 _-]", "", str(body.get("folder", "iphone"))).strip() or "iphone"
    con = connect()
    tokens = {h: f for h, f in (_settings(con, "shortcut_tokens") or {}).items() if f != folder}
    t = secrets.token_urlsafe(24)
    tokens[hashlib.sha256(t.encode()).hexdigest()] = folder
    _put(con, "shortcut_tokens", tokens)
    since = _settings(con, f"shortcut_since:{folder}")
    if not since:                    # from now: what's already on the phone came another way (e.g. iCloud Photos)
        _put(con, f"shortcut_since:{folder}", datetime.now().isoformat(timespec="seconds"))
    return {"token": t, "folder": folder}


@router.get("/api/shortcut/since", response_class=PlainTextResponse)
def since(request: Request):
    folder = _folder(request)
    con = connect()
    _put(con, f"shortcut_started:{folder}", datetime.now().isoformat(timespec="seconds"))
    s = _settings(con, f"shortcut_since:{folder}") or datetime.now().isoformat(timespec="seconds")
    return (datetime.fromisoformat(s) - OVERLAP).strftime("%Y-%m-%d %H:%M:%S")


@router.post("/api/shortcut/done")
def done(request: Request):
    folder = _folder(request)
    con = connect()
    started = _settings(con, f"shortcut_started:{folder}")
    if started:
        _put(con, f"shortcut_since:{folder}", started)
    return {"ok": True}


def _seen(folder):
    f = STATE / f"shortcut-received-{folder}.txt"
    return f, set(f.read_text().split()) if f.exists() else set()


@router.post("/api/shortcut/upload")
async def upload(request: Request, file: UploadFile):
    folder = _folder(request)
    name = os.path.basename((file.filename or "").replace("\\", "/")).lstrip(".")
    name = re.sub(r'[\x00-\x1f<>:"|?*/]', "_", name)
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in IMAGE_EXT | VIDEO_EXT:
        raise HTTPException(415, f"Not a photo or video: {name or '(no name)'}")
    stage = ROOT / folder
    stage.mkdir(parents=True, exist_ok=True)
    part = stage / f".{secrets.token_hex(6)}.part"
    h, size = hashlib.sha256(), 0
    with open(part, "wb") as out:
        while chunk := await file.read(1 << 20):
            h.update(chunk)
            size += len(chunk)
            out.write(chunk)
    log, seen = _seen(folder)
    if h.hexdigest() in seen or size == 0:
        part.unlink()
        return {"ok": True, "skipped": "already sent"}
    stem, k, dest = name.rsplit(".", 1)[0], 1, stage / name
    while dest.exists():
        k += 1
        dest = stage / f"{stem} ({k}).{name.rsplit('.', 1)[1]}"
    os.replace(part, dest)
    with open(log, "a") as f:
        f.write(h.hexdigest() + "\n")
    return {"ok": True, "saved": dest.name, "bytes": size}


@router.get("/api/shortcut/status")
def status(request: Request):
    """For the setup page on this Mac: each shortcut folder and when it last sent photos."""
    con = connect()
    tokens = _settings(con, "shortcut_tokens") or {}
    return [{"folder": f, "since": _settings(con, f"shortcut_since:{f}"),
             "last_run": _settings(con, f"shortcut_started:{f}")} for f in sorted(set(tokens.values()))]
