"""The new features, driven in a real (headless) browser."""
import re

import pytest
from playwright.sync_api import expect

from conftest import all_oldest_first, shot

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
    page.click(".chip[data-f='']")
    expect(page.locator("#days .tile")).to_have_count(10)
    page.click(".chip[data-f=videos]")
    expect(page.locator("#days .tile")).to_have_count(3)
    expect(page.locator(".chip.on")).to_have_text("Videos")
    expect(page.locator("#pgSub")).to_contain_text("3 items")
    page.click(".chip[data-f=located]")
    expect(page.locator("#days .tile")).to_have_count(1)
    page.click(".chip[data-f='']")
    expect(page.locator("#days .tile")).to_have_count(10)
    page.click(".chip[data-f=raw]")
    expect(page.locator("#days .empty")).to_contain_text("Nothing matches this filter")
    page.click(".side a[data-route=albums]")                    # chips belong to a page
    page.click(".side a[data-route=photos]")
    expect(page.locator(".chip.on")).to_have_text("Photos")   # pages open on Photos
    page.click(".chip[data-f='']")
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
    page.click(".chip[data-f='']")                                # All: the timeline opens on Photos
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
def test_mobile_hides_scrubber_and_shows_selection_bar(page):
    go(page)
    expect(page.locator("#days .tile").first).to_be_visible()
    expect(page.locator("#scrubber")).to_be_hidden()
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
