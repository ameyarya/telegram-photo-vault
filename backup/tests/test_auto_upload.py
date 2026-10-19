"""auto_upload.py: lists the drive in Python (what a background job may read on macOS), with upload.sh's rules."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "auto_upload.py"
FILES = {"Phone/a.jpg": b"x", "Phone/sub/b.mov": b"xy", "Phone/.hidden.jpg": b"x", "Phone/c.AAE": b"x",
         "Phone/empty.jpg": b"", "Wedding/d.jpg": b"x", "New trip/e.heic": b"x", ".photo-vault-trash/Phone/f.jpg": b"x",
         "$RECYCLE.BIN/g.jpg": b"x", "_split/h.mp4": b"x", "Phone/Lib.photoslibrary/originals/A/i.heic": b"x",
         "Phone/Lib.photoslibrary/resources/j.jpg": b"x"}


@pytest.fixture
def drive(tmp_path):
    d = tmp_path / "drive"
    for rel, data in FILES.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_bytes(data)
    (tmp_path / "state").mkdir()
    return d


def run(tmp_path, drive, *args, **env):
    e = {**os.environ, "DRIVE": str(drive), "STATE_DIR": str(tmp_path / "state"), "FOLDERS": "",
         "VAULT_CONFIG": str(tmp_path / "no-config.env"), "TG_BOT_TOKEN": "", **env}
    return subprocess.run([sys.executable, str(SCRIPT), *args], env=e, capture_output=True, text=True)


def test_lists_every_folder_with_the_uploaders_rules(tmp_path, drive):
    r = run(tmp_path, drive, "--list")
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "state/all.txt").read_text().splitlines() == [
        "New trip/e.heic", "Phone/Lib.photoslibrary/originals/A/i.heic", "Phone/a.jpg", "Phone/sub/b.mov", "Wedding/d.jpg"]
    r = run(tmp_path, drive, "--list", FOLDERS="Wedding|Phone")      # configured folders, in that order
    assert (tmp_path / "state/all.txt").read_text().splitlines()[0] == "Wedding/d.jpg"


def test_skips_done_and_duplicates_and_needs_a_bot(tmp_path, drive):
    (tmp_path / "state/done.log").write_text("Phone/a.jpg\n")
    (tmp_path / "state/skip.txt").write_text("Wedding/d.jpg\n")
    r = run(tmp_path, drive)
    assert "5 files on the drive, 3 to upload" in r.stdout
    assert r.returncode == 1 and "no bot tokens" in r.stdout


def test_never_empties_the_list(tmp_path, drive):
    run(tmp_path, drive, "--list")
    empty = tmp_path / "empty"
    empty.mkdir()
    r = run(tmp_path, empty)
    assert r.returncode == 1 and "not touching the list" in r.stdout   # a blocked scan once wiped it
    assert len((tmp_path / "state/all.txt").read_text().splitlines()) == 5
    r = run(tmp_path, tmp_path / "unplugged")
    assert r.returncode == 1 and "not plugged in" in r.stdout


def test_background_backup_uses_it():
    script = (SCRIPT.parent / "autobackup.py").read_text()
    assert "auto_upload.py" in script and "bot_upload.py run" in script
