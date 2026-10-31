"""The new features, driven in a real (headless) browser."""
import re

import pytest
from playwright.sync_api import expect

from conftest import pick_filter, all_oldest_first, shot

ML = {"world": "mlworld"}
BIG = {"world": "bigworld"}
CLOUD = {"world": "cloud"}


def go(page, hash_=""):
    page.goto(page.base + "/" + hash_)
    page.wait_for_selector(".side a.active, .page-head h1")


def open_photo(page, album, name):
    go(page, f"#/album/{album}")
    all_oldest_first(page)
    page.locator(f"#days .tile:has(img)").first.wait_for()
    tiles = page.locator("#days .tile")
    for i in range(tiles.count()):
        tiles.nth(i).click()
        page.click("#vInfoBtn") if not page.locator("#vInfo").is_visible() else None
        if name in page.locator("#vInfo").inner_text():
            return
        page.keyboard.press("Escape")
    raise AssertionError(f"{name} not found")


# ---------- filter chips ----------
def test_filter_chips(page):
    go(page)
    expect(page.locator(".chip.on")).to_have_text("Photos")   # pages open on Photos
    pick_filter(page, "")
    expect(page.locator("#days .tile")).to_have_count(10)
    pick_filter(page, "videos")
    expect(page.locator("#days .tile")).to_have_count(3)
    expect(page.locator(".chip.on")).to_have_text("Videos")
    expect(page.locator("#pgSub")).to_contain_text("3 items")
    pick_filter(page, "located")
    expect(page.locator("#days .tile")).to_have_count(1)
    pick_filter(page, "")
    expect(page.locator("#days .tile")).to_have_count(10)
    pick_filter(page, "raw")
    expect(page.locator("#days .empty")).to_contain_text("Nothing matches this filter")
    page.click(".side a[data-route=albums]")                    # chips belong to a page
    page.click(".side a[data-route=photos]")
    expect(page.locator(".chip.on")).to_have_text("Photos")   # pages open on Photos
    pick_filter(page, "")
    expect(page.locator("#days .tile")).to_have_count(10)


# ---------- favorites ----------
def test_favorite_from_viewer_and_favorites_page(page):
    open_photo(page, "vacation", "sunset.jpg")
    expect(page.locator("#vFav")).not_to_have_class(re.compile(r"\bon\b"))
    page.click("#vFav")
    expect(page.locator("#vFav")).to_have_class(re.compile(r"\bon\b"))
    page.keyboard.press("Escape")
    page.click(".side a[data-route=favorites]")
    expect(page.locator("#pgBack")).to_have_count(0)            # a main page: no back arrow
    expect(page.locator("#days .tile")).to_have_count(1)
    expect(page.locator("#days .tile .fav")).to_have_count(1)
    expect(page.locator(".chip")).to_have_count(0)               # no chips on Favorites
    page.locator("#days .tile").first.click()
    page.keyboard.press("f")                                      # keyboard toggles too
    expect(page.locator("#vFav")).not_to_have_class(re.compile(r"\bon\b"))
    page.keyboard.press("Escape")
    page.reload()
    expect(page.locator("#days .empty")).to_be_visible()


def test_unfavorite_from_the_favorites_page(page, api, items):
    ids = [i["id"] for i in list(items.values())[:2]]
    api.post("/api/favorites", json={"ids": ids})
    try:
        go(page, "#/favorites")
        tiles = page.locator("#days .tile")
        expect(tiles).to_have_count(2)
        tiles.nth(0).hover(); tiles.nth(0).locator(".sel").click()
        expect(page.locator("#selFav")).to_have_attribute("title", "Remove from favorites")
        page.click("#selFav")
        expect(tiles).to_have_count(1)                            # gone from the page, still in the library
        page.click("#undoFav")
        expect(tiles).to_have_count(2)
        tiles.nth(0).click()                                      # or from the viewer: gone when it closes
        page.click("#vFav")
        page.keyboard.press("Escape")
        expect(tiles).to_have_count(1)
    finally:
        api.post("/api/favorites/remove", json={"ids": ids})


