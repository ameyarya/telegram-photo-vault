"""Search, filters, months, item details, favorites, your albums and the backup dashboard (API level)."""
import pytest


def names(api, **params):
    return sorted(i["name"] for i in api.get("/api/items", params=params).json()["items"])


# ---------- search ----------
@pytest.mark.parametrize("q, expected", [
    ("2023", ["screenshot.png"]),                                          # a year
    ("feb 2024", ["beach.jpg", "pier.jpg", "sunset.jpg"]),                 # month + year
    ("February 2024", ["beach.jpg", "pier.jpg", "sunset.jpg"]),            # full month name
    ("2024-01-20", ["IMG_0001.heic"]),                                      # an exact day
    ("sony", ["beach.jpg"]),                                                # camera make
    ("ILCE-7M3", ["beach.jpg"]),                                            # camera model
    ("24-70mm", ["beach.jpg"]),                                             # lens
    ("pier", ["pier.jpg"]),                                                 # file name
    ("vacation", ["beach.jpg", "pier.jpg", "sunset.jpg"]),                # folder name
    ("wedding day 2", ["IMG_0001.heic"]),                                   # every word must match
    ("sony 2023", []),                                                      # date and word combine
    ("nothing-like-this", []),
])
def test_search(api, q, expected):
    assert names(api, q=q) == expected


def test_search_reports_what_it_understood(api):
    r = api.get("/api/items", params={"q": "feb 2024 sony"}).json()
    assert r["query"] == 'feb 2024 · "sony"'
    assert r["total"] == 1


def test_search_text_is_not_sql(api):
    """Quotes and wildcards are just text."""
    for q in ["' OR 1=1 --", "%", "_", '"; DROP TABLE media; --']:
        assert api.get("/api/items", params={"q": q}).status_code == 200
    assert api.get("/api/items").json()["total"] == 10


# ---------- filter chips ----------
@pytest.mark.parametrize("f, expected", [
    ("videos", ["clip.mp4", "film.mp4 (part 1)", "film.mp4 (part 2)"]),
    ("screenshots", ["screenshot.png"]),
    ("undated", ["screenshot.png"]),
    ("located", ["pier.jpg"]),
    ("notbackedup", None),          # Telegram disabled in this world: everything
    ("backedup", []),
])
def test_filters(api, f, expected):
    got = names(api, filter=f)
    if expected is None:
        assert len(got) == 10
    else:
        assert got == expected


def test_photos_filter_excludes_videos_and_screenshots(api):
    got = names(api, filter="photos")
    assert len(got) == 6 and not any(n.endswith(".mp4") or "part" in n for n in got)
    assert "screenshot.png" not in got                                   # under Screenshots (and All), not Photos


def test_filters_combine_with_folder_and_search(api):
    assert names(api, album="family", filter="videos") == ["clip.mp4", "film.mp4 (part 1)", "film.mp4 (part 2)"]
    assert names(api, album="family", filter="videos", q="2024-08") == ["film.mp4 (part 1)", "film.mp4 (part 2)"]
    assert names(api, filter="photos,located") == ["pier.jpg"]


def test_unknown_filter_is_ignored(api):
    assert api.get("/api/items", params={"filter": "bogus"}).json()["total"] == 10


# ---------- months (date scrubber) ----------
def test_months_count_every_item_in_order(api):
    asc = api.get("/api/months").json()
    assert [m["m"] for m in asc] == sorted(m["m"] for m in asc)
    assert sum(m["n"] for m in asc) == 10
    assert {"m": "2024-02", "n": 3} in asc
    desc = api.get("/api/months", params={"order": "desc"}).json()
    assert desc == asc[::-1]
    assert api.get("/api/months", params={"album": "vacation"}).json() == [{"m": "2024-02", "n": 3}]


