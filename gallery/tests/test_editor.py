"""The editor saves a new photo beside the original, an album's cover is yours to choose, exact copies go to the Trash."""
import json
import os
import subprocess
import time

import httpx
import pytest
from playwright.sync_api import expect

from conftest import PY, ROOT, make_world, photo, serve, stop


def drive(d):
    photo(d / "trip/a.jpg", 800, 600, "2024:03:01 10:00:00", tags=["-Make=Apple", "-Model=iPhone", "-GPSLatitude*=12.5", "-GPSLongitude*=-45.25"])
    photo(d / "trip/b.jpg", 800, 600, "2024:03:02 10:00:00", color="smptebars")
    photo(d / "trip/c.jpg", 800, 600, "2024:03:03 10:00:00", color="smptehdbars")
    photo(d / "trip/shot.png", 1260, 2736, "2024:03:04 10:00:00")


@pytest.fixture(scope="module")
def edworld(tmp_path_factory):
    w = serve(make_world(tmp_path_factory.mktemp("editor"), drive))
    with httpx.Client(base_url=w["url"], timeout=30) as api:
        w["api"] = api
        w["ids"] = {i["name"]: i["id"] for i in api.get("/api/items").json()["items"]}
        yield w
    stop(w)


def exif(path):
    return json.loads(subprocess.run(["exiftool", "-j", "-n", "-DateTimeOriginal", "-Make", "-GPSLatitude", "-GPSLongitude", "-UserComment",
                                      "-ImageWidth", "-ImageHeight", str(path)], capture_output=True, text=True).stdout)[0]


def indexed(w, name):
    for _ in range(150):
        if name in [i["name"] for i in w["api"].get("/api/items").json()["items"]]:
            return True
        time.sleep(0.1)
    return False


@pytest.mark.parametrize("page", [{"world": "edworld"}], indirect=True)
def test_edit_saves_a_copy_and_leaves_the_original(page, edworld):
    inbox, original = edworld["state"] / "inbox/trip", edworld["drive"] / "trip/a.jpg"
    before = original.read_bytes()
    page.goto(page.base + "/#/album/trip")
    page.locator("#days .tile[data-id='%d']" % edworld["ids"]["a.jpg"]).click()
    page.click("#vEdit")
    expect(page.locator("#edCrop")).to_be_visible()
    page.click("#edSave")
    expect(page.locator(".toast")).to_contain_text("Nothing changed")
    page.click("#edRotR")                                           # 800x600 turned: 600x800
    page.evaluate("() => { const e = document.querySelector('#edB'); e.value = 130; e.dispatchEvent(new Event('input')); }")
    box = page.locator("#edCrop").bounding_box()
    h = page.locator("#edCrop [data-h=se]").bounding_box()
    page.mouse.move(h["x"] + h["width"] / 2, h["y"] + h["height"] / 2)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2, steps=4)   # crop to about a quarter
    page.mouse.up()
    page.click("#edSave")
    expect(page.locator("#editor")).to_be_hidden()
    expect(page.locator("#viewer")).to_be_visible()                 # back on the photo
    e = exif(inbox / "a edited.jpg")
    assert 280 <= e["ImageWidth"] <= 320 and 380 <= e["ImageHeight"] <= 420
    assert e["DateTimeOriginal"] == "2024:03:01 10:00:00" and e["Make"] == "Apple"
    assert abs(e["GPSLatitude"] - 12.5) < .01 and abs(e["GPSLongitude"] + 45.25) < .01
    assert original.read_bytes() == before                          # the original is never changed
    assert indexed(edworld, "a edited.jpg")
    item = next(i for i in edworld["api"].get("/api/items").json()["items"] if i["name"] == "a edited.jpg")
    assert item["taken_at"].startswith("2024-03-01T10:00:00")       # beside the original on the timeline
    assert not [f for f in os.listdir(inbox) if f.startswith(".")]  # no temporary files left


def test_edit_api_takes_only_jpegs_and_never_reuses_a_name(edworld):
    api, ids, jpeg = edworld["api"], edworld["ids"], (edworld["drive"] / "trip/b.jpg").read_bytes()
    send = lambda i, data: api.post(f"/api/edit/{i}", files={"file": ("e.jpg", data, "image/jpeg")})
    assert send(ids["b.jpg"], b"not a jpeg").status_code == 415
    assert send(999999, jpeg).status_code == 404
    assert send(ids["b.jpg"], jpeg).json()["saved"] == "trip/b edited.jpg"
    assert send(ids["b.jpg"], jpeg).json()["saved"] == "trip/b edited 2.jpg"


