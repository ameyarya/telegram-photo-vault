"""DATE_FROM=modified: a photo's date is the file's date modified on the drive, not the date inside the photo."""
import os
import sqlite3
import subprocess
import time

from conftest import PY, ROOT, photo


def index(tmp_path, *args, **extra):
    env = {**os.environ, "GALLERY_DRIVE": str(tmp_path / "drive"), "GALLERY_DATA": str(tmp_path / "data"),
           "STATE_DIR": str(tmp_path / "state"), "GALLERY_TELEGRAM": "0", "FOLDERS": "", **extra}
    (tmp_path / "data").mkdir(exist_ok=True)
    subprocess.run([PY, "build_index.py", "--no-thumbs", *args], cwd=ROOT, env=env, check=True, capture_output=True)
    con = sqlite3.connect(tmp_path / "data/index.db")
    return dict(con.execute("SELECT name, taken_at FROM media"))


def test_date_from_the_files_modified_time(tmp_path):
    p = tmp_path / "drive/trip/a.jpg"
    photo(p, 64, 48, date="2019:05:06 07:08:09")
    q = tmp_path / "drive/trip/b.jpg"
    photo(q, 64, 48, date="2018:01:02 03:04:05")
    t = time.mktime((2024, 3, 4, 12, 0, 0, 0, 0, -1))
    os.utime(p, (t, t))
    os.utime(q, (t, t))
    assert index(tmp_path)["a.jpg"] == "2019-05-06T07:08:09"                      # default: the camera's date
    dates = index(tmp_path, "--redate", DATE_FROM="modified")
    assert dates["a.jpg"] == dates["b.jpg"] == "2024-03-04T12:00:00"             # now: date modified, existing photos too
    t2 = time.mktime((2025, 7, 8, 9, 10, 11, 0, 0, -1))
    c = tmp_path / "drive/trip/c.jpg"
    photo(c, 64, 48, date="2010:01:01 00:00:00")
    os.utime(c, (t2, t2))
    assert index(tmp_path, DATE_FROM="modified")["c.jpg"] == "2025-07-08T09:10:11"   # new photos too


def test_a_rebuilt_index_never_shows_the_old_thumbnails(tmp_path):
    """Thumbnails are named by photo id; after the index is rebuilt the old ones would show other photos."""
    photo(tmp_path / "drive/trip/a.jpg", 64, 48, color="c=red")
    env = {**os.environ, "GALLERY_DRIVE": str(tmp_path / "drive"), "GALLERY_DATA": str(tmp_path / "data"),
           "STATE_DIR": str(tmp_path / "state"), "GALLERY_TELEGRAM": "0", "FOLDERS": "", "HOME": str(tmp_path / "home")}
    (tmp_path / "data").mkdir()
    subprocess.run([PY, "build_index.py"], cwd=ROOT, env=env, check=True, capture_output=True)
    thumb = tmp_path / "data/thumbs/01/1.jpg"
    assert thumb.exists()
    thumb.write_bytes(b"from the old index")
    (tmp_path / "data/index.db").unlink()                                   # the index is lost and rebuilt
    for x in (tmp_path / "data").glob("index.db-*"):
        x.unlink()
    subprocess.run([PY, "build_index.py"], cwd=ROOT, env=env, check=True, capture_output=True)
    assert thumb.read_bytes() != b"from the old index"
    assert list((tmp_path / "home/.Trash").glob("photo-vault-old-cache-*"))


def test_favorites_and_albums_are_put_back_after_a_rebuild(tmp_path):
    import sys
    sys.path.insert(0, str(ROOT))
    import snapshots
    con = sqlite3.connect(":memory:")
    con.executescript("""CREATE TABLE media (id INTEGER PRIMARY KEY, path TEXT, taken_at TEXT, date_src TEXT);
        CREATE TABLE favorites (media_id INTEGER PRIMARY KEY, added TEXT);
        CREATE TABLE user_albums (id INTEGER PRIMARY KEY, name TEXT, cover_id INTEGER, created TEXT);
        CREATE TABLE user_album_items (album_id INTEGER, media_id INTEGER, added TEXT, PRIMARY KEY (album_id, media_id));
        CREATE TABLE trash (media_id INTEGER PRIMARY KEY, trashed_at TEXT, moved INTEGER, emptied INTEGER);
        CREATE TABLE date_fixes (media_id INTEGER PRIMARY KEY, orig_taken_at TEXT, orig_src TEXT, fixed_at TEXT);
        CREATE TABLE folder_names (path TEXT PRIMARY KEY, name TEXT);
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO media VALUES (1,'x/a.jpg','2020-01-01T00:00:00','exif'), (2,'x/b.jpg','2021-01-01T00:00:00','exif');
        INSERT INTO favorites(media_id) VALUES (2);
        INSERT INTO user_albums(id, name, cover_id) VALUES (1, 'Trip', 2);
        INSERT INTO user_album_items(album_id, media_id) VALUES (1, 1), (1, 2);""")
    data = snapshots.export_user_data(con)
    new = sqlite3.connect(":memory:")                                       # rebuilt: other ids
    new.executescript("""CREATE TABLE media (id INTEGER PRIMARY KEY, path TEXT, taken_at TEXT, date_src TEXT);
        CREATE TABLE favorites (media_id INTEGER PRIMARY KEY, added TEXT);
        CREATE TABLE user_albums (id INTEGER PRIMARY KEY, name TEXT, cover_id INTEGER, created TEXT);
        CREATE TABLE user_album_items (album_id INTEGER, media_id INTEGER, added TEXT, PRIMARY KEY (album_id, media_id));
        CREATE TABLE trash (media_id INTEGER PRIMARY KEY, trashed_at TEXT, moved INTEGER, emptied INTEGER);
        CREATE TABLE date_fixes (media_id INTEGER PRIMARY KEY, orig_taken_at TEXT, orig_src TEXT, fixed_at TEXT);
        CREATE TABLE folder_names (path TEXT PRIMARY KEY, name TEXT);
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO media VALUES (7,'x/b.jpg','2021-01-01T00:00:00','exif'), (9,'x/a.jpg','2020-01-01T00:00:00','exif');""")
    assert snapshots.import_user_data(new, data) == 3
    assert [r[0] for r in new.execute("SELECT media_id FROM favorites")] == [7]
    assert new.execute("SELECT cover_id FROM user_albums WHERE name='Trip'").fetchone()[0] == 7
    assert new.execute("SELECT count(*) FROM user_album_items").fetchone()[0] == 2


