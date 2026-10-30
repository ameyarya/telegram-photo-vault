"""phone_import.py: an iPhone's synced photos reach the inbox without ever reusing a name already in Telegram."""
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent


def test_iphone_photos_are_renamed_set_aside_and_never_synced_again(tmp_path):
    state, drive, data = tmp_path / "state", tmp_path / "drive", tmp_path / "data"
    stage = state / "phone-sync/iphone"
    (stage / "2026/10").mkdir(parents=True)
    (drive / "iphone").mkdir(parents=True)
    data.mkdir()
    (drive / "iphone/IMG_0004.HEIC").write_bytes(b"old photo")        # numbers wrapped: same name, other photo
    sys.path[:0] = [str(HERE.parent / "gallery")]
    from db import SCHEMA
    sys.path.pop(0)
    con = sqlite3.connect(data / "index.db")
    con.executescript(SCHEMA)
    for path, size, mtime in [("iphone/IMG_0004.HEIC", 9, 1000.0), ("iphone/IMG_0100 (2).HEIC", 7, 2000.0)]:
        con.execute("INSERT INTO media(path, album, name, kind, ext, taken_at, date_src, size, mtime, msg_id) "
                    "VALUES (?, 'iphone', ?, 'photo', 'heic', '2026-01-01T00:00:00', 'file', ?, ?, 1)",
                    (path, path.split("/")[1], size, mtime))
    con.commit()
    (stage / "2026/10/IMG_0004.HEIC").write_bytes(b"new photo, longer")
    (stage / "2026/10/IMG_0004.MOV").write_bytes(b"its live video")
    (stage / "2026/10/IMG_0100.HEIC").write_bytes(b"backed!")
    (stage / "IMG_[1].PNG").write_bytes(b"png")
    (stage / "2026/10/.syncthing.IMG_0005.HEIC.tmp").write_bytes(b"still coming")
    env = {**os.environ, "VAULT_CONFIG": os.devnull, "STATE_DIR": str(state), "GALLERY_DRIVE": str(drive),
           "GALLERY_DATA": str(data), "FOLDERS": ""}
    r = subprocess.run([sys.executable, str(HERE / "phone_import.py")], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "moved 3 photos" in r.stdout
    assert sorted(os.listdir(state / "inbox/iphone")) == ["IMG_0004 (2).HEIC", "IMG_0004 (2).MOV", "IMG_[1].PNG"]
    assert (state / "inbox/iphone/IMG_0004 (2).HEIC").read_bytes() == b"new photo, longer"
    assert (stage / ".already-backed-up/2026/10/IMG_0100.HEIC").exists()                  # not uploaded twice
    assert (stage / "2026/10/.syncthing.IMG_0005.HEIC.tmp").exists()                      # Syncthing's, left alone
    ignored = (stage / ".stignore").read_text().splitlines()
    assert ignored[0] == "/.already-backed-up"
    assert set(ignored[1:]) == {"/2026/10/IMG_0004.HEIC", "/2026/10/IMG_0004.MOV", "/2026/10/IMG_0100.HEIC",
                                "/IMG_\\[1\\].PNG"}
    r = subprocess.run([sys.executable, str(HERE / "phone_import.py")], env=env, capture_output=True, text=True)
    assert "moved 0 photos" in r.stdout and (stage / ".stignore").read_text().count(".already-backed-up") == 1