# ---------- selection + your albums ----------
def test_select_add_to_new_album_remove_rename_delete(page):
    go(page, "#/album/vacation")
    tiles = page.locator("#days .tile")
    tiles.nth(0).hover(); tiles.nth(0).locator(".sel").click()
    expect(page.locator("#selbar")).to_be_visible()
    tiles.nth(1).click()                                          # while selecting, a click selects
    expect(page.locator("#selCount")).to_have_text("2 selected")
    expect(page.locator("#viewer")).to_be_hidden()
    page.click("#selAlbum")
    page.click(".pick-list [data-id=new]")
    page.fill("#dlgIn", "Trip picks")
    page.keyboard.press("Enter")
    expect(page.locator(".toast")).to_contain_text("Added 2 items to Trip picks")
    expect(page.locator("#selbar")).to_be_hidden()
    page.click(".toast a")
    expect(page.locator(".page-head h1")).to_have_text("Trip picks")
    expect(page.locator("#days .tile")).to_have_count(2)
    shot(page, "user-album")
    t = page.locator("#days .tile").first
    t.hover(); t.locator(".sel").click()
    expect(page.locator("#selRemove")).to_be_visible()
    page.click("#selRemove")
    expect(page.locator("#days .tile")).to_have_count(1)
    page.click("#uaRename")
    page.fill("#dlgIn", "Best of trip")
    page.click(".dialog [data-x='1']")
    expect(page.locator(".page-head h1")).to_have_text("Best of trip")
    page.click(".side a[data-route=albums]")
    expect(page.locator(".card.ua")).to_have_count(1)
    expect(page.locator(".card.ua")).to_contain_text("1 item")
    page.locator(".card.ua").click()
    page.click("#uaDelete")
    expect(page.locator(".dialog")).to_contain_text("photos stay on your drive")
    page.click(".dialog [data-x='1']")
    expect(page).to_have_url(page.base + "/#/albums")
    expect(page.locator(".card.ua")).to_have_count(0)
    page.click(".side a[data-route=photos]")
    pick_filter(page, "")                                # All: the timeline opens on Photos
    expect(page.locator("#days .tile")).to_have_count(10)         # nothing deleted


def test_add_to_existing_album_from_viewer_shows_in_info(page):
    page.request.post(page.base + "/api/user-albums", data={"name": "Keepers"})
    open_photo(page, "vacation", "pier.jpg")
    page.click("#vAlbum")
    page.locator(".pick-list button", has_text="Keepers").click()
    expect(page.locator(".toast")).to_contain_text("Added 1 item to Keepers")
    expect(page.locator("#vInfo")).to_contain_text("In your albums")
    expect(page.locator("#vInfo .info-chips a", has_text="Keepers")).to_be_visible()
    for a in page.request.get(page.base + "/api/user-albums").json():   # clean up for other tests
        page.request.post(page.base + f"/api/user-albums/{a['id']}/delete")


