"""Live Photos, the trash, cleaning up similar shots, and telling the face grouping who's who (liveworld)."""
import hashlib
import os
import sqlite3
import subprocess
import time

import httpx
import numpy as np
import pytest

from conftest import PY, ROOT, shot

TRASH = ".photo-vault-trash"


@pytest.fixture(scope="module")
def lapi(liveworld):
    with httpx.Client(base_url=liveworld["url"], timeout=60) as c:
        yield c


def names(lapi, **params):
    return sorted(i["name"] for i in lapi.get("/api/items", params=params).json()["items"])


def item(lapi, name):
    return next(i for i in lapi.get("/api/items").json()["items"] if i["name"] == name)


def db(w):
    con = sqlite3.connect(w["data"] / "index.db")
    con.row_factory = sqlite3.Row
    return con


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


# ---------------------------------------------------------------- Live Photos
def test_live_photo_shows_once_with_its_motion_attached(lapi, liveworld):
    shown = names(lapi)
    assert "IMG_0100.JPG" in shown and "IMG_0100.MOV" not in shown       # the clip is part of the photo
    assert "IMG_0200.JPG" in shown and "IMG_0200.MOV" in shown           # 8 s: a real video, not a Live Photo
    still = item(lapi, "IMG_0100.JPG")
    assert still["live_id"]
    assert item(lapi, "IMG_0200.JPG")["live_id"] is None
    r = lapi.get(f"/original/{still['live_id']}")                         # the motion clip streams
    assert r.status_code == 200 and r.content == (liveworld["drive"] / "phone/IMG_0100.MOV").read_bytes()
    assert names(lapi, filter="live") == ["IMG_0100.JPG"]
    phone = next(a for a in lapi.get("/api/albums").json() if a["album"] == "phone")
    assert phone["n"] == 4                                                # 3 photos + the real video
    assert sum(m["n"] for m in lapi.get("/api/months").json()) == len(shown)


def test_pairing_is_redone_on_reindex(liveworld):
    subprocess.run([PY, "build_index.py", "--no-thumbs"], cwd=ROOT, env=liveworld["env"], check=True, capture_output=True)
    con = db(liveworld)
    pairs = con.execute("""SELECT s.name, v.name FROM media s JOIN media v ON v.id = s.live_id""").fetchall()
    assert [tuple(p) for p in pairs] == [("IMG_0100.JPG", "IMG_0100.MOV")]
    assert con.execute("SELECT count(*) FROM media WHERE live_of IS NOT NULL").fetchone()[0] == 1


def test_restoring_a_live_photo_brings_its_motion_clip(lapi):
    r = lapi.post("/api/restore", json={"ids": [item(lapi, "IMG_0100.JPG")["id"]], "label": "live"}).json()
    assert r["files"] == 2
    for _ in range(300):
        j = lapi.get(f"/api/jobs/{r['job']}").json()
        if j["status"] != "running":
            break
        time.sleep(0.1)
    assert j["status"] == "done", j
    got = sorted(os.path.relpath(os.path.join(d, f), r["dest"]) for d, _, fs in os.walk(r["dest"]) for f in fs)
    assert got == ["phone/IMG_0100.JPG", "phone/IMG_0100.MOV"]


# ---------------------------------------------------------------- trash
def test_trash_hides_moves_and_restores_with_the_live_clip(lapi, liveworld):
    drive = liveworld["drive"]
    still = item(lapi, "IMG_0100.JPG")
    before = {p: sha(drive / p) for p in ("phone/IMG_0100.JPG", "phone/IMG_0100.MOV")}
    r = lapi.post("/api/trash", json={"ids": [still["id"]]}).json()
    assert r == {"trashed": 2, "moved": 2, "drive": True}
    assert "IMG_0100.JPG" not in names(lapi) and names(lapi, filter="live") == []
    for p in before:                                                       # moved aside on the same drive
        assert not (drive / p).exists() and (drive / TRASH / p).is_file()
    t = lapi.get("/api/trash").json()
    assert [i["name"] for i in t["items"]] == ["IMG_0100.JPG"] and t["bytes"] > 0
    assert lapi.get(f"/original/{still['id']}").status_code == 200        # still viewable from the trash
    assert lapi.get("/api/status").json()["uploaded"] == lapi.get("/api/status").json()["total"]  # Telegram untouched
    assert lapi.post("/api/trash/restore", json={"ids": [still["id"]]}).json() == {"restored": 2}
    assert "IMG_0100.JPG" in names(lapi) and lapi.get("/api/trash").json()["items"] == []
    for p, h in before.items():
        assert sha(drive / p) == h
    assert not any((drive / TRASH).rglob("*.JPG"))