def test_offset_of_a_month_lands_on_that_month(api):
    """The scrubber jumps to sum(earlier months); the item there must be the month's first."""
    months = api.get("/api/months", params={"order": "desc"}).json()
    offset = 0
    for m in months:
        first = api.get("/api/items", params={"order": "desc", "offset": offset, "limit": 1}).json()["items"][0]
        assert first["taken_at"].startswith(m["m"])
        offset += m["n"]


# ---------- item details ----------
def test_item_details_camera_and_location(api, items):
    d = api.get(f"/api/item/{items['beach.jpg']['id']}").json()
    assert (d["make"], d["model"], d["lens"], d["iso"], d["fnumber"]) == ("SONY", "ILCE-7M3", "FE 24-70mm F2.8 GM", 200, 2.8)
    assert d["exposure"] == pytest.approx(1 / 250) and d["focal"] == 35
    assert d["lat"] is None
    p = api.get(f"/api/item/{items['pier.jpg']['id']}").json()
    assert p["lat"] == pytest.approx(26.9124, abs=1e-3) and p["lon"] == pytest.approx(75.7873, abs=1e-3)
    assert api.get("/api/item/999999").status_code == 404


# ---------- on this day ----------
def test_memories_are_this_date_in_earlier_years(api, items):
    it = next(i for i in items.values() if i["kind"] != "video" and i["date_src"] != "file")
    y = api.get("/api/memories", params={"on": it["taken_at"][5:10]}).json()["years"]
    assert it["id"] in [x["id"] for g in y for x in g["items"]]
    assert all(g["ago"] > 0 and all(x["taken_at"].startswith(str(g["year"])) for x in g["items"]) for g in y)
    assert [g["year"] for g in y] == sorted((g["year"] for g in y), reverse=True)
    assert api.get("/api/memories", params={"on": "not-a-day"}).status_code == 400


# ---------- favorites ----------
def test_favorites(api, items):
    ids = [items["beach.jpg"]["id"], items["clip.mp4"]["id"]]
    assert api.post("/api/favorites", json={"ids": ids + [999999]}).json() == {"favorited": 2}  # unknown ids ignored
    assert api.post("/api/favorites", json={"ids": ids}).status_code == 200                       # idempotent
    assert names(api, filter="favorites") == ["beach.jpg", "clip.mp4"]
    flags = {i["name"]: i["fav"] for i in api.get("/api/items").json()["items"]}
    assert flags["beach.jpg"] == 1 and flags["sunset.jpg"] == 0
    api.post("/api/favorites/remove", json={"ids": ids})
    assert names(api, filter="favorites") == []


# ---------- your albums ----------
def test_user_albums_lifecycle(api, items):
    a = api.post("/api/user-albums", json={"name": "  Best of 2024  "}).json()
    assert a["name"] == "Best of 2024"
    add = [items["beach.jpg"]["id"], items["portrait.jpg"]["id"], items["clip.mp4"]["id"]]
    assert api.post(f"/api/user-albums/{a['id']}/add", json={"ids": add}).json() == {"added": 3}
    api.post(f"/api/user-albums/{a['id']}/add", json={"ids": add})  # adding twice doesn't duplicate
    assert names(api, user_album=a["id"]) == ["beach.jpg", "clip.mp4", "portrait.jpg"]
    listed = {x["id"]: x for x in api.get("/api/user-albums").json()}
    assert listed[a["id"]]["n"] == 3 and listed[a["id"]]["cover"] is not None
    api.post(f"/api/user-albums/{a['id']}/remove", json={"ids": [items["clip.mp4"]["id"]]})
    assert names(api, user_album=a["id"]) == ["beach.jpg", "portrait.jpg"]
    assert api.post(f"/api/user-albums/{a['id']}/rename", json={"name": "Faves"}).status_code == 200
    assert {x["id"]: x["name"] for x in api.get("/api/user-albums").json()}[a["id"]] == "Faves"
    item = api.get(f"/api/item/{items['beach.jpg']['id']}").json()
    assert {"id": a["id"], "name": "Faves"} in item["user_albums"]
    assert api.post(f"/api/user-albums/{a['id']}/delete").status_code == 200
    assert a["id"] not in {x["id"] for x in api.get("/api/user-albums").json()}
    assert api.get("/api/items").json()["total"] == 10   # deleting an album never deletes photos


