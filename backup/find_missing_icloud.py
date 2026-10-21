"""List photos in the Mac's iCloud Photos library that aren't anywhere in the gallery (drive, inbox or Telegram).

    .venv/bin/python backup/find_missing_icloud.py [OUT_FILE]   # writes their Photos ids, one per line (default: state)
then  .venv/bin/python backup/icloud_export.py --uuids OUT_FILE  to bring exactly those in.

A photo counts as present if the gallery has an item taken at the same second (same kind: photo/video), or a file of
exactly the same size, so renamed copies (IMG_1234 (2).HEIC) still count. Needs Full Disk Access (like the export).
"""
import os
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "gallery"))
from config import DB_PATH, STATE  # noqa: E402

import osxphotos  # noqa: E402

LIBRARY = Path(os.environ.get("PHOTOS_LIBRARY") or "~/Pictures/Photos Library.photoslibrary").expanduser()


def main():
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else STATE / "icloud-missing.txt"
    g = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    have = {(t[:19], k) for t, k in g.execute("SELECT taken_at, kind FROM media")}
    sizes = {s for (s,) in g.execute("SELECT size FROM media")}
    missing = []
    for p in osxphotos.PhotosDB(str(LIBRARY)).photos():
        if p.shared:
            continue
        t = p.date.replace(tzinfo=None).isoformat()[:19]
        if (t, "video" if p.ismovie else "image") in have or p.original_filesize in sizes:
            continue
        missing.append(p)
    out.write_text("".join(p.uuid + "\n" for p in missing))
    print(f"{len(missing)} photos in iCloud Photos aren't in the gallery; ids in {out}")
    for p in sorted(missing, key=lambda p: p.date)[:5]:
        print("  e.g.", p.date.date(), p.original_filename)


if __name__ == "__main__":
    main()