def test_trash_while_unplugged_moves_the_file_when_plugged_in(lapi, liveworld):
    drive = liveworld["drive"]
    code = "import trash; print(trash.trash(trash.Ids(ids=[%d])))" % item(lapi, "plain.jpg")["id"]
    env = {**liveworld["env"], "GALLERY_DRIVE": str(drive) + "-missing"}
    out = subprocess.run([PY, "-c", code], cwd=ROOT, env=env, check=True, capture_output=True, text=True).stdout
    assert "'moved': 0" in out and "'drive': False" in out
    assert "plain.jpg" not in names(lapi) and (drive / "phone/plain.jpg").is_file()   # hidden now, moved later
    subprocess.run([PY, "-c", "import trash; trash.apply_pending()"], cwd=ROOT, env=liveworld["env"], check=True)
    assert (drive / TRASH / "phone/plain.jpg").is_file() and not (drive / "phone/plain.jpg").exists()
    lapi.post("/api/trash/restore", json={"ids": [item_any(liveworld, "plain.jpg")]})
    assert (drive / "phone/plain.jpg").is_file() and "plain.jpg" in names(lapi)


def item_any(w, name):
    return db(w).execute("SELECT id FROM media WHERE name = ?", (name,)).fetchone()[0]


def test_empty_trash_deletes_from_the_drive_only(lapi, liveworld):
    drive = liveworld["drive"]
    pid = item(lapi, "IMG_0200.JPG")["id"]
    lapi.post("/api/trash", json={"ids": [pid]})
    r = lapi.post("/api/trash/empty").json()
    assert r["deleted"] == 1 and r["bytes"] > 0
    assert not (drive / TRASH / "phone/IMG_0200.JPG").exists() and not (drive / "phone/IMG_0200.JPG").exists()
    assert not (drive / TRASH / "phone").exists()                         # empty folders tidied
    assert lapi.get("/api/trash").json()["items"] == [] and "IMG_0200.JPG" not in names(lapi)
    row = db(liveworld).execute("SELECT msg_id FROM media WHERE id = ?", (pid,)).fetchone()
    assert row["msg_id"] is not None                                      # Telegram copy kept and still known
    assert lapi.post("/api/trash/restore", json={"ids": [pid]}).status_code == 409


# ---------------------------------------------------------------- similar shots
def test_similar_picks_the_sharp_shot_and_hides_trashed(lapi):
    g = lapi.get("/api/similar").json()
    assert g["total"] == 1
    grp = g["groups"][0]
    by_id = {i["id"]: i["name"] for i in grp["items"]}
    assert sorted(by_id.values()) == ["b_0.jpg", "b_1.jpg", "b_2.jpg"]
    assert by_id[grp["best"]] == "b_0.jpg"                                # the un-blurred one
    lapi.post("/api/trash", json={"ids": [i for i, n in by_id.items() if n == "b_1.jpg"]})
    g2 = lapi.get("/api/similar").json()["groups"][0]
    assert sorted(i["name"] for i in g2["items"]) == ["b_0.jpg", "b_2.jpg"]
    lapi.post("/api/trash", json={"ids": [i for i, n in by_id.items() if n == "b_2.jpg"]})
    assert lapi.get("/api/similar").json()["total"] == 0                 # one left: nothing to compare
    lapi.post("/api/trash/restore", json={"ids": list(by_id)})
    assert lapi.get("/api/similar").json()["total"] == 1


def test_favorite_wins_best_and_groups_can_be_dismissed(lapi, liveworld):
    grp = lapi.get("/api/similar").json()["groups"][0]
    fav = next(i["id"] for i in grp["items"] if i["name"] == "b_2.jpg")
    lapi.post("/api/favorites", json={"ids": [fav]})
    assert lapi.get("/api/similar").json()["groups"][0]["best"] == fav
    lapi.post("/api/favorites/remove", json={"ids": [fav]})
    assert lapi.post("/api/similar/dismiss", json={"key": grp["key"]}).status_code == 200
    assert lapi.get("/api/similar").json() == {"total": 0, "groups": []}
    con = db(liveworld)
    con.execute("DELETE FROM similar_dismissed")
    con.commit()


