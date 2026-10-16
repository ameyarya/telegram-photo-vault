"""Server + indexer behaviour against the fake drive."""
import socket

import httpx
import pytest


def test_indexer_picks_media_and_skips_junk(items):
    assert set(items) == {"beach.jpg", "sunset.jpg", "pier.jpg", "portrait.jpg", "clip.mp4", "IMG_0001.heic",
                          "screenshot.png", "film.mp4 (part 1)", "film.mp4 (part 2)", "group.jpg"}


def test_albums_follow_folders_and_split_parts_map_back(api):
    albums = {a["album"]: a for a in api.get("/api/albums").json()}
    assert set(albums) == {"vacation", "family/wedding", "family/wedding/day 1", "family/wedding/day 2", "iphone", "family/videos"}
    assert albums["vacation"]["n"] == 3
    assert albums["family/wedding/day 1"]["videos"] == 1
    assert albums["vacation"]["cover"] is not None


def test_dates_come_from_exif_or_file(items):
    assert items["beach.jpg"]["taken_at"] == "2024-02-07T10:00:00"
    assert items["beach.jpg"]["date_src"] == "exif"
    assert items["IMG_0001.heic"]["taken_at"] == "2024-01-20T12:00:00"
    assert items["screenshot.png"]["date_src"] == "file"
    assert items["screenshot.png"]["taken_at"].startswith("2023-05-01")
    assert items["clip.mp4"]["taken_at"].startswith("2024-01-19")


def test_items_sorted_by_date(api):
    dates = [i["taken_at"] for i in api.get("/api/items").json()["items"]]
    assert dates == sorted(dates)


def test_sideways_photo_reports_portrait_size(items):
    p = items["portrait.jpg"]
    assert (p["width"], p["height"]) == (800, 1200)
    assert (items["beach.jpg"]["width"], items["beach.jpg"]["height"]) == (1200, 800)


def test_album_filter_with_and_without_subfolders(api):
    sub = api.get("/api/items", params={"album": "family/wedding"}).json()
    assert sub["total"] == 4
    exact = api.get("/api/items", params={"album": "family/wedding", "sub": "false"}).json()
    assert [i["name"] for i in exact["items"]] == ["group.jpg"]
    # a prefix that isn't a folder boundary must not match ("shad" is not "family")
    assert api.get("/api/items", params={"album": "shad"}).json()["total"] == 0


def test_paging(api):
    first = api.get("/api/items", params={"limit": 4}).json()
    rest = api.get("/api/items", params={"limit": 4, "offset": 4}).json()
    assert first["total"] == rest["total"] == 10
    ids = [i["id"] for i in first["items"] + rest["items"]]
    assert len(ids) == len(set(ids)) == 8


def test_thumbnails_are_made_on_demand(api, items, world):
    for name in ("beach.jpg", "IMG_0001.heic", "clip.mp4", "screenshot.png"):
        r = api.get(f"/thumb/{items[name]['id']}")
        assert r.status_code == 200, name
        assert r.headers["content-type"] == "image/jpeg"
        assert r.content[:2] == b"\xff\xd8", name


def test_view_converts_heic_to_jpeg(api, items):
    r = api.get(f"/view/{items['IMG_0001.heic']['id']}")
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"


def test_original_supports_ranges_and_exact_bytes(api, items, world):
    it = items["clip.mp4"]
    whole = (world["drive"] / "family/wedding/day 1/clip.mp4").read_bytes()
    r = api.get(f"/original/{it['id']}", headers={"Range": "bytes=100-199"})
    assert r.status_code == 206
    assert r.content == whole[100:200]
    assert api.get(f"/original/{it['id']}").content == whole


def test_download_keeps_original_filename(api, items):
    r = api.get(f"/original/{items['IMG_0001.heic']['id']}", params={"download": 1})
    assert "IMG_0001.heic" in r.headers["content-disposition"]


