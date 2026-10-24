"""watcher.py (real-time backup) with a fake uploader, indexer and iCloud export."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "watcher.py"


@pytest.fixture
def w(tmp_path):
    state, drive = tmp_path / "state", tmp_path / "drive"
    (state / "inbox" / "her-phone").mkdir(parents=True)
    drive.mkdir()
    upload = tmp_path / "up.py"     # "uploads" every file it's given: adds the inbox's files to done.log
    upload.write_text(f"""import os
inbox = {str(state / 'inbox')!r}
with open({str(state / 'done.log')!r}, 'a') as d:
    for dp, dn, fn in os.walk(inbox):
        dn[:] = [x for x in dn if not x.startswith('.')]
        for f in fn:
            if not f.startswith('.'):
                d.write(os.path.relpath(os.path.join(dp, f), inbox) + '\\n')
open({str(state / 'uploaded')!r}, 'a').write('x')
""")
    icloud = tmp_path / "icloud.py"  # pretends iCloud brought one photo
    icloud.write_text(f"""import os
os.makedirs({str(state / 'inbox/iphone')!r}, exist_ok=True)
open({str(state / 'inbox/iphone/IMG_1.HEIC')!r}, 'wb').write(b'from icloud')
""")
    env = {**os.environ, "VAULT_CONFIG": os.devnull, "STATE_DIR": str(state), "GALLERY_DRIVE": str(drive),
           "GALLERY_DATA": str(tmp_path / "data"), "FOLDERS": "",
           "WATCH_UPLOAD": f"{sys.executable} {upload}", "WATCH_INDEX": "true", "WATCH_ML": "true",
           "WATCH_NOTIFY": "0", "WATCH_SETTLE": "0", "WATCH_USED": "echo 1000", "WATCH_BUSY_PATTERN": "no-such-xyz",
           "WATCH_LOW_GB": "0"}

    def once(**extra):
        r = subprocess.run([sys.executable, str(SCRIPT), "--once"], env={**env, **extra}, capture_output=True, text=True,
                           timeout=60)
        assert r.returncode == 0, r.stderr
        return (state / "watcher.log").read_text()

    return {"once": once, "state": state, "drive": drive, "tmp": tmp_path, "icloud": icloud}


def test_new_phone_photo_is_backed_up_and_moved_to_the_drive(w):
    (w["state"] / "inbox/her-phone/20261020_101010.jpg").write_bytes(b"new photo")
    log = w["once"]()
    assert "backing up" in log and (w["state"] / "uploaded").exists()
    assert (w["drive"] / "her-phone/20261020_101010.jpg").read_bytes() == b"new photo"      # same path on the drive
    assert not (w["state"] / "inbox/her-phone/20261020_101010.jpg").exists()
    assert (w["state"] / "inbox/her-phone").is_dir()                                    # Syncthing's folder stays


def test_drive_unplugged_still_backs_up_and_keeps_the_file(w):
    (w["state"] / "inbox/her-phone/a.jpg").write_bytes(b"x")
    import shutil
    shutil.rmtree(w["drive"])
    w["once"]()
    assert (w["state"] / "uploaded").exists() and (w["state"] / "inbox/her-phone/a.jpg").exists()
    w["drive"].mkdir()                                                                  # plugged in later
    w["once"]()
    assert (w["drive"] / "her-phone/a.jpg").exists() and not (w["state"] / "inbox/her-phone/a.jpg").exists()


def test_nothing_new_does_nothing(w):
    w["drive"].rmdir()
    log = w["once"]()
    assert not (w["state"] / "uploaded").exists() and "backing up" not in log


def test_icloud_photos_are_exported_then_backed_up(w):
    w["drive"].rmdir()
    log = w["once"](WATCH_ICLOUD=f"{sys.executable} {w['icloud']}", WATCH_PHOTOS_MARKER="5:123")
    assert "checking iCloud Photos" in log and (w["state"] / "uploaded").exists()
    assert "iphone/IMG_1.HEIC" in (w["state"] / "done.log").read_text()


def test_unfinished_files_are_left_alone(w):
    d = w["state"] / "inbox/her-phone"
    (d / ".syncthing.photo.jpg.tmp").write_bytes(b"half")
    (d / "upload.jpg.part").write_bytes(b"half")
    w["drive"].rmdir()
    log = w["once"]()
    assert "backing up" not in log


def test_video_too_big_for_telegram_moves_to_the_drive_and_is_split(w):
    """Regression: 3-4 GB DJI videos stayed in the Mac's inbox (never uploadable whole), filling the Mac's disk."""
    v = w["state"] / "inbox/iphone/dji_video.mp4"
    v.parent.mkdir(parents=True)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=6:size=320x240:rate=25",
                    "-c:v", "libx264", "-g", "25", str(v)], check=True)
    size = v.stat().st_size
    log = w["once"](SPLIT_LIMIT=str(size // 2), WATCH_SPLIT_TARGET=str(size // 3))       # "too big", parts of a third
    assert (w["drive"] / "iphone/dji_video.mp4").stat().st_size == size and not v.exists()
    parts = sorted(os.listdir(w["drive"] / "_split/iphone"))
    assert len(parts) >= 2 and parts[0] == "dji_video_part00.mp4" and "split iphone/dji_video.mp4" in log
    assert (w["drive"] / "_split/.complete").exists()


def test_big_video_waits_quietly_while_the_drive_is_unplugged(w):
    (w["state"] / "inbox/her-phone/big.mp4").write_bytes(b"x" * 5000)
    w["drive"].rmdir()
    log = w["once"](SPLIT_LIMIT="1000")
    assert "backing up" not in log and (w["state"] / "inbox/her-phone/big.mp4").exists()


def test_unreadable_photos_library_is_reported_once(w):
    lib = w["tmp"] / "Photos Library.photoslibrary"
    (lib / "database").mkdir(parents=True)
    (lib / "database" / "Photos.sqlite").write_bytes(b"not a database")
    w["drive"].rmdir()
    log = w["once"](WATCH_ICLOUD="true", PHOTOS_LIBRARY=str(lib))
    assert "can't read the Photos library" in log and "Give Python Full Disk Access" in log
    log = w["once"](WATCH_ICLOUD="true", PHOTOS_LIBRARY=str(lib))
    assert log.count("Give Python Full Disk Access") == 1                         # not every minute


def test_telegram_only_folder_never_goes_to_the_drive_and_leaves_the_mac_once_safe(w):
    import sqlite3
    f = w["state"] / "inbox/old-account/old.jpg"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"old google photo")
    data = w["tmp"] / "data"
    (data / "thumbs/01").mkdir(parents=True)
    con = sqlite3.connect(data / "index.db")
    con.execute("CREATE TABLE media (id INTEGER PRIMARY KEY, path TEXT, msg_id INT)")
    con.execute("INSERT INTO media VALUES (1, 'old-account/old.jpg', NULL)")
    con.commit()
    w["once"](CLOUD_ONLY_FOLDERS="old-account")
    assert (w["state"] / "uploaded").exists() and not (w["drive"] / "old-account").exists()   # uploaded, not on the drive
    assert f.exists()                                       # not linked to Telegram in the gallery yet: kept
    con.execute("UPDATE media SET msg_id = 99"); con.commit()
    w["once"](CLOUD_ONLY_FOLDERS="old-account")
    assert f.exists()                                       # no thumbnail yet: kept
    (data / "thumbs/01/1.jpg").write_bytes(b"thumb")
    log = w["once"](CLOUD_ONLY_FOLDERS="old-account")
    assert not f.exists() and not (w["drive"] / "old-account/old.jpg").exists() and "removed 1 Telegram-only" in log