# ---------------------------------------------------------------- people: your answers
def people_by_face(lapi):
    out = {}
    for p in lapi.get("/api/people").json():
        who = {i["name"].split("_")[1] for i in lapi.get("/api/items", params={"person": p["id"]}).json()["items"]}
        out[p["id"]] = who
    return out


def test_merging_people_survives_reanalysis(lapi, liveworld):
    groups = people_by_face(lapi)
    assert sorted(map(sorted, groups.values())) == [["alice"], ["bob"], ["dave"]]
    alice = next(p for p, w in groups.items() if w == {"alice"})
    bob = next(p for p, w in groups.items() if w == {"bob"})
    assert lapi.post("/api/people/merge", json={"ids": [alice]}).status_code == 400
    r = lapi.post("/api/people/merge", json={"ids": [bob, alice]}).json()
    assert r["into"] in (alice, bob)
    assert sorted(map(sorted, people_by_face(lapi).values())) == [["alice", "bob"], ["dave"]]
    subprocess.run([PY, "ml.py", "run", "people"], cwd=ROOT, env=liveworld["env"], check=True, capture_output=True)
    after = people_by_face(lapi)
    assert after[r["into"]] == {"alice", "bob"}                          # still one person, same id
    assert sorted(map(sorted, after.values())) == [["alice", "bob"], ["dave"]]
    p = next(x for x in lapi.get("/api/people").json() if x["id"] == r["into"])
    assert p["n"] == 6


def test_maybe_same_suggestions_and_not_same(lapi, liveworld):
    assert lapi.get("/api/people/suggestions").json() == []              # fake faces: nobody looks alike
    groups = people_by_face(lapi)
    ab = next(p for p, w in groups.items() if "alice" in w)
    dave = next(p for p, w in groups.items() if w == {"dave"})
    con = db(liveworld)
    a_vec = np.frombuffer(con.execute("SELECT emb FROM faces WHERE person = ? LIMIT 1", (ab,)).fetchone()[0], np.float32)
    saved = con.execute("SELECT id, emb FROM faces WHERE person = ?", (dave,)).fetchall()
    for f in saved:   # make dave look somewhat like alice: the model isn't sure
        d = np.frombuffer(f["emb"], np.float32)
        v = 0.75 * a_vec + 0.66 * d
        con.execute("UPDATE faces SET emb = ? WHERE id = ?", ((v / np.linalg.norm(v)).astype(np.float32).tobytes(), f["id"]))
    con.commit()
    try:
        s = lapi.get("/api/people/suggestions").json()
        assert len(s) == 1 and {s[0]["a"]["id"], s[0]["b"]["id"]} == {ab, dave} and 0.45 <= s[0]["similarity"] < 0.7
        mine = lapi.get("/api/people/suggestions", params={"person": dave}).json()
        assert [x["b"]["id"] for x in mine] == [ab]
        assert lapi.post("/api/people/not-same", json={"a": dave, "b": ab}).status_code == 200
        assert lapi.get("/api/people/suggestions").json() == []
        assert lapi.post("/api/people/not-same", json={"a": dave, "b": dave}).status_code == 400
    finally:
        con.executemany("UPDATE faces SET emb = ? WHERE id = ?", [(f["emb"], f["id"]) for f in saved])
        con.execute("DELETE FROM people_not_same")
        con.commit()


def test_maybe_same_ranking_unit():
    import ml
    e = np.eye(4, dtype=np.float32)
    cents = {1: e[0], 2: ml._norm(e[0] + 0.9 * e[1]), 3: ml._norm(e[0] + 2 * e[1]), 4: e[3]}
    pairs = ml.maybe_same(cents)
    assert [(a, b) for a, b, _ in pairs] == [(2, 3), (1, 2)]              # most likely first; 1~3 too far
    assert ml.maybe_same(cents, not_same={(2, 3)})[0][:2] == (1, 2)
    assert [b for _, b, _ in ml.maybe_same(cents, person=1)] == [2]
    assert ml.maybe_same(cents, person=99) == [] and ml.maybe_same({}) == []


