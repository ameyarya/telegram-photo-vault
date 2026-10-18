"""Fixing dates: photos with no camera date are dated by their file date, which is often the day they were copied.

* Edit one photo or a selection: set a date, or shift them all by the same amount (keeps their order and spacing).
* Suggestions where there's a clue: a date in the file name (20220513_152109.MOV, IMG-20210305-WA0001.jpg,
  Screenshot 2023-01-02 at 10.11.12.png), or the same photo elsewhere with a camera date (an edited _DSC1291.jpg and
  the camera's _DSC1291.ARW) that looks the same and was taken no later than the file itself (iPhone numbers repeat
  after IMG_9999, and two phones can both have an IMG_6164).
Edited dates live in the gallery only (date_src = 'user'), so files stay byte-identical to the Telegram backup, and
re-indexing keeps them. The original date is kept so an edit can be undone. A Live Photo's motion clip follows its photo.
"""
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from db import connect

router = APIRouter()
FMT = "%Y-%m-%dT%H:%M:%S"
NAME_DATE = re.compile(r"(?<!\d)((?:19|20)\d{2})[-_.]?(\d{2})[-_.]?(\d{2})"
                       r"(?:(?:[ _T-]|\sat\s)+(\d{2})[-_.:h]?(\d{2})[-_.:m]?(\d{2}))?")
COPY_SUFFIX = re.compile(r"(?:[\s_-]*(?:\(\d+\)|copy|edited|edit|final|~\d+|-\d{1,2}| \d{1,2}))+$", re.I)


# names too common to mean "the same photo"
GENERIC = {"download", "image", "img", "photo", "picture", "video", "movie", "untitled", "screenshot", "file", "unknown"}


def date_from_name(name):
    """'2022-05-13T15:21:09' from a file name, or None. Noon when the name has a day but no time."""
    stem = name.rsplit(".", 1)[0]
    for m in NAME_DATE.finditer(stem):
        y, mo, d, h, mi, s = m.groups()
        try:
            dt = datetime(int(y), int(mo), int(d), int(h or 12), int(mi or 0), int(s or 0))
        except ValueError:
            continue
        if datetime(1990, 1, 1) < dt <= datetime.now() + timedelta(days=1):
            return dt.strftime(FMT)
    return None


def base_name(name):
    return COPY_SUFFIX.sub("", name.rsplit(".", 1)[0]).lower()


def suggestions(con, ids=None):
    """[{id, suggested, why}] for photos still dated by their file date (or the given ids)."""
    if ids is None:
        rows = con.execute("SELECT id, name, path, taken_at FROM media WHERE date_src = 'file' AND live_of IS NULL").fetchall()
    else:
        q = ",".join("?" * len(ids)) or "NULL"
        rows = con.execute(f"SELECT id, name, path, taken_at FROM media WHERE id IN ({q})", list(ids)).fetchall()
    dated = {}
    for r in con.execute("SELECT id, name, path, taken_at FROM media WHERE date_src IN ('exif', 'user')"):
        dated.setdefault(base_name(r["name"]), []).append(r)
    out = []
    for r in rows:
        when = date_from_name(r["name"])
        if when:
            out.append({"id": r["id"], "suggested": when, "why": "Date in the file name"})
            continue
        key = base_name(r["name"])
        if len(key) < 3 or key in GENERIC:
            continue
        twins = [t for t in dated.get(key, []) if t["id"] != r["id"] and t["taken_at"] <= r["taken_at"]]
        same_top = [t for t in twins if t["path"].split("/")[0] == r["path"].split("/")[0]]
        twins = same_top or twins
        twins = [t for t in twins if _looks_same(con, r["id"], t["id"])]
        if twins:
            times = sorted(datetime.fromisoformat(t["taken_at"]) for t in twins)
            if times[-1] - times[0] <= timedelta(minutes=10):  # several matches must agree, or it's a common name
                t = twins[0]
                out.append({"id": r["id"], "suggested": times[0].strftime(FMT), "twin": t["id"],
                            "why": f"Same photo as {t['path']}"})
    return out


LOOKS_SAME = 0.78  # CLIP similarity: edits, exports and crops of one picture scored 0.79+, different photos 0.76 or less


def _looks_same(con, a, b):
    """Same picture, judging by the smart-search vectors. Without them (analysis not run), the name match stands."""
    import numpy as np
    e = {r[0]: np.frombuffer(r[1], dtype=np.float16).astype(np.float32)
         for r in con.execute("SELECT media_id, emb FROM clip WHERE media_id IN (?, ?)", (a, b))}
    if len(e) < 2:
        return True
    va, vb = e[a], e[b]
    return float(va @ vb / (np.linalg.norm(va) * np.linalg.norm(vb) + 1e-8)) >= LOOKS_SAME


