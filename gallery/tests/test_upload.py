"""Backing up a phone from its browser: files land in the person's own folder on the drive, then the gallery."""
import os
import time

import httpx
import pytest

from conftest import build_drive, make_world, serve, shot, start_server, stop
from test_auth import sign_in, start_server_remote


@pytest.fixture(scope="module")
def upworld(tmp_path_factory):
    w = serve(make_world(tmp_path_factory.mktemp("up"), build_drive))
    yield w
    stop(w)


@pytest.fixture(scope="module")
def mac(upworld):
    with httpx.Client(base_url=upworld["url"], timeout=60) as c:
        yield c


def put(c, name, data, modified=1700000000000):
    return c.put("/api/upload", params={"name": name, "size": len(data), "modified": modified}, content=data)


def test_upload_lands_in_its_folder_with_the_phones_date(mac, upworld):
    r = put(mac, "PXL_1.jpg", b"photo-one")
    assert r.json() == {"saved": True, "skipped": False, "path": "mac-uploads/PXL_1.jpg", "waiting": False}
    f = upworld["drive"] / "mac-uploads/PXL_1.jpg"
    assert f.read_bytes() == b"photo-one" and int(f.stat().st_mtime) == 1700000000
    assert mac.get("/api/upload/check", params={"name": "PXL_1.jpg", "size": 9}).json()["exists"] is True
    assert put(mac, "PXL_1.jpg", b"photo-one").json()["skipped"] is True                 # already backed up
    assert put(mac, "PXL_1.jpg", b"another photo").json()["path"] == "mac-uploads/PXL_1 (2).jpg"  # never overwrites
    assert f.read_bytes() == b"photo-one"


def test_bad_uploads_leave_nothing_behind(mac, upworld):
    d = upworld["drive"] / "mac-uploads"
    assert put(mac, "notes.txt", b"x").status_code == 415
    assert put(mac, "../../escape.jpg", b"x").json()["path"] == "mac-uploads/escape.jpg"   # no climbing out
    assert not (upworld["drive"].parent / "escape.jpg").exists()
    r = mac.put("/api/upload", params={"name": "short.jpg", "size": 100}, content=b"only ten b")
    assert r.status_code == 400 and not (d / "short.jpg").exists()
    assert not [p for p in os.listdir(d) if p.endswith(".part")]                           # no half files
    assert put(mac, "empty.jpg", b"").status_code == 400


def test_batch_done_adds_them_to_the_gallery(mac):
    assert mac.post("/api/upload/done").json() == {"indexing": True}
    for _ in range(100):
        names = {i["name"] for i in mac.get("/api/items", params={"album": "mac-uploads"}).json()["items"]}
        if "PXL_1.jpg" in names:
            break
        time.sleep(0.2)
    assert {"PXL_1.jpg", "PXL_1 (2).jpg"} <= names


def test_each_person_gets_their_own_folder(mac, upworld):
    env = {**upworld["env"], "GALLERY_LOOPBACK_IS_REMOTE": "1"}
    proc, url = start_server_remote(env)
    try:
        mac.post("/api/users", json={"name": "Asha K", "password": "sunflower-42"})
        mac.post("/api/sharing", json={"on": True})
        with httpx.Client(base_url=url, timeout=60) as p:
            assert put(p, "a.jpg", b"x").status_code == 401                                 # signed out: refused
            sign_in(p, "Asha K", "sunflower-42")
            assert p.get("/api/upload/info").json()["folder"] == "asha-k-phone"
            assert put(p, "a.jpg", b"from her phone").json()["path"] == "asha-k-phone/a.jpg"   # view-only can upload
        assert (upworld["drive"] / "asha-k-phone/a.jpg").read_bytes() == b"from her phone"
        assert any(u["folder"] == "asha-k-phone" for u in mac.get("/api/users").json()["users"])
    finally:
        proc.terminate()
        mac.post("/api/sharing", json={"on": False})