def test_an_edited_screenshot_stays_a_screenshot(edworld):
    api, ids = edworld["api"], edworld["ids"]
    jpeg = (edworld["drive"] / "trip/c.jpg").read_bytes()
    assert api.post(f"/api/edit/{ids['shot.png']}", files={"file": ("e.jpg", jpeg, "image/jpeg")}).status_code == 200
    assert indexed(edworld, "shot edited.jpg")
    names = [i["name"] for i in api.get("/api/items", params={"filter": "screenshots"}).json()["items"]]
    assert "shot edited.jpg" in names


@pytest.mark.parametrize("page", [{"world": "edworld"}], indirect=True)
def test_choose_an_albums_cover(page, edworld):
    api, ids = edworld["api"], edworld["ids"]
    album = api.post("/api/user-albums", json={"name": "Best"}).json()["id"]
    api.post(f"/api/user-albums/{album}/add", json={"ids": [ids["a.jpg"], ids["b.jpg"], ids["c.jpg"]]})
    cover = lambda: next(a["cover"] for a in api.get("/api/user-albums").json() if a["id"] == album)
    assert cover() == ids["a.jpg"]                                  # the oldest, until you choose
    page.goto(page.base + f"/#/ualbum/{album}")
    tile = page.locator(f"#days .tile[data-id='{ids['c.jpg']}']")
    tile.hover(); tile.locator(".sel").click()
    page.click("#selCover")
    expect(page.locator(".toast")).to_contain_text("cover")
    assert cover() == ids["c.jpg"]
    assert api.post(f"/api/user-albums/{album}/cover", json={"ids": [ids["shot.png"]]}).status_code == 400   # not in it
    api.post("/api/trash", json={"ids": [ids["c.jpg"]]})            # trashed: back to the oldest; put back: yours again
    assert cover() == ids["a.jpg"]
    api.post("/api/trash/restore", json={"ids": [ids["c.jpg"]]})
    assert cover() == ids["c.jpg"]
    api.post(f"/api/user-albums/{album}/remove", json={"ids": [ids["c.jpg"]]})
    assert cover() == ids["a.jpg"]


def test_a_new_exact_copy_goes_to_the_trash_and_off_the_drive(edworld):
    """A copy under another name, added later: not uploaded, not shown twice, moved off the drive once it's indexed."""
    state, d, env = edworld["state"], edworld["drive"], edworld["env"]
    copy = d / "trip/copy of b.jpg"
    copy.write_bytes((d / "trip/b.jpg").read_bytes())
    subprocess.run([PY, "build_index.py", "--no-thumbs"], cwd=ROOT, env=env, check=True, capture_output=True)
    con_env = {**env}
    code = f"""
import sqlite3, sys
sys.path.insert(0, {str(ROOT.parent / 'backup')!r})
from config import DB_PATH
con = sqlite3.connect(DB_PATH)
con.execute("UPDATE media SET msg_id = 7 WHERE path = 'trip/b.jpg'")
con.commit()
import guard, trash
print(guard.reconcile(['trip/copy of b.jpg'], log=lambda m: None))
trash.sync_drive()
"""
    out = subprocess.run([PY, "-c", code], cwd=ROOT, env=con_env, check=True, capture_output=True, text=True).stdout
    assert out.strip() == "[]"                                      # not uploaded
    assert "trip/copy of b.jpg\n" in (state / "skip.txt").read_text()
    assert "copy of b.jpg" not in [i["name"] for i in edworld["api"].get("/api/items").json()["items"]]
    assert not copy.exists() and (d / ".photo-vault-trash/trip/copy of b.jpg").is_file()
    assert (d / "trip/b.jpg").is_file()
    assert (state / "copies-to-trash.tsv").read_text() == ""
    trashed = edworld["api"].get("/api/trash").json()["items"]
    edworld["api"].post("/api/trash/restore", json={"ids": [i["id"] for i in trashed]})    # your choice: stays back
    subprocess.run([PY, "-c", "import trash; trash.sync_drive()"], cwd=ROOT, env=con_env, check=True)
    assert copy.exists()