def test_thumbnails_are_cached_for_good_only_with_their_index_id(mlworld):
    """Thumbnails are named by photo id; a rebuilt index reuses ids, so a year-long cache must be tied to the index."""
    import httpx
    base = mlworld["url"]
    page = httpx.get(base + "/", verify=False).text
    v = page.split('data-v="')[1].split('"')[0]
    assert v and v != "__V__"
    plain = httpx.get(base + "/thumb/1", verify=False)
    assert plain.headers["cache-control"] == "no-cache"
    tied = httpx.get(base + f"/thumb/1?v={v}", verify=False)
    assert "immutable" in tied.headers["cache-control"]


def test_telegram_only_photos_come_back_after_a_rebuild(tmp_path):
    """CLOUD_ONLY folders live only in Telegram; a rebuilt index must bring them back from there."""
    store = tmp_path / "tg"
    photo(store / "f1.jpg", 64, 48, date="2019:08:09 10:11:12")
    photo(store / "f2.jpg", 64, 48)
    (store / "messages.json").write_text('[{"id": 5, "caption": "Cloud/a.jpg", "file": "f1.jpg"},'
                                         ' {"id": 6, "caption": "Cloud/b.jpg", "file": "f2.jpg"},'
                                         ' {"id": 7, "caption": "Other/c.jpg", "file": "f1.jpg"}]')
    (tmp_path / "drive").mkdir()
    (tmp_path / "data").mkdir()
    env = {**os.environ, "GALLERY_DRIVE": str(tmp_path / "drive"), "GALLERY_DATA": str(tmp_path / "data"),
           "STATE_DIR": str(tmp_path / "state"), "GALLERY_FAKE_TG": str(store), "CLOUD_ONLY_FOLDERS": "Cloud",
           "CLOUD_SEED_PACE": "0", "FOLDERS": "", "DATE_FROM": "modified"}
    (tmp_path / "state").mkdir()
    con = sqlite3.connect(tmp_path / "data/index.db")
    con.executescript("CREATE TABLE tg_msgs (path TEXT PRIMARY KEY, msg_id INTEGER NOT NULL);")
    con.executemany("INSERT INTO tg_msgs VALUES (?, ?)", [("Cloud/a.jpg", 5), ("Cloud/b.jpg", 6), ("Other/c.jpg", 7)])
    con.commit()
    con.close()
    code = "import asyncio, cloud_seed, tg\nc, cfg = tg.client_from_env()\nprint(asyncio.run(cloud_seed.run(c, cfg)))"
    for _ in range(2):                                                          # the second run has nothing left to do
        r = subprocess.run([PY, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "0"
    rows = sqlite3.connect(tmp_path / "data/index.db").execute(
        "SELECT path, taken_at, date_src, msg_id, thumb FROM media ORDER BY path").fetchall()
    assert [x[0] for x in rows] == ["Cloud/a.jpg", "Cloud/b.jpg"]               # only the CLOUD_ONLY folder
    assert rows[0][1:] == ("2019-08-09T10:11:12", "exif", 5, 1)                 # the camera's date, with a thumbnail
    assert rows[1][2:4] == ("file", 6)                                          # no camera date: the upload day
    subprocess.run([PY, "build_index.py", "--no-thumbs", "--redate"], cwd=ROOT, env=env, check=True, capture_output=True)
    after = sqlite3.connect(tmp_path / "data/index.db").execute("SELECT taken_at FROM media WHERE path='Cloud/a.jpg'").fetchone()
    assert after[0] == "2019-08-09T10:11:12"                                    # a rebuild keeps their dates


def test_a_telegram_only_photo_gets_its_thumbnail_when_first_looked_at(cloud):
    """Seeded Telegram-only photos have no file anywhere on the Mac: the gallery reads it from Telegram on demand."""
    import httpx
    msg = next(m for m in cloud["messages"] if m["caption"].lower().endswith(".jpg"))
    db = sqlite3.connect(cloud["env"]["GALLERY_DATA"] + "/index.db")
    db.execute("""INSERT INTO media(path, album, name, kind, ext, taken_at, date_src, size, mtime, thumb, msg_id)
                  VALUES ('CloudOnly/x.jpg', 'CloudOnly', 'x.jpg', 'image', 'jpg', '2015-05-05T05:05:05', 'exif', 1, 0, 0, ?)""",
               (msg["id"],))
    db.commit()
    mid = db.execute("SELECT id FROM media WHERE path = 'CloudOnly/x.jpg'").fetchone()[0]
    r = httpx.get(cloud["url"] + f"/thumb/{mid}", timeout=30)
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:2] == b"\xff\xd8"
    assert db.execute("SELECT thumb FROM media WHERE id = ?", (mid,)).fetchone()[0] == 1