def test_drive_unplugged_files_wait_on_the_mac(upworld):
    env = {**upworld["env"], "GALLERY_DRIVE": str(upworld["drive"]) + "-missing"}
    proc, url = start_server(env, __import__("conftest").free_port())
    try:
        with httpx.Client(base_url=url, timeout=60) as c:
            r = put(c, "late.jpg", b"while unplugged").json()
            assert r["waiting"] is True
        waiting = upworld["state"] / "inbox/mac-uploads/late.jpg"
        assert waiting.read_bytes() == b"while unplugged"
        with httpx.Client(base_url=url, timeout=60) as c:
            assert c.post("/api/upload/done").json() == {"indexing": True}              # shown even while unplugged
            for _ in range(100):
                items = c.get("/api/items", params={"album": "mac-uploads"}).json()["items"]
                if any(i["name"] == "late.jpg" for i in items):
                    break
                time.sleep(0.2)
            else:
                raise AssertionError("not in the gallery")
    finally:
        proc.terminate()
    import subprocess, sys
    move = [sys.executable, "-c", "import upload; print(upload.move_waiting())"]
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    subprocess.run(move, cwd=here, env=upworld["env"], check=True)
    assert waiting.exists()                                    # not in Telegram yet: stays in the inbox
    with open(upworld["state"] / "done.log", "a") as d:
        d.write("mac-uploads/late.jpg\n")
    subprocess.run(move, cwd=here, env=upworld["env"], check=True)
    assert (upworld["drive"] / "mac-uploads/late.jpg").read_bytes() == b"while unplugged" and not waiting.exists()


def test_status_tells_backed_up_on_drive_and_missing(mac, upworld):
    import sqlite3
    put(mac, "S_1.jpg", b"one"), put(mac, "S_2.jpg", b"two")
    con = sqlite3.connect(upworld["data"] / "index.db")
    con.execute("INSERT INTO media(path, album, name, kind, ext, taken_at, date_src, size, msg_id) VALUES "
                "('mac-uploads/S_1.jpg', 'mac-uploads', 'S_1.jpg', 'image', 'jpg', '2024-01-01T00:00:00', 'file', 3, 999)")
    con.commit()
    r = mac.post("/api/upload/status", json={"files": [{"name": "S_1.jpg", "size": 3}, {"name": "S_2.jpg", "size": 3},
                                                      {"name": "S_2.jpg", "size": 4}, {"name": "S_3.jpg", "size": 3},
                                                      {"name": "../x.txt", "size": 1}]}).json()
    assert r["status"] == ["backed_up", "on_drive", "missing", "missing", "missing"]
    # (same name, other size: a different photo, not backed up)


@pytest.mark.parametrize("page", [{"world": "upworld", "viewport": {"width": 390, "height": 844}}], indirect=True)
def test_ui_pick_see_status_then_upload(page, tmp_path, upworld):
    a, b = tmp_path / "IMG_9001.jpg", tmp_path / "VID_9002.mp4"
    a.write_bytes(b"jpg bytes"), b.write_bytes(b"mp4 bytes" * 1000)
    page.goto(page.base + "/#/upload")
    page.locator("text=mac-uploads").first.wait_for()
    page.set_input_files("#upFiles", [str(a), str(b)])
    page.locator("#upGo", has_text="Upload 2 photos and videos").wait_for()          # checked first, nothing sent yet
    assert not (upworld["drive"] / "mac-uploads/IMG_9001.jpg").exists()
    assert page.locator(".up-tile .up-badge", has_text="Not backed up").count() == 2
    page.click("#upGo")
    page.locator("#upNow", has_text="Done").wait_for(timeout=30000)
    assert page.locator("#upD").inner_text() == "2"                                   # on the hard disk now
    page.locator(".chip[data-f=on_drive]").click()
    assert page.locator(".up-tile .up-badge", has_text="On hard disk").count() == 2
    shot(page, "upload-status")
    assert (upworld["drive"] / "mac-uploads/VID_9002.mp4").stat().st_size == 9000
    page.set_input_files("#upFiles", [str(a), str(b)])                                 # picking again: nothing to send
    page.locator("#upD", has_text="2").wait_for()
    assert page.locator("#upGo").count() == 0
