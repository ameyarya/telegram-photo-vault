"""Restore: download photos/videos back from Telegram into a folder, with their original dates.

Started from the gallery (any album, folder, search, filter or selection). Files land in
~/Downloads/Photo Vault Restore/<name> <date>/<original path on the drive>. Each file's size is checked, and when the
drive is plugged in its SHA-256 is compared with the original. Created/modified dates come from the timestamp
manifest (timestamps.tsv) the backup saved. Restoring only writes into that new folder.
"""
import asyncio
import hashlib
import json
import os
import subprocess
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import DRIVE, RESTORE_DIR, TIMESTAMPS
from db import connect
from search import selection

router = APIRouter()
_tasks = {}  # job id -> asyncio task


class RestoreRequest(BaseModel):
    album: str = ""
    sub: bool = True
    filter: str = ""
    q: str = ""
    user_album: int | None = None
    person: int | None = None
    ids: list[int] | None = None
    label: str = ""
    verify: bool = True


def load_timestamps(path=TIMESTAMPS):
    """path -> (created, modified) as epoch seconds, from the manifest written by the backup."""
    out = {}
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return out
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        try:
            out[parts[0]] = (datetime.fromisoformat(parts[2]).timestamp(), datetime.fromisoformat(parts[3]).timestamp())
        except ValueError:
            continue
    return out


def set_dates(path, created, modified):
    # On APFS, setting an earlier modification time also moves the creation date back; then set the real mtime.
    os.utime(path, (created, created))
    os.utime(path, (modified, modified))


def _sha256(path):
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _update(job_id, **fields):
    con = connect()
    sets = ", ".join(f"{k} = ?" for k in fields)
    con.execute(f"UPDATE jobs SET {sets} WHERE id = ?", list(fields.values()) + [job_id])
    con.commit()


async def _run(job_id, rows, dest, verify):
    import server
    stamps = await asyncio.to_thread(load_timestamps)
    done = skipped = 0
    errors = []
    detail = {"dest": str(dest), "errors": errors, "skipped": 0, "verified": 0, "dated": 0}
    slots = asyncio.Semaphore(3)
    pending = []

    async def one(row):
        nonlocal done, skipped
        target = dest / row["path"]
        async with slots:
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists() and target.stat().st_size == row["size"]:
                    skipped += 1
                else:
                    tmp = target.with_name(target.name + ".part")
                    with open(tmp, "wb") as f:
                        async for chunk in server.tg_bytes(row, 0, row["size"] - 1):
                            f.write(chunk)
                    if tmp.stat().st_size != row["size"]:
                        raise RuntimeError(f"size {tmp.stat().st_size} != {row['size']}")
                    tmp.rename(target)
                original = DRIVE / row["path"]
                if verify and original.is_file():
                    same = await asyncio.to_thread(lambda: _sha256(target) == _sha256(original))
                    if not same:
                        raise RuntimeError("content differs from the original on the drive")
                    detail["verified"] += 1
                if row["path"] in stamps:
                    set_dates(target, *stamps[row["path"]])
                    detail["dated"] += 1
            except Exception as e:  # keep going; report at the end
                errors.append(f"{row['path']}: {e}")
            done += 1

    try:
        pending = [asyncio.create_task(one(r)) for r in rows]
        for i, t in enumerate(asyncio.as_completed(pending), 1):
            await t
            if i % 5 == 0 or i == len(pending):
                detail["skipped"] = skipped
                await asyncio.to_thread(_update, job_id, done=done, detail=json.dumps(detail))
        detail["skipped"] = skipped
        status = "done" if not errors else "done with errors"
        await asyncio.to_thread(_update, job_id, status=status, done=done, detail=json.dumps(detail),
                                finished=datetime.now().isoformat(timespec="seconds"))
    except asyncio.CancelledError:
        for t in pending:
            t.cancel()
        await asyncio.to_thread(_update, job_id, status="cancelled", done=done, detail=json.dumps(detail),
                                finished=datetime.now().isoformat(timespec="seconds"))


@router.post("/api/restore")
async def start_restore(req: RestoreRequest):
    import server
    if server.state["client"] is None:
        raise HTTPException(503, "Telegram isn't connected, so there's nothing to restore from")
    where, args = selection(req.album, req.sub, req.filter, req.q, req.user_album, req.person, req.ids)
    con = connect()
    rows = [dict(r) for r in con.execute(f"SELECT * FROM media WHERE {where} AND msg_id IS NOT NULL", args)]
    # a Live Photo comes back with its motion clip
    stills = [r["live_id"] for r in rows if r.get("live_id")]
    for i in range(0, len(stills), 500):
        chunk = stills[i:i + 500]
        rows += [dict(r) for r in con.execute(
            f"SELECT * FROM media WHERE id IN ({','.join('?' * len(chunk))}) AND msg_id IS NOT NULL", chunk)]
    missing = con.execute(f"SELECT count(*) FROM media WHERE {where} AND msg_id IS NULL", args).fetchone()[0]
    if not rows:
        raise HTTPException(400, "None of these are backed up to Telegram yet")
    label = (req.label or (req.album.split("/")[-1] if req.album else "") or "Everything").replace("/", "-")[:80]
    dest = RESTORE_DIR / f"{label} {datetime.now():%Y-%m-%d %H-%M-%S}"
    cur = con.execute("INSERT INTO jobs(kind, status, total, detail) VALUES ('restore', 'running', ?, ?)",
                      (len(rows), json.dumps({"dest": str(dest), "not_backed_up": missing})))
    con.commit()
    job_id = cur.lastrowid
    _tasks[job_id] = asyncio.create_task(_run(job_id, rows, dest, req.verify))
    return {"job": job_id, "files": len(rows), "not_backed_up": missing, "dest": str(dest),
            "bytes": sum(r["size"] for r in rows)}


@router.get("/api/jobs")
def list_jobs(kind: str = ""):
    con = connect()
    rows = con.execute("SELECT * FROM jobs WHERE (? = '' OR kind = ?) ORDER BY id DESC LIMIT 20", (kind, kind))
    return [{**dict(r), "detail": json.loads(r["detail"] or "{}")} for r in rows]


@router.get("/api/jobs/{job_id}")
def get_job(job_id: int):
    r = connect().execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if not r:
        raise HTTPException(404, "no such job")
    return {**dict(r), "detail": json.loads(r["detail"] or "{}")}


@router.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: int):
    t = _tasks.get(job_id)
    if t and not t.done():
        t.cancel()
        return {"ok": True}
    raise HTTPException(400, "that job isn't running")


@router.post("/api/jobs/{job_id}/reveal")
def reveal(job_id: int):
    """Open the restored folder in Finder."""
    dest = get_job(job_id)["detail"].get("dest")
    if not dest or not os.path.isdir(dest):
        raise HTTPException(404, "folder not found")
    if not os.environ.get("GALLERY_NO_FINDER"):
        subprocess.run(["open", dest], check=False)
    return {"ok": True, "dest": dest}