def test_user_album_errors(api):
    assert api.post("/api/user-albums", json={"name": "   "}).status_code == 400
    assert api.post("/api/user-albums/999999/add", json={"ids": [1]}).status_code == 404
    assert api.post("/api/user-albums/999999/rename", json={"name": "x"}).status_code == 404
    assert api.post("/api/user-albums/999999/delete").status_code == 404


def test_nothing_can_change_the_drive_or_telegram(api, items, world):
    """The only writes are local (favorites/albums); no endpoint deletes or edits photos."""
    before = sorted(p.name for p in world["drive"].rglob("*") if p.is_file())
    i = items["beach.jpg"]["id"]
    for method, url in [("DELETE", f"/original/{i}"), ("DELETE", f"/api/item/{i}"), ("POST", f"/api/item/{i}"),
                        ("PUT", f"/view/{i}"), ("DELETE", "/api/albums"), ("POST", "/api/items")]:
        assert api.request(method, url).status_code in (404, 405), (method, url)
    assert sorted(p.name for p in world["drive"].rglob("*") if p.is_file()) == before


# ---------- dashboard ----------
def test_backup_dashboard(api):
    b = api.get("/api/backup").json()
    assert b["total"]["files"] == 10 and b["total"]["backed_up"] == 0
    assert b["total"]["videos"] == 3 and b["total"]["undated"] == 1
    assert {f["folder"]: f["files"] for f in b["by_folder"]} == {"vacation": 3, "family": 6, "iphone": 1}
    assert {y["year"]: y["files"] for y in b["by_year"]} == {"2023": 1, "2024": 9}
    assert (b["upload_list"], b["uploaded_files"]) == (4, 3)
    assert (b["skipped_duplicates"], b["empty_files"], b["failed_batches"]) == (1, 1, 1)
    assert b["last_failures"] == ["2026-10-16 10:01:00  batch failed (first: a)"]
    assert len(b["not_backed_up_sample"]) == 10
    assert b["drive"] is True and b["telegram"] is False


def test_only_real_screenshots_count_as_screenshots():
    """Regression: Snapchat saves and other tall images (downloads, AI art) were filed under Screenshots."""
    import sqlite3
    from search import SCREENSHOT
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE media (name TEXT, ext TEXT, width INT, height INT, make TEXT, shot INT)")
    rows = {"IMG_0001.PNG": ("png", 1179, 2556, None, True),        # iPhone screenshot
            "IMG_0002.PNG": ("png", 2556, 1179, None, True),        # ... taken sideways
            "IMG_0003.PNG": ("png", 1640, 2360, None, True),        # iPad screenshot
            "Screenshot_2019-07-26.png": ("png", 1080, 2214, None, True),   # Android: by name
            "Screen Shot 2020-01-01.jpg": ("jpg", 1440, 900, None, True),
            "Snapchat-1203229220.jpg": ("jpg", 1080, 2214, None, False),    # a Snapchat save
            "IMG_0004.JPG": ("jpg", 1242, 2208, None, False),        # screen-sized JPG: saved from an app, not a screenshot
            "IMG_0005.PNG": ("png", 1024, 1536, None, False),        # AI image / download
            "IMG_0006.PNG": ("png", 1179, 2556, "Apple", False)}     # a camera made it
    con.executemany("INSERT INTO media(name, ext, width, height, make) VALUES (?,?,?,?,?)", [(n, *v[:4]) for n, v in rows.items()])
    got = {r[0] for r in con.execute(f"SELECT name FROM media WHERE {SCREENSHOT}")}
    assert got == {n for n, v in rows.items() if v[4]}
    rest = {r[0] for r in con.execute(f"SELECT name FROM media WHERE NOT {SCREENSHOT}")}    # regression: unknown shot hid them
    assert rest == {n for n, v in rows.items() if not v[4]}
