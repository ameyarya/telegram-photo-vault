"""Bring photos synced from an iPhone (Möbius Sync, a Syncthing app) into the inbox, never reusing a name.

Android names photos by date, so its Syncthing folder can be the inbox folder itself. An iPhone numbers them, and the
numbers wrap around (a new IMG_0004 next to an old, different IMG_0004): synced straight into the inbox, a new photo
would take the name of one already in Telegram and never be uploaded. So the iPhone's Syncthing folder is a staging
folder, <state>/phone-sync/<drive folder> (e.g. phone-sync/iphone), and every watcher round this:

  * moves each new photo into the inbox folder of the same name, renaming it " (2)" (" (3)" ...) if the name was ever
    used there (on the drive, in the inbox or in Telegram), a photo and its Live video together, like icloud_export.py;
  * sets aside photos that are already backed up (same size and date, or same size and name, e.g. what iCloud Photos
    already brought) in a hidden .already-backed-up folder, instead of uploading them twice;
  * adds both to the folder's .stignore, so Syncthing never downloads them again.

Set the Syncthing folder up as Receive Only with Ignore Delete, like a phone that syncs into the inbox.
"""
import os
import re
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "gallery"))
from config import DB_PATH, INBOX, STATE  # noqa: E402
from icloud_export import move_in, names_in_use  # noqa: E402

ROOT = STATE / "phone-sync"
ASIDE = ".already-backed-up"


def base_name(n):
    """IMG_1234 (2).HEIC -> img_1234.heic"""
    stem, ext = (n.lower().rsplit(".", 1) + [""])[:2]
    return re.sub(r"\s*\(\d+\)$", "", stem) + "." + ext


def backed_up(folder):
    """(size, second) and (size, name) of everything already in Telegram from that folder."""
    have = set()
    if DB_PATH.exists():
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        for path, size, mtime in con.execute("SELECT path, size, mtime FROM media WHERE path LIKE ? AND msg_id IS NOT NULL",
                                             (folder + "/%",)):
            have.add((size, round(mtime or 0)))
            have.add((size, base_name(path.rsplit("/", 1)[1])))
        con.close()
    return have


def ignore_line(rel):
    return "/" + re.sub(r"([\\*?\[\]{}!#])", r"\\\1", rel) + "\n"


def run(log=print):
    total = 0
    if not ROOT.is_dir():
        return 0
    for stage in sorted(ROOT.iterdir()):
        if not stage.is_dir() or stage.name.startswith("."):
            continue
        dest = INBOX / stage.name
        dest.mkdir(parents=True, exist_ok=True)
        have, gone, aside = backed_up(stage.name), [], 0
        for dp, dn, fn in os.walk(stage):
            dn[:] = sorted(d for d in dn if not d.startswith("."))
            d = Path(dp)
            files = [f for f in fn if not f.startswith(".") and (d / f).is_file()]
            if not files:
                continue
            for f in files:
                st = (d / f).stat()
                if (st.st_size, round(st.st_mtime)) in have or (st.st_size, base_name(f)) in have:
                    out = stage / ASIDE / (d / f).relative_to(stage)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(d / f, out)
                    aside += 1
            gone += [(d / f).relative_to(stage).as_posix() for f in files]
            total += len(move_in(d, dest, names_in_use(stage.name)))
        if gone and (stage / ".stfolder").exists():            # a Syncthing folder: never download them again
            ign = stage / ".stignore"
            first = "" if ign.exists() and f"/{ASIDE}\n" in ign.read_text() else f"/{ASIDE}\n"
            with open(ign, "a") as s:
                s.write(first + "".join(ignore_line(r) for r in gone))
        if aside:
            log(f"{aside} photos from {stage.name}'s phone sync were already backed up: set aside, not uploaded again")
    return total


if __name__ == "__main__":
    print(f"moved {run()} photos into the inbox")