# ---------------------------------------------------------------- in the browser
@pytest.mark.parametrize("page", [{"world": "liveworld"}], indirect=True)
def test_ui_live_badge_and_play_button(page):
    page.goto(page.base + "/#/album/phone")
    tile = page.locator("#days .tile", has=page.locator(".live"))
    tile.first.wait_for()
    assert tile.count() == 1
    tile.first.click()
    live = page.locator("#vLive")
    live.wait_for(state="visible")
    shot(page, "livephoto-viewer")
    live.click()
    v = page.locator("#vMain video.v-live")
    v.wait_for()
    assert "/original/" in v.get_attribute("src")
    page.wait_for_function("document.querySelector('#vMain video.v-live') === null", timeout=10000)  # back to the photo
    assert page.locator("#vMain img.v-media").is_visible()
    page.keyboard.press("ArrowRight")                                     # a plain photo: no LIVE button
    page.wait_for_timeout(200)
    assert not live.is_visible()


@pytest.mark.parametrize("page", [{"world": "liveworld"}], indirect=True)
def test_ui_similar_keep_best_then_restore_from_trash(page):
    page.goto(page.base + "/#/similar")
    grp = page.locator(".sim-group")
    grp.wait_for()
    assert grp.locator(".tile.keep").count() == 1 and grp.locator(".tile.drop").count() == 2
    assert grp.locator(".tile.keep .best").count() == 1
    btn = grp.locator('[data-act="clean"]')
    assert btn.inner_text() == "Trash 2, keep 1"
    grp.locator(".tile.drop .tg").first.click()                           # change my mind about one
    assert btn.inner_text() == "Trash 1, keep 2"
    shot(page, "similar-cleanup")
    btn.click()
    page.locator(".toast").wait_for()
    page.wait_for_selector(".sim-group", state="detached")
    page.goto(page.base + "/#/trash")
    page.locator(".trash-grid .tile").first.wait_for()
    assert page.locator(".trash-grid .tile").count() == 1
    shot(page, "trash")
    page.locator("#trRestoreAll").click()
    page.locator("text=Trash is empty").wait_for()
    page.goto(page.base + "/#/similar")                                  # all three back: a new group to review
    page.locator(".sim-group").wait_for()
    assert page.locator(".sim-group .tile").count() == 3
    con = db(page.world)
    con.execute("DELETE FROM similar_dismissed")
    con.commit()


@pytest.mark.parametrize("page", [{"world": "liveworld"}], indirect=True)
def test_ui_people_select_and_merge(page, lapi, liveworld):
    page.goto(page.base + "/#/people")
    page.locator(".person").first.wait_for()
    n = page.locator(".person").count()
    assert n == 2
    page.locator("#pSelect").click()
    page.locator(".person").nth(0).click()
    assert page.locator("#pMergeSel").is_hidden()
    page.locator(".person").nth(1).click()
    assert page.locator("#pMergeSel").inner_text() == "Merge 2 into one person"
    shot(page, "people-merge")
    page.locator("#pMergeSel").click()
    page.wait_for_url("**/#/person/*")
    assert len(lapi.get("/api/people").json()) == 1
    page.goto(page.base + "/#/people/review")
    page.locator("text=No more suggestions").wait_for()


# ---------------------------------------------------------------- fixing dates
def test_dates_from_file_names():
    import dates
    cases = {"20220513_152109_441~2.MOV": "2022-05-13T15:21:09", "IMG-20210305-WA0001.jpg": "2021-03-05T12:00:00",
             "Screenshot 2023-01-02 at 10.11.12.png": "2023-01-02T10:11:12", "PXL_20220101_101112345.jpg": "2022-01-01T10:11:12",
             "photo_2023-07-01_12-30-45.jpg": "2023-07-01T12:30:45", "IMG_1234.JPG": None, "DSC09618.jpg": None,
             "20221399_x.jpg": None, "21000101.jpg": None}
    assert {n: dates.date_from_name(n) for n in cases} == cases
    assert dates.base_name("_DSC1291-2.jpg") == dates.base_name("_DSC1291 copy.JPG") == "_dsc1291"
    assert dates.base_name("IMG_0018 1.JPG") == "img_0018"


