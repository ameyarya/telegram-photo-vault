"""Import a Google Photos export (Google Takeout zip files) into a folder of the gallery, a few GB at a time.

    .venv/bin/python backup/import_takeout.py FOLDER takeout-001.zip [takeout-002.zip ...]

Photos and videos are unpacked into the Mac's inbox (INBOX/FOLDER/), where the watcher uploads them to Telegram. Only a
few GB are unpacked at once: the next batch waits until the watcher has backed up the last one (and, for a Telegram-only
folder, removed it from the Mac), so a 50 GB export never needs 50 GB of free space.

* The date: Takeout often strips it from the file, so it's taken from Google's .json note next to each photo
  ("photoTakenTime") and set as the file's date (what the gallery uses when a photo has no camera date).
* Duplicates are skipped: Takeout puts a photo once per album it's in, and photos already in your library (same size,
  taken at the same time) aren't uploaded again.
* Flat: everything goes straight into FOLDER/; a name that's taken gets " (2)".
* Resumable: what's done is remembered (takeout-imported.txt in the state folder), so run it again any time.
* Videos over Telegram's 2 GB limit are left out and listed at the end (copy them to the drive to have them split).
Writes its progress to takeout-import.log in the state folder.
"""
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "gallery"))
from config import DB_PATH, IMAGE_EXT, INBOX, STATE, VIDEO_EXT  # noqa: E402
import inbox  # noqa: E402

BATCH = int(float(os.environ.get("TAKEOUT_BATCH_GB", 3)) * 1024**3)     # unpack this much, then wait for the backup
MIN_FREE = float(os.environ.get("TAKEOUT_MIN_FREE_GB", 8))             # and keep at least this much free on the Mac
WAIT = int(os.environ.get("TAKEOUT_WAIT", 30))
MAX = 2 * 1024**3
DONE = STATE / "takeout-imported.txt"
LOG = STATE / "takeout-import.log"


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S  ") + msg
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def sidecars(z):
    """Google's notes: {photo entry name: photoTakenTime}. Takeout names them '<photo>.json' or, when the name is long,
    with a cut-down name ('<photo>.supplemental-metadata.json', '<photo>.suppl.json' ...); match by folder + prefix."""
    notes = {}
    for n in z.namelist():
        if n.endswith(".json"):
            try:
                t = json.loads(z.read(n)).get("photoTakenTime", {}).get("timestamp")
            except (ValueError, KeyError, AttributeError):
                continue
            if t:
                notes[n[:-5]] = int(t)
    def taken(name):
        if name in notes:
            return notes[name]
        folder, fn = name.rsplit("/", 1) if "/" in name else ("", name)
        m = re.match(r"^(.*?)(\(\d+\))(\.\w+)$", fn)                       # IMG(1).jpg -> IMG.jpg(1).json
        if m and f"{folder}/{m.group(1)}{m.group(3)}{m.group(2)}" in notes:
            return notes[f"{folder}/{m.group(1)}{m.group(3)}{m.group(2)}"]
        for k, v in notes.items():                                         # cut-down names
            kf, kn = k.rsplit("/", 1) if "/" in k else ("", k)
            if kf == folder and (kn.startswith(fn) or (len(kn) >= 40 and fn.startswith(kn.split(".")[0]))):
                return v
        return None
    return taken


def base_name(n):
    """IMG_1234 (2).HEIC, IMG_1234(1).heic -> img_1234.heic"""
    stem, ext = (n.lower().rsplit(".", 1) + [""])[:2]
    return re.sub(r"\s*\(\d+\)$|\s\d$", "", stem) + "." + ext


def library():
    """What's already in the gallery: (size, taken second) and (size, file name). Google's time is in UTC and the
    library's camera times are local, so a byte-exact size with the same name also counts as the same photo."""
    if not DB_PATH.exists():
        return set()
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    have = set()
    for size, t, name in con.execute("SELECT size, taken_at, name FROM media"):
        have.add((size, t[:19]))
        have.add((size, base_name(name)))
    con.close()
    return have


def pending_bytes(folder):
    done = inbox.backed_up()
    total = 0
    for rel in inbox.files():
        if rel.split("/", 1)[0] == folder and rel not in done:
            total += (INBOX / rel).stat().st_size
    return total


def wait_for_room(folder):
    while True:
        p, free = pending_bytes(folder), inbox.mac_free_gb()
        if p < BATCH and free > MIN_FREE:
            return
        log(f"waiting for the backup to catch up ({p / 1e9:.1f} GB to upload, {free:.0f} GB free on the Mac)")
        time.sleep(WAIT)


def free_name(dest, name, taken_names):
    stem, ext = (name.rsplit(".", 1) + [""])[:2]
    k, cand = 1, name
    while cand.lower() in taken_names or (dest / cand).exists():
        k += 1
        cand = f"{stem} ({k}).{ext}" if ext else f"{stem} ({k})"
    taken_names.add(cand.lower())
    return cand


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    folder, zips = sys.argv[1].strip("/"), [Path(z) for z in sys.argv[2:]]
    dest = INBOX / folder
    dest.mkdir(parents=True, exist_ok=True)
    STATE.mkdir(parents=True, exist_ok=True)
    done = set(DONE.read_text().splitlines()) if DONE.exists() else set()
    have = library()
    names = {n.lower() for n in os.listdir(dest)}
    if DB_PATH.exists():
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        names.update(p.rsplit("/", 1)[1].lower() for (p,) in con.execute("SELECT path FROM media WHERE path LIKE ?",
                                                                          (folder + "/%",)))
        con.close()
    seen, counts, too_big = set(), {"added": 0, "already in library": 0, "same photo in another album": 0}, []
    for zp in zips:
        log(f"reading {zp.name}")
        with zipfile.ZipFile(zp) as z:
            taken = sidecars(z)
            for info in z.infolist():
                n = info.filename
                ext = n.rsplit(".", 1)[-1].lower() if "." in n.rsplit("/", 1)[-1] else ""
                if info.is_dir() or ext not in IMAGE_EXT | VIDEO_EXT or info.file_size == 0:
                    continue
                key = f"{zp.name}::{n}"
                if key in done:
                    continue
                t = taken(n)
                when = datetime.fromtimestamp(t).strftime("%Y-%m-%dT%H:%M:%S") if t else None
                ident = (info.file_size, t, n.rsplit("/", 1)[-1].lower())
                if ident in seen:
                    counts["same photo in another album"] += 1
                elif (when and (info.file_size, when) in have) or (info.file_size, base_name(n.rsplit("/", 1)[-1])) in have:
                    counts["already in library"] += 1
                elif info.file_size > MAX:
                    too_big.append(n)
                else:
                    wait_for_room(folder)
                    out = dest / free_name(dest, n.rsplit("/", 1)[-1], names)
                    tmp = dest / f".{out.name}.part"
                    with z.open(info) as src, open(tmp, "wb") as f:
                        shutil.copyfileobj(src, f, 1 << 20)
                    if t:
                        os.utime(tmp, (t, t))                              # the date it was taken (Google's note)
                    os.rename(tmp, out)
                    counts["added"] += 1
                    if counts["added"] % 200 == 0:
                        log(f"{counts['added']} added so far")
                seen.add(ident)
                with open(DONE, "a") as d:
                    d.write(key + "\n")
    log("done: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    if too_big:
        log(f"{len(too_big)} videos over 2 GB left out (copy them to the drive to have them split): {too_big[:5]}")


if __name__ == "__main__":
    main()
