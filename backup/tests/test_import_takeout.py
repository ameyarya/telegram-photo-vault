"""import_takeout.py: a Google Takeout zip into a gallery folder, dated from Google's notes, duplicates skipped."""
import json
import os
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "import_takeout.py"


def test_takeout_import(tmp_path):
    state, data = tmp_path / "state", tmp_path / "data"
    data.mkdir()
    con = sqlite3.connect(data / "index.db")                       # the library already has one of the photos
    con.execute("CREATE TABLE media (path TEXT, size INT, taken_at TEXT, name TEXT)")
    con.execute("INSERT INTO media VALUES ('iphone/IMG_0001.HEIC', 7, '2019-01-01T10:00:00', 'IMG_0001.HEIC')")
    con.commit()
    z = tmp_path / "takeout-001.zip"
    note = lambda t: json.dumps({"title": "x", "photoTakenTime": {"timestamp": str(t)}})
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("Takeout/Google Photos/Photos from 2015/beach.jpg", b"beach-bytes")
        zf.writestr("Takeout/Google Photos/Photos from 2015/beach.jpg.json", note(1420000000))
        zf.writestr("Takeout/Google Photos/Trip/beach.jpg", b"beach-bytes")              # same photo, an album
        zf.writestr("Takeout/Google Photos/Trip/beach.jpg.json", note(1420000000))
        zf.writestr("Takeout/Google Photos/Photos from 2016/beach.jpg", b"other beach")  # different photo, same name
        zf.writestr("Takeout/Google Photos/Photos from 2016/beach.jpg.json", note(1450000000))
        zf.writestr("Takeout/Google Photos/Photos from 2019/IMG_0001.HEIC", b"already")   # in the library (7 bytes)
        zf.writestr("Takeout/Google Photos/Photos from 2016/clip.mp4", b"video")
        zf.writestr("Takeout/Google Photos/Photos from 2016/notes.txt", b"not media")
    env = {**os.environ, "VAULT_CONFIG": os.devnull, "STATE_DIR": str(state), "GALLERY_DATA": str(data),
           "GALLERY_DRIVE": str(tmp_path / "nodrive"), "TAKEOUT_MIN_FREE_GB": "0", "TAKEOUT_WAIT": "0"}
    run = lambda: subprocess.run([sys.executable, str(SCRIPT), "old-account", str(z)], env=env, capture_output=True,
                                 text=True, check=True).stdout
    out = run()
    dest = state / "inbox/old-account"
    assert sorted(os.listdir(dest)) == ["beach (2).jpg", "beach.jpg", "clip.mp4"]
    assert (dest / "beach.jpg").read_bytes() == b"beach-bytes" and int((dest / "beach.jpg").stat().st_mtime) == 1420000000
    assert "3 added, 1 already in library, 1 same photo in another album" in out
    assert "0 added" in run()                                       # run again: nothing twice