def test_escape_clears_selection(page):
    go(page)
    t = page.locator("#days .tile").first
    t.hover(); t.locator(".sel").click()
    expect(page.locator("#selbar")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#selbar")).to_be_hidden()


# ---------- info panel: camera + location ----------
def test_info_shows_camera_settings_and_location(page):
    open_photo(page, "vacation", "beach.jpg")
    info = page.locator("#vInfo")
    expect(info).to_contain_text("SONY ILCE-7M3")
    expect(info).to_contain_text("FE 24-70mm F2.8 GM · 1/250 · f/2.8 · 35mm · ISO 200")
    page.keyboard.press("ArrowRight") if "pier" not in info.inner_text() else None
    for _ in range(3):
        if "pier.jpg" in info.inner_text():
            break
        page.keyboard.press("ArrowRight")
    expect(info.locator("a", has_text="Open map")).to_have_attribute("href", re.compile(r"openstreetmap\.org/\?mlat=26\.91"))
    shot(page, "viewer-info-camera")


# ---------- zoom ----------
def test_zoom_click_keys_and_button(page):
    open_photo(page, "vacation", "beach.jpg")
    page.click("#vInfoClose")
    img = page.locator("#vMain img.v-media")
    page.wait_for_function("document.querySelector('#vMain img.v-media')?.naturalWidth > 0")
    img.click()                                                  # one click zooms in
    expect(img).to_have_attribute("style", re.compile(r"scale\(2\.5\)"))
    expect(page.locator("#vZoom")).to_have_class(re.compile(r"\bon\b"))
    page.keyboard.press("ArrowRight")                            # arrows don't change photo while zoomed
    expect(page.locator("#vMain img.v-media")).to_have_attribute("style", re.compile(r"scale"))
    page.keyboard.press("+")
    expect(img).to_have_attribute("style", re.compile(r"scale\(3\.75\)"))
    box = img.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down(); page.mouse.move(box["x"] + box["width"] / 2 + 80, box["y"] + box["height"] / 2); page.mouse.up()
    expect(img).to_have_attribute("style", re.compile(r"translate\("))
    expect(img).to_have_attribute("style", re.compile(r"scale\(3\.75\)"))    # dragging around isn't a click
    page.mouse.click(600, 450)                                   # a click on the photo zooms back out
    expect(img).not_to_have_attribute("style", re.compile(r"scale"))
    page.keyboard.press("+")
    page.keyboard.press("0")
    expect(img).not_to_have_attribute("style", re.compile(r"scale"))
    page.click("#vZoom")
    expect(img).to_have_attribute("style", re.compile(r"scale\(2\.5\)"))
    page.keyboard.press("Escape")                                # first Escape un-zooms
    expect(page.locator("#viewer")).to_be_visible()
    expect(img).not_to_have_attribute("style", re.compile(r"scale"))
    page.keyboard.press("Escape")
    expect(page.locator("#viewer")).to_be_hidden()


def test_zoom_hidden_for_videos(page):
    go(page, "#/album/family%2Fwedding%2Fday%201")
    all_oldest_first(page)
    page.locator(".tile", has=page.locator(".dur")).click()
    expect(page.locator("#vZoom")).to_be_hidden()


# ---------- date scrubber ----------
@pytest.mark.parametrize("page", [BIG], indirect=True)
def test_scrubber_jumps_and_loads_both_ways(page):
    go(page)
    all_oldest_first(page)
    expect(page.locator("#pgSub")).to_contain_text("240 items")
    expect(page.locator("#scrubber")).to_be_visible()
    expect(page.locator("#scrubber .yr")).to_have_text(["2022", "2023"])
    shot(page, "scrubber")
    sc = page.locator("#scrubber").bounding_box()
    page.mouse.move(sc["x"] + sc["width"] / 2, sc["y"] + sc["height"] * 0.8)
    expect(page.locator("#scTip")).to_contain_text("2023")
    page.mouse.click(sc["x"] + sc["width"] / 2, sc["y"] + sc["height"] * 0.8)
    on_screen = """() => [...document.querySelectorAll('#days .day h3')]
                     .find(h => h.getBoundingClientRect().top >= 60)?.textContent"""
    page.wait_for_function(f"({on_screen})()?.includes('2023')")    # jumped straight to late 2023
    for _ in range(50):                                            # ...and the earlier months fill in above it
        if page.evaluate("feed.base") == 0:
            break
        page.wait_for_timeout(100)
    assert page.evaluate("feed.base") == 0
    assert "2023" in page.evaluate(f"({on_screen})()")             # without moving what's on screen
    expect(page.locator("#days .day h3").first).to_contain_text("2022")
    page.evaluate("document.querySelector('#main').scrollTop = 1e9")
    expect(page.locator("#days .tile")).to_have_count(240)        # and the rest below, by scrolling down


@pytest.mark.parametrize("page", [BIG], indirect=True)
def test_scrubber_follows_newest_first(page):
    go(page)
    all_oldest_first(page)
    page.click("#sortBtn")
    expect(page.locator("#scrubber .yr")).to_have_text(["2023", "2022"])
    sc = page.locator("#scrubber").bounding_box()
    page.mouse.click(sc["x"] + sc["width"] / 2, sc["y"] + sc["height"] * 0.9)
    page.wait_for_function("""[...document.querySelectorAll('#days .day h3')]
                              .find(h => h.getBoundingClientRect().top >= 60)?.textContent.includes('2022')""")


def test_no_scrubber_for_small_albums(page):
    go(page, "#/album/vacation")
    expect(page.locator("#days .tile")).to_have_count(3)
    expect(page.locator("#scrubber")).to_be_hidden()


# ---------- restore from the page ----------
@pytest.mark.parametrize("page", [CLOUD], indirect=True)
def test_restore_from_backup_page_and_watch_progress(page):
    go(page, "#/backup")
    expect(page.locator("#restorePanel")).to_be_visible()
    page.select_option("#restoreWhat", label="Vacation")
    page.click("#restoreGo")
    expect(page.locator(".dialog")).to_contain_text("Photo Vault Restore")
    page.click(".dialog [data-x='1']")
    expect(page.locator(".toast")).to_contain_text("Restoring 3 files")
    page.click(".toast a")
    expect(page.locator("#dashJobs")).to_contain_text("done · 3 / 3 files · 3 checked against the drive", timeout=20000)
    expect(page.locator("#dashJobs [data-reveal]").first).to_be_visible()
    page.locator("#dashJobs [data-reveal]").first.click()
    shot(page, "backup-restores")


@pytest.mark.parametrize("page", [CLOUD], indirect=True)
def test_restore_selection(page):
    go(page, "#/album/vacation")
    t = page.locator("#days .tile").first
    t.hover(); t.locator(".sel").click()
    page.click("#selRestore")
    page.click(".dialog [data-x='1']")
    expect(page.locator(".toast")).to_contain_text("Restoring 1 file")


def test_restore_without_telegram_explains(page):
    go(page, "#/backup")
    page.click("#restoreGo")
    page.click(".dialog [data-x='1']")
    expect(page.locator(".toast")).to_contain_text("Telegram isn't connected")


# ---------- smart search, people, similar ----------
@pytest.mark.parametrize("page", [ML], indirect=True)
def test_smart_search_best_matches(page):
    go(page)
    page.fill("#q", "red")
    page.press("#q", "Enter")
    expect(page.locator("#smartRow .tile").first).to_be_visible()
    page.locator("#smartRow .tile").first.click()
    page.click("#vInfoBtn")
    expect(page.locator("#vInfo")).to_contain_text("red.jpg")
    shot(page, "smart-search")


def test_smart_search_explains_when_not_analysed(page):
    go(page, "#/search/beach")
    expect(page.locator("#smart .note")).to_contain_text("appears once photo analysis has run")


@pytest.mark.parametrize("page", [ML], indirect=True)
def test_people_name_a_person(page):
    go(page, "#/people")
    expect(page.locator(".person")).to_have_count(2)
    expect(page.locator(".person img")).to_have_count(2)
    page.locator(".person").first.click()
    expect(page.locator("#days .tile")).to_have_count(3)
    expect(page.locator(".page-head h1")).to_have_text(re.compile("Unnamed person|Alice|Bob"))
    page.click("#pName")
    page.fill("#dlgIn", "Dana")
    page.keyboard.press("Enter")
    expect(page.locator(".page-head h1")).to_have_text("Dana")
    page.click("#pgBack")
    expect(page.locator(".person b", has_text="Dana")).to_be_visible()
    shot(page, "people")
    pid = page.locator(".person", has_text="Dana").get_attribute("data-id")
    page.request.post(page.base + f"/api/people/{pid}/name", data={"name": ""})


@pytest.mark.parametrize("page", [ML], indirect=True)
def test_similar_shots_page(page):
    go(page, "#/similar")
    expect(page.locator(".sim-group")).to_have_count(1)
    expect(page.locator(".sim-group .tile")).to_have_count(3)
    page.locator(".sim-group .tile").nth(1).click()
    page.click("#vInfoBtn")
    expect(page.locator("#vInfo")).to_contain_text("shot_1.jpg")
    page.keyboard.press("ArrowRight")
    expect(page.locator("#vInfo")).to_contain_text("shot_2.jpg")
    expect(page.locator("#vNext")).to_be_hidden()                 # the viewer stays within the group
    shot(page, "similar")


def test_people_and_similar_explain_when_empty(page):
    go(page, "#/people")
    expect(page.locator(".note")).to_contain_text("analys")
    go(page, "#/similar")
    expect(page.locator(".note")).to_contain_text("No similar groups")


def test_held_upload_is_shown_and_approved(page, world):
    """A surprise flood of new drive files (a folder renamed in Finder) waits on the Backup page for an OK."""
    hold = world["state"] / "upload-hold.json"
    hold.write_text('{"files": 1500, "folders": {"samsung": 1500}, "since": "2026-10-29 10:00"}')
    try:
        go(page, "#/backup")
        expect(page.locator("#holdNote")).to_contain_text("1,500 new files")
        expect(page.locator("#holdNote")).to_contain_text("samsung: 1,500")
        shot(page, "backup-hold")
        page.click("#holdGo")
        expect(page.locator("#holdNote")).to_have_count(0)
        assert (world["state"] / ".upload-approved").exists() and not hold.exists()
    finally:
        hold.unlink(missing_ok=True)
        (world["state"] / ".upload-approved").unlink(missing_ok=True)


# ---------- backup dashboard ----------
def test_backup_dashboard(page):
    go(page, "#/backup")
    expect(page.locator("#dashTotal")).to_contain_text("0%")
    expect(page.locator("#dashTotal")).to_contain_text("0 of 10 items")
    expect(page.locator("#dashTotal")).to_contain_text("Exact duplicates skipped")
    expect(page.locator("#dashStatus")).to_contain_text("Failed batches")
    expect(page.locator("#dashStatus")).to_contain_text("1 (retried automatically)")
    expect(page.locator("#dashFolders .hbar")).to_have_count(3)
    expect(page.locator("#dashYears .hbar")).to_have_count(2)
    expect(page.locator("#dashMissing")).to_contain_text("vacation/beach.jpg")
    shot(page, "backup-dashboard")
    page.click("#seeMissing")
    expect(page.locator(".chip.on")).to_have_text("Not backed up")
    expect(page.locator("#days .tile")).to_have_count(10)


@pytest.mark.parametrize("page", [ML], indirect=True)
def test_dashboard_analysis_panel(page):
    go(page, "#/backup")
    expect(page.locator("#dashMl")).to_contain_text("14 of 14")
    expect(page.locator("#dashMl")).to_contain_text("People")
    page.click("#runMl")
    expect(page.locator(".toast")).to_contain_text("Analysis started")


# ---------- mobile ----------
@pytest.mark.parametrize("page", [{"world": "bigworld", "viewport": {"width": 390, "height": 844}}], indirect=True)
def test_mobile_scrubber_appears_only_while_scrolling_and_selection_bar(page):
    go(page)
    expect(page.locator("#days .tile").first).to_be_visible()
    sc = page.locator("#scrubber")
    page.wait_for_function("getComputedStyle(document.querySelector('#scrubber')).opacity === '0'")   # out of the way
    page.evaluate("document.querySelector('#main').scrollTop += 400")
    expect(sc).to_have_class(re.compile("active"))                                    # shows while scrolling
    page.wait_for_function("getComputedStyle(document.querySelector('#scrubber')).opacity === '0'", timeout=5000)
    t = page.locator("#days .tile").first
    t.locator(".sel").click(force=True)
    expect(page.locator("#selbar")).to_be_visible()
    shot(page, "mobile-selection")


# ---------- folder names (gallery only) ----------
def test_rename_folder_in_the_gallery_only(page, api, world):
    go(page, "#/album/vacation")
    page.click("#renameFolder")
    page.fill("#dlgIn", "Beach trip 2024")
    page.click(".dialog [data-x='1']")
    expect(page.locator(".page-head h1")).to_have_text("Beach trip 2024")
    expect(page.locator(".side a[data-folder='vacation']")).to_contain_text("Beach trip 2024")
    assert (world["drive"] / "vacation").is_dir() and not (world["drive"] / "Beach trip 2024").exists()  # drive untouched
    assert api.get("/api/folder-names").json() == {"vacation": "Beach trip 2024"}
    assert api.post("/api/folder-names", json={"path": "vacation", "name": ""}).json()["name"] is None
    assert api.get("/api/folder-names").json() == {}
    assert api.post("/api/folder-names", json={"path": "no/such", "name": "x"}).status_code == 404


def test_people_page_puts_main_people_first_and_folds_the_rest(page):
    """A big library: named people and anyone in 10+ photos are shown; strangers in a photo or two behind a link."""
    many = [{"id": i, "name": "Sam" if i == 1 else None, "aliases": None, "n": 50 - i, "cover_face": None} for i in range(1, 16)]
    few = [{"id": 100 + i, "name": None, "aliases": None, "n": 2, "cover_face": None} for i in range(5)]
    page.route("**/api/people", lambda r: r.fulfill(json=many + few))
    go(page, "#/people")
    expect(page.locator("#peopleGrid .person")).to_have_count(15)
    expect(page.locator("#peopleGrid .person").first).to_contain_text("Sam")
    expect(page.locator("#peopleGrid .person b[data-name]").first).to_have_text("Who's this?")
    expect(page.locator("#peopleMore")).to_have_text("Show 5 more people (in fewer than 10 photos)")
    page.click("#peopleMore")
    expect(page.locator("#peopleGrid .person")).to_have_count(20)


@pytest.mark.parametrize("page", [BIG], indirect=True)
def test_scrubber_drag_bubble_and_fair_spacing(page):
    go(page)
    all_oldest_first(page)
    sc = page.locator("#scrubber").bounding_box()
    x = sc["x"] + sc["width"] / 2
    page.mouse.move(x, sc["y"] + sc["height"] * 0.1)
    page.mouse.down()                                                   # drag from the top ...
    page.mouse.move(x, sc["y"] + sc["height"] * 0.5, steps=5)
    page.mouse.move(x, sc["y"] + sc["height"] * 0.85, steps=5)
    expect(page.locator("#scrubber")).to_have_class(re.compile("dragging"))
    expect(page.locator("#scTip")).to_contain_text("2023")              # the bubble follows the finger
    page.mouse.up()                                                     # ... and let go: jump there
    on_screen = """() => [...document.querySelectorAll('#days .day h3')]
                     .find(h => h.getBoundingClientRect().top >= 60)?.textContent"""
    page.wait_for_function(f"({on_screen})()?.includes('2023')")
    page.evaluate("document.querySelector('#main').scrollTop += 300")    # scrolling shows the date bubble
    expect(page.locator("#scrubber")).to_have_class(re.compile("active"))
    expect(page.locator("#scTip")).to_be_visible()
    # a year with one photo next to one with thousands still gets a readable slot on the bar
    gap = page.evaluate("""() => { const f = {months: [{m: '2010-05', n: 1}, {m: '2011-03', n: 1},
                                     ...Array.from({length: 12}, (_, i) => ({m: '2024-' + String(i + 1).padStart(2, '0'), n: 5000}))]};
                                   f.total = f.months.reduce((a, m) => a + m.n, 0); scrubberLayout(f); return f.ys[1] - f.ys[0]; }""")
    assert gap > 18


def test_filters_are_one_dropdown(page):
    go(page)
    expect(page.locator("#filterBtn")).to_have_text("Photos")          # the current filter, not rows of pills
    expect(page.locator("#filterMenu")).to_be_hidden()
    page.click("#filterBtn")
    expect(page.locator("#filterMenu .chip")).to_have_count(10)
    page.click("#filterMenu .chip[data-f='videos']")
    expect(page.locator("#filterBtn")).to_have_text("Videos")
    expect(page.locator("#filterMenu")).to_be_hidden()
    expect(page.locator("#days .tile")).to_have_count(3)
    page.click("#filterBtn")
    page.mouse.click(600, 600)                                           # a click elsewhere closes it
    expect(page.locator("#filterMenu")).to_be_hidden()


@pytest.mark.parametrize("page", [{"viewport": {"width": 390, "height": 844}}], indirect=True)
def test_menus_fit_on_a_phone(page):
    go(page)
    page.click("#viewBtn")
    b = page.locator("#viewMenu").bounding_box()
    assert b["x"] >= 0 and b["x"] + b["width"] <= 390                   # not cut off at the edge
    page.click(".page-head h1")                                          # closes the View menu
    page.click("#filterBtn")
    b = page.locator("#filterMenu").bounding_box()
    assert b["x"] >= 0 and b["x"] + b["width"] <= 390
    shot(page, "phone-filter-menu")


@pytest.mark.parametrize("page", [{"viewport": {"width": 1300, "height": 800}}, {"viewport": {"width": 390, "height": 844}}], indirect=True)
def test_page_buttons_are_round_icons_on_the_right(page):
    go(page)
    title = page.locator(".page-head h1").bounding_box()
    boxes = [page.locator(f"#{b}").bounding_box() for b in ("filterBtn", "viewBtn", "refreshBtn", "sortBtn")]
    for b in boxes:
        assert abs(b["width"] - 40) <= 1 and abs(b["height"] - 40) <= 1          # same round icon size
        assert abs(b["y"] - boxes[0]["y"]) <= 1                                   # one row, with the title
        assert b["x"] > title["x"] + title["width"]                               # to the right of the title
    xs = [b["x"] for b in boxes]
    assert xs == sorted(xs)
    page_right = page.locator("#days").bounding_box()
    assert abs((boxes[-1]["x"] + 40) - (page_right["x"] + page_right["width"])) <= 4   # flush with the right edge
    assert page.locator("#sortBtn").get_attribute("title").startswith("Newest first")    # names are tooltips
    assert page.locator("#filterBtn").get_attribute("title") == "Showing: Photos"
    expect(page.locator("#pgSub")).to_be_hidden()                                   # no count/date line on Photos
    shot(page, f"header-icons-{page.viewport_size['width']}")


@pytest.mark.parametrize("page", [{"world": "bigworld", "viewport": {"width": 1300, "height": 800}},
                                  {"world": "bigworld", "viewport": {"width": 390, "height": 844}}], indirect=True)
def test_rows_and_buttons_line_up_with_the_scrubber(page):
    """Regression: rows were laid out before the date bar appeared and ran under it; on phones a stray 44px margin
    pushed the buttons in from the edge."""
    go(page)
    page.wait_for_selector("body.with-scrubber")
    page.wait_for_timeout(300)
    right = page.evaluate("""() => { const d = document.querySelector('#days').getBoundingClientRect().right;
        return [d, Math.max(...[...document.querySelectorAll('#days .tile')].map(t => t.getBoundingClientRect().right)),
                document.querySelector('#sortBtn').getBoundingClientRect().right]; }""")
    days, tiles, sort = right
    assert tiles <= days + 1                                   # no photo runs under the date bar
    assert abs(sort - days) <= 2                               # the buttons end where the photos do
    if page.viewport_size["width"] < 600:
        assert 390 - sort <= 12                                # close to the phone's edge
