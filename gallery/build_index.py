"""Index the drive: album, real date, size, dimensions, and a 400px thumbnail per photo/video.

Incremental: files already indexed with the same size+mtime are skipped, so re-run any time
(e.g. after adding photos). Needs the drive plugged in; the gallery itself does not.
Usage: ../.venv/bin/python build_index.py [--no-thumbs]
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from config import DRIVE, IMAGE_EXT, INBOX, THUMBS, VIDEO_EXT, folders, local_path, roots
from db import connect

SKIP_EXT = {"aae", "xml", "srt", "thm"}
PART_RE = re.compile(r"^(.*)_part(\d+)$")


def walk():
    """Yield (relpath, stat) for every media file on the drive and in the inbox, with the same rules as the uploader."""
    seen = set()
    for root, top in ((r, t) for r in roots() for t in folders()):
        base = root / top
        if not base.is_dir() or (top == "_split" and not (base / ".complete").exists()):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            rel_dir = os.path.relpath(dirpath, root)
            if ".photoslibrary" in rel_dir and ".photoslibrary/originals" not in rel_dir:
                # a macOS Photos library: only its originals are photos; the rest is databases and caches
                if rel_dir.endswith(".photoslibrary"):
                    dirnames[:] = [d for d in dirnames if d == "originals"]
                else:
                    dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            for fn in filenames:
                ext = fn.rsplit(".", 1)[-1].lower() if "." in fn else ""
                if fn.startswith(".") or ext in SKIP_EXT or ext not in IMAGE_EXT | VIDEO_EXT:
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.join(rel_dir, fn)
                st = os.stat(full)
                if st.st_size == 0 or st.st_size > 2 * 1024**3 or rel in seen:  # empty, or uploaded as split parts
                    continue
                seen.add(rel)
                yield rel, st


def album_and_name(rel):
    folder, fn = os.path.split(rel)
    stem, ext = os.path.splitext(fn)
    if folder.startswith("_split/") or folder == "_split":
        folder = folder[len("_split/"):]
        m = PART_RE.match(stem)
        if m:
            return folder, f"{m.group(1)}{ext} (part {int(m.group(2)) + 1})"
    if ".photoslibrary/originals" in folder:
        folder = folder.split(".photoslibrary")[0]
    return folder, fn


def exif_batch(paths):
    """Return {relpath: exif dict} using one exiftool process per batch."""
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        where = {str(local_path(p)): p for p in paths}
        f.write("\n".join(where))
        argfile = f.name
    try:
        out = subprocess.run(
            ["exiftool", "-j", "-fast", "-api", "QuickTimeUTC", "-d", "%Y-%m-%dT%H:%M:%S",
             "-DateTimeOriginal", "-CreationDate", "-CreateDate", "-MediaCreateDate", "-ImageWidth#", "-ImageHeight#",
             "-Duration#", "-Orientation#", "-Rotation#", "-Make", "-Model", "-LensModel", "-ISO#", "-FNumber#",
             "-ExposureTime#", "-FocalLength#", "-GPSLatitude#", "-GPSLongitude#", "-@", argfile],
            capture_output=True, text=True).stdout
        rows = json.loads(out or "[]")
    finally:
        os.unlink(argfile)
    return {where[r["SourceFile"]]: r for r in rows if r.get("SourceFile") in where}


def sideways(e):
    return 1 if e.get("Orientation") in (5, 6, 7, 8) or e.get("Rotation") in (90, 270) else 0


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def details(e):
    """Camera, settings and location from EXIF, in the order of DETAIL_COLS."""
    lat, lon = _num(e.get("GPSLatitude")), _num(e.get("GPSLongitude"))
    if lat is not None and lon is not None and lat == 0 and lon == 0:
        lat = lon = None  # "0,0" is a missing fix, not the Gulf of Guinea
    iso = _num(e.get("ISO"))
    return (str(e["Make"]).strip() if e.get("Make") else None, str(e["Model"]).strip() if e.get("Model") else None,
            str(e["LensModel"]).strip() if e.get("LensModel") else None, int(iso) if iso else None,
            _num(e.get("FNumber")), _num(e.get("ExposureTime")), _num(e.get("FocalLength")), lat, lon)


DETAIL_COLS = ("make", "model", "lens", "iso", "fnumber", "exposure", "focal", "lat", "lon")


def valid_date(v):
    return isinstance(v, str) and len(v) >= 19 and not v.startswith("0000") and v[:4] > "1990"


def thumb_path(media_id):
    return THUMBS / f"{media_id % 256:02x}" / f"{media_id}.jpg"


def make_thumb(row):
    src, out = str(local_path(row["path"])), thumb_path(row["id"])
    if out.exists():  # already made on demand by the gallery
        return row["id"], 1
    out.parent.mkdir(parents=True, exist_ok=True)
    if row["kind"] == "image":
        cmd = ["sips", "-s", "format", "jpeg", "-s", "formatOptions", "70", "-Z", "400", src, "--out", str(out)]
    else:
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-ss", "1", "-i", src, "-frames:v", "1",
               "-vf", "scale=400:400:force_original_aspect_ratio=decrease", "-q:v", "5", str(out)]
    ok = subprocess.run(cmd, capture_output=True).returncode == 0 and out.exists()
    if not ok and row["kind"] == "video":  # very short clips: take the first frame
        cmd[cmd.index("-ss") + 1] = "0"
        ok = subprocess.run(cmd, capture_output=True).returncode == 0 and out.exists()
    return row["id"], 1 if ok else -1


LIVE_MAX_SECONDS = 5  # an iPhone Live Photo's motion clip is ~1.5-3 s; longer same-name videos are real videos
LIVE_SAME_MOMENT = 10  # seconds between a photo and its motion clip (real pairs: within 2 s)


def same_moment(a, b, tol=LIVE_SAME_MOMENT):
    """Taken at the same moment: within tol seconds, also allowing a whole time-zone shift (photos taken while
    travelling can store local time while their clip stores another zone; zones go in 15-minute steps)."""
    from datetime import datetime
    dt = abs((datetime.fromisoformat(a) - datetime.fromisoformat(b)).total_seconds())
    off = dt % 900
    return dt <= tol or (dt <= 26 * 3600 and min(off, 900 - off) <= tol)


def prune_missing(con):
    """Forget items whose file is gone from the drive (deleted or renamed in Finder) and that never reached Telegram:
    there's nothing left to show. Items in Telegram stay (they're viewed from there), and so does the gallery's Trash.
    Without this, a folder renamed in Finder showed up twice."""
    if not DRIVE.is_dir():
        return 0
    rows = con.execute("""SELECT id, path FROM media WHERE msg_id IS NULL
                          AND id NOT IN (SELECT media_id FROM trash)""").fetchall()
    gone = [r["id"] for r in rows if not (DRIVE / r["path"]).exists() and not (INBOX / r["path"]).exists()]
    if not DRIVE.is_dir():                     # unplugged while checking: don't trust the answers
        return 0
    for i in range(0, len(gone), 500):
        chunk = gone[i:i + 500]
        q = ",".join("?" * len(chunk))
        for table in ("faces", "clip", "similar", "favorites", "user_album_items", "ml_done", "date_fixes"):
            con.execute(f"DELETE FROM {table} WHERE media_id IN ({q})", chunk)
        con.execute(f"DELETE FROM media WHERE id IN ({q})", chunk)
    con.commit()
    return len(gone)


def pair_live_photos(con):
    """A short video with the same name as a photo in the same folder, recorded at the same moment, is that photo's
    Live Photo motion: hide it from the timeline and let the photo play it. Names alone aren't enough: iPhone
    numbers repeat (IMG_9999 wraps around, and every phone has an IMG_0001), which paired random videos."""
    real = ("""SELECT m.id, m.album, m.name, m.ext, m.duration, m.kind,
                      CASE WHEN m.date_src = 'user' THEN f.orig_taken_at ELSE m.taken_at END AS t,
                      CASE WHEN m.date_src = 'user' THEN f.orig_src ELSE m.date_src END AS src
               FROM media m LEFT JOIN date_fixes f ON f.media_id = m.id WHERE m.kind = ?""")  # your date edits don't count
    stills = {}
    for r in con.execute(real, ("image",)):
        if r["src"] == "exif":                     # only a camera date says when it was really taken
            stills.setdefault((r["album"], r["name"][:-len(r["ext"]) - 1].lower()), []).append(r)
    pairs, taken = [], set()
    for r in con.execute(real, ("video",)):
        if r["src"] != "exif" or (r["duration"] or 0) > LIVE_MAX_SECONDS:
            continue
        for st in stills.get((r["album"], r["name"][:-len(r["ext"]) - 1].lower()), []):
            if st["id"] not in taken and same_moment(st["t"], r["t"]):
                pairs.append((st["id"], r["id"]))
                taken.add(st["id"])
                break
    con.execute("UPDATE media SET live_id = NULL, live_of = NULL WHERE live_id IS NOT NULL OR live_of IS NOT NULL")
    con.executemany("UPDATE media SET live_id = ? WHERE id = ?", [(v, s) for s, v in pairs])
    con.executemany("UPDATE media SET live_of = ? WHERE id = ?", pairs)
    con.commit()
    return len(pairs)


def main():
    if not roots():
        sys.exit(f"Drive not mounted at {DRIVE}, and nothing in the inbox ({INBOX})")
    con = connect(timeout=600)  # wait out the gallery writing rather than crash
    known = {r["path"]: (r["size"], r["mtime"]) for r in con.execute("SELECT path, size, mtime FROM media")}

    todo = [(rel, st) for rel, st in walk() if known.get(rel) != (st.st_size, st.st_mtime)]
    print(f"{len(todo)} new or changed files to index", flush=True)

    for i in range(0, len(todo), 500):
        chunk = todo[i:i + 500]
        exif = exif_batch([rel for rel, _ in chunk])
        for rel, st in chunk:
            e = exif.get(rel, {})
            ext = rel.rsplit(".", 1)[-1].lower()
            kind = "video" if ext in VIDEO_EXT else "image"
            date = next((e[k] for k in ("DateTimeOriginal", "CreationDate", "CreateDate", "MediaCreateDate") if valid_date(e.get(k))), None)
            src = "exif" if date else "file"
            if not date:  # same fallback as the timestamp manifest: the file's own date
                ts = min(st.st_mtime, getattr(st, "st_birthtime", st.st_mtime))
                date = datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")
            album, name = album_and_name(rel)
            con.execute(
                f"""INSERT INTO media(path, album, name, kind, ext, taken_at, date_src, size, mtime, width, height, duration,
                                      orient, thumb, exif2, {", ".join(DETAIL_COLS)})
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,0,1,{",".join("?" * len(DETAIL_COLS))})
                   ON CONFLICT(path) DO UPDATE SET album=excluded.album, name=excluded.name,
                     taken_at=CASE WHEN date_src = 'user' THEN taken_at ELSE excluded.taken_at END,
                     date_src=CASE WHEN date_src = 'user' THEN 'user' ELSE excluded.date_src END, size=excluded.size, mtime=excluded.mtime, width=excluded.width,
                     height=excluded.height, duration=excluded.duration, orient=excluded.orient, thumb=0, exif2=1,
                     {", ".join(f"{c}=excluded.{c}" for c in DETAIL_COLS)}""",
                (rel, album, name, kind, ext, date[:19], src, st.st_size, st.st_mtime,
                 e.get("ImageWidth"), e.get("ImageHeight"), e.get("Duration"), sideways(e)) + details(e))
        con.commit()
        print(f"indexed {min(i + 500, len(todo))}/{len(todo)}", flush=True)

    # rows indexed before orientation / camera details were tracked
    con.execute("DELETE FROM media WHERE size = 0")  # empty files: nothing to show or back up
    con.commit()
    old = [r["path"] for r in con.execute("SELECT path FROM media WHERE exif2 IS NULL")]
    sets = ", ".join(f"{c}=?" for c in DETAIL_COLS)
    for i in range(0, len(old), 500):
        exif = exif_batch(old[i:i + 500])
        con.executemany(f"UPDATE media SET orient=?, exif2=1, {sets} WHERE path=?",
                        [(sideways(exif.get(p, {})),) + details(exif.get(p, {})) + (p,) for p in old[i:i + 500]])
        con.commit()
        print(f"details backfill {min(i + 500, len(old))}/{len(old)}", flush=True)

    gone = prune_missing(con)
    if gone:
        print(f"{gone} items removed: their files are gone from the drive and were never backed up", flush=True)
    n = pair_live_photos(con)
    print(f"{n} Live Photos paired with their motion clips", flush=True)

    if "--no-thumbs" in sys.argv:
        return
    rows = [dict(r) for r in con.execute("SELECT id, path, kind FROM media WHERE thumb = 0")]
    print(f"{len(rows)} thumbnails to make", flush=True)
    done, pending = 0, []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for media_id, status in pool.map(make_thumb, rows):
            pending.append((status, media_id))
            done += 1
            if len(pending) >= 50:  # short write transactions, so the gallery's sync never waits long
                con.executemany("UPDATE media SET thumb=? WHERE id=?", pending)
                con.commit()
                pending = []
            if done % 500 == 0:
                print(f"thumbs {done}/{len(rows)}", flush=True)
    con.executemany("UPDATE media SET thumb=? WHERE id=?", pending)
    con.commit()
    print("done", flush=True)


if __name__ == "__main__":
    main()