def test_edit_shift_suggest_and_undo_dates(lapi, liveworld):
    con = db(liveworld)
    still = item(lapi, "IMG_0100.JPG")
    clip = still["live_id"]
    r = lapi.post("/api/dates", json={"ids": [still["id"]], "set": "2019-07-04T09:30"})
    assert r.json() == {"changed": 1}
    after = item(lapi, "IMG_0100.JPG")
    assert after["taken_at"] == "2019-07-04T09:30:00" and after["date_src"] == "user"
    assert con.execute("SELECT taken_at FROM media WHERE id = ?", (clip,)).fetchone()[0] == "2019-07-04T09:30:00"  # motion follows
    info = lapi.get(f"/api/item/{still['id']}").json()
    assert info["date_original"]["orig_taken_at"].startswith("2023-03-01")
    # survives re-indexing
    subprocess.run([PY, "build_index.py", "--no-thumbs"], cwd=ROOT, env=liveworld["env"], check=True, capture_output=True)
    assert item(lapi, "IMG_0100.JPG")["taken_at"] == "2019-07-04T09:30:00"
    # shifting keeps order and spacing
    b = {n: item(lapi, n) for n in ("b_0.jpg", "b_2.jpg")}
    lapi.post("/api/dates", json={"ids": [b["b_0.jpg"]["id"], b["b_2.jpg"]["id"]], "first": "2023-04-02T12:00:00"})
    assert item(lapi, "b_0.jpg")["taken_at"] == "2023-04-02T12:00:00" and item(lapi, "b_2.jpg")["taken_at"] == "2023-04-02T12:00:20"
    assert lapi.post("/api/dates", json={"ids": [still["id"]], "set": "nonsense"}).status_code == 400
    assert lapi.post("/api/dates", json={"ids": [999999], "set": "2020-01-01T00:00"}).status_code == 404
    assert lapi.post("/api/dates", json={"ids": [still["id"]]}).status_code == 400
    # undo, twice over: back to the very first date
    lapi.post("/api/dates", json={"ids": [still["id"]], "set": "2018-01-01T00:00"})
    assert lapi.post("/api/dates/reset", json={"ids": [still["id"], b["b_0.jpg"]["id"], b["b_2.jpg"]["id"]]}).json()["reset"] == 4
    assert item(lapi, "IMG_0100.JPG")["taken_at"].startswith("2023-03-01") and item(lapi, "IMG_0100.JPG")["date_src"] == "exif"
    assert item(lapi, "b_2.jpg")["taken_at"] == "2023-04-01T12:00:20"


def test_suggested_dates_from_the_same_photo(lapi, liveworld):
    """An edited copy without a camera date gets its date from the original, if they look alike and it's earlier."""
    import shutil
    drive = liveworld["drive"]
    (drive / "edits").mkdir(exist_ok=True)
    shutil.copyfile(drive / "burst/b_0.jpg", drive / "edits/b_0-edited.jpg")
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-all=", str(drive / "edits/b_0-edited.jpg")], check=True)
    shutil.copyfile(drive / "phone/plain.jpg", drive / "edits/b_2.jpg")             # same name, different picture
    subprocess.run(["exiftool", "-q", "-overwrite_original", "-all=", str(drive / "edits/b_2.jpg")], check=True)
    subprocess.run([PY, "build_index.py", "--no-thumbs"], cwd=ROOT, env=liveworld["env"], check=True, capture_output=True)
    subprocess.run([PY, "ml.py", "run", "clip"], cwd=ROOT, env=liveworld["env"], check=True, capture_output=True)
    s = {i["name"]: i for i in lapi.get("/api/dates/suggestions").json()["items"]}
    assert s["b_0-edited.jpg"]["suggested"] == "2023-04-01T12:00:00" and "burst/b_0.jpg" in s["b_0-edited.jpg"]["why"]
    assert "b_2.jpg" not in s                                                       # looks different: no suggestion
    ed = s["b_0-edited.jpg"]["id"]
    assert lapi.get(f"/api/item/{ed}").json()["date_suggestion"]["suggested"] == "2023-04-01T12:00:00"
    assert lapi.post("/api/dates", json={"ids": [ed], "use_suggested": True}).json() == {"changed": 1}
    assert item(lapi, "b_0-edited.jpg")["taken_at"] == "2023-04-01T12:00:00"
    assert "b_0-edited.jpg" not in {i["name"] for i in lapi.get("/api/dates/suggestions").json()["items"]}


