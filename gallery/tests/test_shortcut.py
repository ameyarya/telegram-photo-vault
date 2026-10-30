"""An iPhone's Shortcuts automation backs it up: a token for one folder, photos since the last run, each kept once."""
from datetime import datetime, timedelta

import httpx
import pytest

from conftest import build_drive, make_world, serve, stop
from test_auth import start_server_remote


@pytest.fixture(scope="module")
def sw(tmp_path_factory):
    w = serve(make_world(tmp_path_factory.mktemp("shortcut"), build_drive))
    with httpx.Client(base_url=w["url"], timeout=30) as mac:
        w["mac"] = mac
        w["token"] = mac.post("/api/shortcut/token", json={"folder": "iphone"}).json()["token"]
        proc, url = start_server_remote({**w["env"], "GALLERY_LOOPBACK_IS_REMOTE": "1"})   # the phone, on the Wi-Fi
        with httpx.Client(base_url=url, timeout=30, headers={"Authorization": f"Bearer {w['token']}"}) as phone:
            w["phone"], w["phone_url"] = phone, url
            yield w
        proc.terminate()
        proc.wait()
    stop(w)


def send(c, name, data):
    return c.post("/api/shortcut/upload", files={"file": (name, data, "application/octet-stream")})


def test_photos_land_in_the_folders_staging_area_once(sw):
    stage = sw["state"] / "phone-sync/iphone"
    assert send(sw["phone"], "IMG_0001.HEIC", b"first").json()["saved"] == "IMG_0001.HEIC"
    assert send(sw["phone"], "IMG_0001.HEIC", b"first").json()["skipped"] == "already sent"   # the overlap: kept once
    assert send(sw["phone"], "IMG_0001.HEIC", b"other").json()["saved"] == "IMG_0001 (2).HEIC"  # never overwrites
    assert send(sw["phone"], "IMG_0002.MOV", b"video").status_code == 200
    assert sorted(p.name for p in stage.iterdir() if not p.name.startswith(".")) == \
        ["IMG_0001 (2).HEIC", "IMG_0001.HEIC", "IMG_0002.MOV"]
    assert send(sw["phone"], "notes.txt", b"x").status_code == 415
    assert not [p for p in stage.iterdir() if p.name.endswith(".part")]


def test_each_run_starts_where_the_last_finished_one_did(sw):
    phone = sw["phone"]
    first = datetime.strptime(phone.get("/api/shortcut/since").text, "%Y-%m-%d %H:%M:%S")
    assert abs(datetime.now() - timedelta(minutes=10) - first) < timedelta(minutes=1)   # from setup, a bit before
    started = datetime.now()
    phone.get("/api/shortcut/since")                       # a run that never finished (the Mac fell asleep)
    assert datetime.strptime(phone.get("/api/shortcut/since").text, "%Y-%m-%d %H:%M:%S") == first
    assert phone.post("/api/shortcut/done").status_code == 200
    later = datetime.strptime(phone.get("/api/shortcut/since").text, "%Y-%m-%d %H:%M:%S")
    assert later >= started.replace(microsecond=0) - timedelta(minutes=10, seconds=1)
    assert sw["mac"].get("/api/shortcut/status").json()[0]["folder"] == "iphone"


def test_only_the_token_gets_in(sw):
    with httpx.Client(base_url=sw["phone_url"], timeout=30) as stranger:
        assert stranger.get("/api/shortcut/since").status_code == 401
        assert send(stranger, "a.jpg", b"x").status_code == 401
        r = stranger.get("/api/shortcut/since", headers={"Authorization": "Bearer wrong"})
        assert r.status_code == 401
        assert stranger.post("/api/shortcut/token", json={"folder": "x"}).status_code in (401, 403)  # only this Mac
        r = stranger.get("/api/items", headers={"Authorization": f"Bearer {sw['token']}"})
        assert r.status_code in (401, 403)                  # the token opens nothing else
    new = sw["mac"].post("/api/shortcut/token", json={"folder": "iphone"}).json()["token"]
    assert sw["phone"].get("/api/shortcut/since").status_code == 401                  # replaced: the old one stops
    sw["phone"].headers["Authorization"] = f"Bearer {new}"
    assert sw["phone"].get("/api/shortcut/since").status_code == 200