def test_unknown_item_is_404(api):
    assert api.get("/view/999999").status_code == 404
    assert api.get("/thumb/999999").status_code == 404


def test_no_write_or_delete_endpoints(api, items):
    i = items["beach.jpg"]["id"]
    for method, url in [("DELETE", f"/original/{i}"), ("POST", "/api/items"), ("PUT", f"/view/{i}"),
                        ("DELETE", "/api/albums")]:
        assert api.request(method, url).status_code in (404, 405), (method, url)


def test_status(api):
    s = api.get("/api/status").json()
    assert s["total"] == 10 and s["drive"] is True and s["telegram"] is False


def test_unplugged_drive_without_telegram_explains_itself(unplugged, items, api):
    i = items["sunset.jpg"]["id"]
    api.get(f"/thumb/{i}")  # cached thumbnail keeps working when the drive is gone
    assert unplugged.get(f"/thumb/{i}").status_code == 200
    r = unplugged.get(f"/view/{i}")
    assert r.status_code == 503 and "not in Telegram yet" in r.json()["detail"]
    assert unplugged.get("/api/status").json()["drive"] is False


def lan_ip():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 9))  # no packets sent; just picks the outgoing interface
            return s.getsockname()[0]
        except OSError:
            return None


def test_other_devices_are_refused(world):
    """The real service listens on all interfaces for port 80, so the app itself must refuse non-local clients."""
    ip = lan_ip()
    if not ip or ip.startswith("127."):
        pytest.skip("no network interface")
    import os, subprocess, sys, time
    from conftest import ROOT, free_port
    port = free_port()
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", str(port)],
                            cwd=ROOT, env=world["env"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                local = httpx.get(f"http://127.0.0.1:{port}/api/status", timeout=1)
                break
            except httpx.HTTPError:
                time.sleep(0.1)
        assert local.status_code == 200
        assert httpx.get(f"http://{ip}:{port}/api/status", timeout=3).status_code == 403
    finally:
        proc.terminate()
        proc.wait()


def test_server_stays_responsive_while_index_is_busy(world, api, items):
    """Regression: a write lock held by the indexer used to freeze the whole server."""
    import sqlite3, time
    con = sqlite3.connect(world["data"] / "index.db")
    con.execute("BEGIN IMMEDIATE")  # what the indexer holds while writing
    try:
        t = time.time()
        r = api.get(f"/thumb/{items['pier.jpg']['id']}")  # uncached: makes a thumbnail and tries to record it
        assert r.status_code == 200
        assert time.time() - t < 5
        assert api.get("/api/status", timeout=3).status_code == 200
        assert api.get("/api/items", timeout=3).json()["total"] == 10
    finally:
        con.rollback()
        con.close()


def test_skipped_duplicate_shares_its_twins_upload(world, items, tmp_path):
    """A duplicate the uploader skipped is linked to the Telegram message of the identical copy that was uploaded."""
    import os, sqlite3, subprocess, sys
    from conftest import ROOT
    dups = tmp_path / "duplicates.tsv"
    dups.write_text("vacation/pier.jpg\tvacation/beach.jpg\n")      # pretend pier is a copy of beach
    code = ("import db, sqlite3; con = db.connect();"
            "con.execute(\"INSERT OR REPLACE INTO tg_msgs VALUES ('vacation/beach.jpg', 4242)\");"
            "print(db.link_uploads(con))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
                         env={**world["env"], "GALLERY_DUPLICATES": str(dups)})
    assert out.returncode == 0, out.stderr
    con = sqlite3.connect(world["data"] / "index.db")
    ids = dict(con.execute("SELECT name, msg_id FROM media WHERE album='vacation'"))
    assert ids["beach.jpg"] == ids["pier.jpg"] == 4242
    assert ids["sunset.jpg"] is None
    con.execute("UPDATE media SET msg_id=NULL")  # leave the shared index as other tests expect
    con.execute("DELETE FROM tg_msgs")
    con.commit()