def _with_live_clips(con, ids):
    q = ",".join("?" * len(ids))
    return [(r["id"], r["live_of"]) for r in con.execute(f"SELECT id, live_of FROM media WHERE live_of IN ({q})", ids)]


def apply(con, new_dates):
    """{media id: 'YYYY-MM-DDTHH:MM:SS'} — remembers each original date once, so it can be undone."""
    if not new_dates:
        return 0
    ids = list(new_dates)
    for clip, still in _with_live_clips(con, ids):
        new_dates.setdefault(clip, new_dates[still])
    con.executemany("""INSERT OR IGNORE INTO date_fixes(media_id, orig_taken_at, orig_src)
                       SELECT id, taken_at, date_src FROM media WHERE id = ?""", [(i,) for i in new_dates])
    con.executemany("UPDATE media SET taken_at = ?, date_src = 'user' WHERE id = ?",
                    [(d, i) for i, d in new_dates.items()])
    con.commit()
    return len(ids)


def _parse(v):
    try:
        return datetime.fromisoformat(v)
    except (TypeError, ValueError):
        raise HTTPException(400, "Use a date like 2023-05-01T14:30")


class Edit(BaseModel):
    ids: list[int]
    set: str | None = None          # every photo gets this date and time
    first: str | None = None        # shift all so the earliest becomes this (keeps their spacing)
    use_suggested: bool = False     # take each photo's suggested date


@router.post("/api/dates")
def edit_dates(body: Edit):
    con = connect()
    ids = list(dict.fromkeys(body.ids))
    if not ids:
        raise HTTPException(400, "Pick some photos")
    q = ",".join("?" * len(ids))
    rows = {r["id"]: r["taken_at"] for r in con.execute(f"SELECT id, taken_at FROM media WHERE id IN ({q})", ids)}
    if len(rows) != len(ids):
        raise HTTPException(404, "Some of these photos aren't in the gallery")
    if body.use_suggested:
        new = {s["id"]: s["suggested"] for s in suggestions(con, ids)}
    elif body.set:
        when = _parse(body.set).strftime(FMT)
        new = {i: when for i in ids}
    elif body.first:
        delta = _parse(body.first) - min(datetime.fromisoformat(t) for t in rows.values())
        new = {i: (datetime.fromisoformat(t) + delta).strftime(FMT) for i, t in rows.items()}
    else:
        raise HTTPException(400, "Say what the new date is")
    if any(not ("1900" <= d[:4] <= str(datetime.now().year + 1)) for d in new.values()):
        raise HTTPException(400, "That date is out of range")
    return {"changed": apply(con, new)}


class Ids(BaseModel):
    ids: list[int]


@router.post("/api/dates/reset")
def reset_dates(body: Ids):
    """Back to the date the file itself gives."""
    con = connect()
    ids = list(body.ids)
    if ids:
        ids += [c for c, _ in _with_live_clips(con, ids)]
    q = ",".join("?" * len(ids)) or "NULL"
    rows = con.execute(f"SELECT media_id, orig_taken_at, orig_src FROM date_fixes WHERE media_id IN ({q})", ids).fetchall()
    con.executemany("UPDATE media SET taken_at = ?, date_src = ? WHERE id = ?",
                    [(r["orig_taken_at"], r["orig_src"], r["media_id"]) for r in rows])
    con.executemany("DELETE FROM date_fixes WHERE media_id = ?", [(r["media_id"],) for r in rows])
    con.commit()
    return {"reset": len(rows)}


@router.get("/api/dates/suggestions")
def date_suggestions(ids: str = ""):
    """Suggested dates for photos without a camera date (or for these comma-separated ids)."""
    import server
    con = connect()
    wanted = [int(i) for i in ids.split(",") if i.strip().isdigit()] if ids else None
    sug = suggestions(con, wanted)
    if not sug:
        return {"items": [], "undated": con.execute("SELECT count(*) FROM media WHERE date_src = 'file' AND live_of IS NULL").fetchone()[0]}
    q = ",".join("?" * len(sug))
    items = {r["id"]: dict(r) for r in con.execute(f"SELECT {server.ITEM_COLS} FROM media WHERE id IN ({q})",
                                                    [s["id"] for s in sug])}
    return {"items": [{**items[s["id"]], **s} for s in sug if s["id"] in items],
            "undated": con.execute("SELECT count(*) FROM media WHERE date_src = 'file' AND live_of IS NULL").fetchone()[0]}
