"""The basic photo editor: the page crops, rotates and adjusts the photo itself and sends the result here as a JPEG.

It is saved as a new photo next to the original ("<name> edited.jpg"), which is never changed, and goes through the inbox
like any new photo: into the gallery, to Telegram, then onto the drive. It keeps the original's date, camera and place,
so it sits beside the original on the timeline, and a screenshot stays a screenshot.
"""
import asyncio
import os
import secrets
import subprocess

from fastapi import APIRouter, HTTPException, UploadFile

from config import DRIVE, INBOX
from db import connect
from search import SCREENSHOT

router = APIRouter()


def _free_name(con, folder, stem):
    """A name never used in that folder: on the drive, in the inbox, in the gallery or in Telegram."""
    import inbox
    used = inbox.backed_up()
    for k in range(1, 1000):
        rel = os.path.join(folder, f"{stem} edited{'' if k == 1 else f' {k}'}.jpg")
        if not ((INBOX / rel).exists() or (DRIVE / rel).exists() or rel in used
                or con.execute("SELECT 1 FROM media WHERE path = ?", (rel,)).fetchone()):
            return rel
    raise HTTPException(409, "Too many edited copies of this photo")


def _tags(row, shot):
    tags = []
    if row["taken_at"]:
        when = row["taken_at"][:19].replace("-", ":", 2).replace("T", " ")
        tags += [f"-DateTimeOriginal={when}", f"-CreateDate={when}"]
    if row["make"]:
        tags.append(f"-Make={row['make']}")
    if row["model"]:
        tags.append(f"-Model={row['model']}")
    if row["lat"] is not None and row["lon"] is not None:
        tags += [f"-GPSLatitude*={row['lat']}", f"-GPSLongitude*={row['lon']}"]
    if shot:
        tags.append("-UserComment=Screenshot")
    return tags


@router.post("/api/edit/{media_id}")
async def save_edit(media_id: int, file: UploadFile):
    con = connect()
    row = con.execute(f"SELECT *, {SCREENSHOT} AS is_shot FROM media WHERE id = ?", (media_id,)).fetchone()
    if not row:
        raise HTTPException(404, "No such photo")
    if row["kind"] != "image":
        raise HTTPException(400, "Only photos can be edited")
    data = await file.read()
    if data[:2] != b"\xff\xd8":
        raise HTTPException(415, "Expected a JPEG")
    folder, name = os.path.split(row["path"])
    rel = _free_name(con, folder, name.rsplit(".", 1)[0])
    (INBOX / folder).mkdir(parents=True, exist_ok=True)
    tmp = INBOX / folder / f".{secrets.token_hex(6)}.jpg"       # hidden: not picked up before it's complete
    tmp.write_bytes(data)
    proc = await asyncio.create_subprocess_exec("exiftool", "-q", "-q", "-overwrite_original", *_tags(row, row["is_shot"]), str(tmp),
                                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    await proc.wait()
    os.replace(tmp, INBOX / rel)
    import upload
    upload.batch_done()                                         # into the gallery now; the watcher sends it to Telegram
    return {"saved": rel}
