"""guard.py: a folder renamed or copied in Finder is recognised instead of being uploaded to Telegram again, and a
surprise flood of new drive files waits for an OK."""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent.parent
DRIVER = f"""
import json, sys
sys.path[:0] = [{str(HERE)!r}, {str(HERE.parent / 'gallery')!r}]
import guard
todo = json.loads(sys.argv[1])
if sys.argv[2] == "reconcile":
    print(json.dumps(guard.reconcile(todo, log=lambda m: None)))
else:
    print(json.dumps(guard.hold_big_surprises(todo, log=lambda m: None)))
"""


@pytest.fixture
def g(tmp_path):
    state, drive, data = tmp_path / "state", tmp_path / "drive", tmp_path / "data"
    for d in (state / "inbox", drive, data):
        d.mkdir(parents=True)
    sys.path[:0] = [str(HERE.parent / "gallery")]
    from db import SCHEMA
    sys.path.pop(0)
    con = sqlite3.connect(data / "index.db")
    con.executescript(SCHEMA)
    con.commit()
    env = {**os.environ, "VAULT_CONFIG": os.devnull, "STATE_DIR": str(state), "GALLERY_DRIVE": str(drive),
           "GALLERY_DATA": str(data), "FOLDERS": "", "UPLOAD_HOLD": "3"}

    def run(what, todo):
        r = subprocess.run([sys.executable, "-c", DRIVER, json.dumps(todo), what], env=env, capture_output=True,
                           text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)

    def backed_up(rel, data_bytes, msg):
        p = drive / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data_bytes)
        st = p.stat()
        cur = con.execute("INSERT INTO media(path, album, name, kind, ext, taken_at, date_src, size, mtime, msg_id) "
                          "VALUES (?, ?, ?, 'photo', 'jpg', '2026-01-01T00:00:00', 'file', ?, ?, ?)",
                          (rel, rel.rsplit("/", 1)[0], rel.rsplit("/", 1)[1], st.st_size, st.st_mtime, msg))
        con.execute("INSERT INTO favorites(media_id) VALUES (?)", (cur.lastrowid,))
        con.execute("INSERT INTO tg_msgs(path, msg_id) VALUES (?, ?)", (rel, msg))
        con.commit()
        (state / "done.log").open("a").write(rel + "\n")
        return cur.lastrowid

    return {"run": run, "backed_up": backed_up, "con": con, "drive": drive, "state": state}


def test_a_folder_renamed_in_finder_is_not_uploaded_again(g):
    """Regression: renaming a phone's folder on the drive re-uploaded every photo in it."""
    mid = g["backed_up"]("her-phone/a.jpg", b"photo a", 11)
    (g["drive"] / "her-phone").rename(g["drive"] / "samsung")
    assert g["run"]("reconcile", ["samsung/a.jpg"]) == []                       # nothing to upload
    con = g["con"]
    assert con.execute("SELECT path, album, name FROM media WHERE id = ?", (mid,)).fetchone() == \
        ("samsung/a.jpg", "samsung", "a.jpg")                                    # same photo: favorites etc. kept
    assert con.execute("SELECT msg_id FROM tg_msgs WHERE path = 'samsung/a.jpg'").fetchone() == (11,)
    assert con.execute("SELECT new FROM path_moves WHERE old = 'her-phone/a.jpg'").fetchone() == ("samsung/a.jpg",)
    assert "samsung/a.jpg" in (g["state"] / "done.log").read_text().splitlines()


def test_a_copied_folder_is_skipped_as_duplicates(g):
    g["backed_up"]("trip/a.jpg", b"photo a", 12)
    (g["drive"] / "trip copy").mkdir()
    (g["drive"] / "trip copy/a.jpg").write_bytes(b"photo a")
    assert g["run"]("reconcile", ["trip copy/a.jpg"]) == []
    assert (g["state"] / "duplicates.tsv").read_text() == "trip copy/a.jpg\ttrip/a.jpg\n"
    assert (g["state"] / "skip.txt").read_text() == "trip copy/a.jpg\n"


def test_really_new_or_different_files_are_still_uploaded(g):
    g["backed_up"]("trip/a.jpg", b"photo a", 13)
    (g["drive"] / "other").mkdir()
    (g["drive"] / "other/a.jpg").write_bytes(b"photo b")                         # same name and size, other bytes
    (g["drive"] / "other/new.jpg").write_bytes(b"new")
    assert g["run"]("reconcile", ["other/a.jpg", "other/new.jpg"]) == ["other/a.jpg", "other/new.jpg"]
    assert not (g["state"] / "duplicates.tsv").exists()


def test_unplugged_drive_never_counts_as_a_move(g):
    g["backed_up"]("trip/a.jpg", b"photo a", 14)
    (g["state"] / "inbox/phone").mkdir()
    os.replace(g["drive"] / "trip/a.jpg", g["state"] / "inbox/phone/a.jpg")
    (g["drive"] / "trip").rmdir()
    g["drive"].rmdir()
    assert g["run"]("reconcile", ["phone/a.jpg"]) == ["phone/a.jpg"]


def test_a_surprise_flood_of_drive_files_waits_for_an_ok(g):
    (g["state"] / "inbox/phone").mkdir()
    (g["state"] / "inbox/phone/p.jpg").write_bytes(b"p")
    drive_files = [f"big/{i}.jpg" for i in range(4)]                            # over UPLOAD_HOLD=3
    now, held = g["run"]("hold", drive_files + ["phone/p.jpg"])
    assert now == ["phone/p.jpg"] and held == drive_files                        # the phone keeps flowing
    hold = json.loads((g["state"] / "upload-hold.json").read_text())
    assert hold["files"] == 4 and hold["folders"] == {"big": 4}
    (g["state"] / ".upload-approved").touch()                                     # "Upload them" on the Backup page
    now, held = g["run"]("hold", drive_files)
    assert now == drive_files and held == []
    assert not (g["state"] / "upload-hold.json").exists() and not (g["state"] / ".upload-approved").exists()
    assert g["run"]("hold", drive_files[:3]) == [drive_files[:3], []]            # a normal number goes straight up