@pytest.mark.parametrize("page", [{"world": "liveworld"}], indirect=True)
def test_ui_edit_date_from_viewer_and_selection(page, lapi):
    page.goto(page.base + "/#/album/phone")
    page.locator("#days .tile").first.wait_for()
    page.locator("#days .tile").first.click()
    page.keyboard.press("i")
    page.locator("#vEditDate").click()
    page.locator("#dIn").fill("2021-12-25T08:15")
    shot(page, "edit-date")
    page.click(".date-dlg [data-x='1']")
    page.locator("#vInfo", has_text="date edited").wait_for()
    first = page.locator("#days .tile").first.get_attribute("data-id")
    page.keyboard.press("Escape")
    page.keyboard.press("Escape")
    assert any(i["taken_at"] == "2021-12-25T08:15:00" for i in lapi.get("/api/items").json()["items"])
    lapi.post("/api/dates/reset", json={"ids": [int(first)]})
    # select two and shift them
    page.goto(page.base + "/#/album/burst")
    tiles = page.locator("#days .tile")
    tiles.first.wait_for()
    tiles.nth(0).locator(".sel").click()
    tiles.nth(1).locator(".sel").click()
    page.click("#selDate")
    page.locator("#dIn").fill("2020-01-01T10:00")
    page.click(".date-dlg [data-x='1']")
    page.wait_for_function("document.querySelector('#selbar').hidden")
    got = sorted(i["taken_at"] for i in lapi.get("/api/items", params={"album": "burst"}).json()["items"])
    assert got[:2] == ["2020-01-01T10:00:00", "2020-01-01T10:00:10"]
    lapi.post("/api/dates/reset", json={"ids": [i["id"] for i in lapi.get("/api/items", params={"album": "burst"}).json()["items"]]})


# ---------------------------------------------------------------- downloading
def test_download_several_as_zip_with_live_clips_and_from_telegram(lapi, liveworld):
    import io, json, zipfile
    store = liveworld["base"] / "channel"
    by_caption = {m["caption"]: store / m["file"] for m in json.loads((store / "messages.json").read_text())}
    con = db(liveworld)
    gone = con.execute("SELECT id FROM media WHERE name = 'IMG_0200.JPG'").fetchone()[0]  # deleted from the drive earlier
    ids = [item(lapi, "plain.jpg")["id"], item(lapi, "IMG_0100.JPG")["id"], gone]
    r = lapi.get("/download", params={"ids": ",".join(map(str, ids)), "name": "My/picks"})
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "My picks.zip" in r.headers["content-disposition"].replace("%20", " ")
    z = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(z.namelist()) == ["IMG_0100.JPG", "IMG_0100.MOV", "IMG_0200.JPG", "plain.jpg"]  # motion clip included
    drive = liveworld["drive"]
    assert z.read("plain.jpg") == (drive / "phone/plain.jpg").read_bytes()
    assert z.read("IMG_0100.MOV") == (drive / "phone/IMG_0100.MOV").read_bytes()
    assert z.read("IMG_0200.JPG") == by_caption["phone/IMG_0200.JPG"].read_bytes()      # streamed from Telegram
    assert z.getinfo("plain.jpg").date_time[:3] == (2023, 3, 3)                          # dated like the photo


def test_download_one_is_the_file_itself(lapi, liveworld):
    r = lapi.get("/download", params={"ids": str(item(lapi, "plain.jpg")["id"])})
    assert r.status_code == 200 and r.content == (liveworld["drive"] / "phone/plain.jpg").read_bytes()
    assert "attachment" in r.headers["content-disposition"]
    assert lapi.get("/download", params={"ids": "999999"}).status_code == 404


@pytest.mark.parametrize("page", [{"world": "liveworld"}], indirect=True)
def test_ui_download_selection(page):
    page.goto(page.base + "/#/album/burst")
    tiles = page.locator("#days .tile")
    tiles.first.wait_for()
    tiles.nth(0).locator(".sel").click()
    tiles.nth(1).locator(".sel").click()
    with page.expect_download() as dl:
        page.click("#selDownload")
    assert dl.value.suggested_filename.endswith(".zip")
