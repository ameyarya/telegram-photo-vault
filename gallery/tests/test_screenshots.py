"""What counts as a screenshot: also the HEIC or JPEG an iPhone saves after you draw on one, not any tall picture."""
import sqlite3

import httpx
import pytest

from conftest import PY, ROOT, make_world, photo, run, serve, stop


def drive(d):
    photo(d / "phone/plain.png", 1260, 2736)                                         # as taken
    photo(d / "phone/marked.jpg", 1260, 2736, tags=["-UserComment=Screenshot"])      # drawn on: a JPEG that says so
    photo(d / "phone/marked.heic", 1179, 2556)                                       # drawn on: a HEIC, says nothing
    photo(d / "phone/saved.jpg", 1260, 2736)                                         # tall download or Snapchat save
    photo(d / "phone/app.heic", 1080, 1920)                                          # 16:9 from an app, no phone screen today
    photo(d / "phone/camera.heic", 1260, 2736, tags=["-Make=Apple"])                 # a camera took it
    photo(d / "phone/photo.jpg", 1200, 800, "2024:01:19 11:00:00")


@pytest.fixture(scope="module")
def shots(tmp_path_factory):
    w = serve(make_world(tmp_path_factory.mktemp("shots"), drive))
    yield w
    stop(w)


def test_marked_up_screenshots_are_screenshots(shots):
    names = lambda f: sorted(i["name"] for i in httpx.get(shots["url"] + "/api/items", params={"filter": f}).json()["items"])
    assert names("screenshots") == ["marked.heic", "marked.jpg", "plain.png"]
    assert names("photos") == ["app.heic", "camera.heic", "photo.jpg", "saved.jpg"]
    con = sqlite3.connect(shots["data"] / "index.db")               # indexed before the marker was read: found later
    con.execute("UPDATE media SET shot = NULL")
    con.commit()
    assert "marked.jpg" not in names("screenshots")
    run(PY, "build_index.py", "--no-thumbs", cwd=ROOT, env=shots["env"])
    assert "marked.jpg" in names("screenshots") and "saved.jpg" not in names("screenshots")
