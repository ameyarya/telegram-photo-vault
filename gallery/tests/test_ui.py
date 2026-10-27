"""Drives the real UI in headless Chromium. Screenshots land in tests/screenshots/ for a visual check."""
import pytest
from playwright.sync_api import expect

from conftest import all_oldest_first, shot

MOBILE = {"viewport": {"width": 390, "height": 844}}


def go(page, hash_=""):
    page.goto(page.base + "/" + hash_)
    page.wait_for_selector(".side a.active")


def active_nav(page):
    return page.locator(".side a.active").all_inner_texts()


def wait_thumbs(page):
    page.wait_for_function("""() => [...document.querySelectorAll('.tile img')].every(i => i.complete)
                              && document.querySelectorAll('.tile img.ok').length > 0""")


# ---------- navigation ----------
@pytest.mark.parametrize("hash_, expected", [
    ("", ["Photos"]),
    ("#/photos", ["Photos"]),
    ("#/albums", ["Albums"]),
    ("#/album/vacation", ["Vacation"]),
    ("#/album/family%2Fwedding%2Fday%201", ["Family"]),   # inside a folder: that folder stays highlighted
    ("#/all/family", ["Family"]),
])
def test_exactly_one_nav_item_is_highlighted(page, hash_, expected):
    go(page, hash_)
    assert [t.strip() for t in active_nav(page)] == expected


def test_highlight_follows_clicks(page):
    go(page)
    page.click(".side a[data-route=albums]")
    expect(page.locator(".side a.active")).to_have_text("Albums")
    page.click(".side a[data-route=photos]")
    expect(page.locator(".side a.active")).to_have_text("Photos")
    assert len(active_nav(page)) == 1


# ---------- timeline ----------
def test_photos_timeline(page):
    go(page)
    all_oldest_first(page)
    expect(page.locator(".tile")).to_have_count(10)
    days = page.locator(".day h3").all_inner_texts()
    assert len(days) == len(set(days)) == 6          # one heading per day, no duplicates
    assert "2023" in days[0]                          # oldest first: the screenshot from 2023
    expect(page.locator("#pgSub")).to_contain_text("10 items")
    wait_thumbs(page)
    broken = page.evaluate("[...document.querySelectorAll('.tile img')].filter(i => !i.naturalWidth).length")
    assert broken == 0
    shot(page, "photos-dark")


def test_rows_fill_the_width_and_keep_shapes(page):
    go(page, "#/album/vacation")
    wait_thumbs(page)
    box = page.locator("#days").bounding_box()
    for row in page.locator(".row").all():
        tiles = row.locator(".tile").all()
        last = tiles[-1].bounding_box()
        assert last["x"] + last["width"] <= box["x"] + box["width"] + 1   # never overflows
    go(page, "#/album/family%2Fwedding%2Fday%201")
    t = page.locator(".tile").first.bounding_box()
    assert t["height"] > t["width"]                   # the sideways-stored photo shows as portrait


def test_video_tile_shows_duration(page):
    go(page, "#/album/family%2Fwedding%2Fday%201")
    all_oldest_first(page)
    expect(page.locator(".tile .dur")).to_contain_text("0:02")


# ---------- albums ----------
def test_albums_page_is_your_albums_and_folders_are_in_the_sidebar(page):
    go(page, "#/albums")
    expect(page.locator(".card")).to_have_count(0)                          # no folders repeated here
    expect(page.locator(".note")).to_contain_text("Make your own albums")
    expect(page.locator("#topFolders a")).to_have_text(["Family", "iPhone", "Vacation"])
    page.locator("#topFolders a", has_text="Family").click()
    expect(page.locator(".page-head h1")).to_have_text("Family")
    page.wait_for_function("[...document.querySelectorAll('.card img')].every(i => i.complete && i.naturalWidth)")
    shot(page, "folder-family")
    page.locator(".card", has_text="Wedding").click()
    page.locator(".card", has_text="Day 1").click()
    expect(page.locator(".page-head h1")).to_have_text("Day 1")
    all_oldest_first(page)
    expect(page.locator(".tile")).to_have_count(2)
    page.click("#pgBack")                                   # back goes up one folder
    expect(page).to_have_url(page.base + "/#/album/family%2Fwedding")


def test_search_clear_button_only_when_typing(page):
    go(page)
    expect(page.locator("#qClear")).to_be_hidden()
    page.fill("#q", "x")
    expect(page.locator("#qClear")).to_be_visible()


def test_search_shows_matching_albums_and_photos(page):
    go(page)
    page.fill("#q", "day 2")
    page.press("#q", "Enter")
    expect(page).to_have_url(page.base + "/#/search/day%202")
    expect(page.locator(".page-head h1")).to_have_text("Results for “day 2”")
    expect(page.locator(".card")).to_have_count(1)                     # the "Day 2" folder
    expect(page.locator("#days .tile")).to_have_count(1)                # IMG_0001.heic lives there
    page.click("#qClear")
    expect(page.locator("#q")).to_have_value("")
    page.fill("#q", "zzz")
    page.press("#q", "Enter")
    expect(page.locator(".card")).to_have_count(0)
    expect(page.locator("#days .empty")).to_contain_text("No names, dates or cameras match")


# ---------- viewer ----------
def open_first_photo(page, album="vacation"):
    go(page, f"#/album/{album}")
    all_oldest_first(page)
    page.locator(".tile").first.click()
    expect(page.locator("#viewer")).to_be_visible()
    page.wait_for_function("document.querySelector('#vMain img.v-media')?.naturalWidth > 0")


def test_viewer_opens_and_back_arrow_closes(page):
    open_first_photo(page)
    expect(page.locator("#vBack")).to_be_visible()
    expect(page.locator("#vDl")).to_be_visible()
    expect(page.locator("#vInfoBtn")).to_be_visible()
    shot(page, "viewer")
    page.click("#vBack")
    expect(page.locator("#viewer")).to_be_hidden()
    expect(page).to_have_url(page.base + "/#/album/vacation")


def black_spot(page):
    """A point in the viewer's black space, beside or below the photo."""
    img = page.locator("img.v-media").bounding_box()
    main = page.locator("#vMain").bounding_box()
    if img["x"] - main["x"] > 40:
        return main["x"] + 20, main["y"] + main["height"] / 2
    return main["x"] + main["width"] / 2, main["y"] + main["height"] - 20


def test_click_on_the_black_space_closes_the_viewer(page):
    page.set_viewport_size({"width": 1400, "height": 700})                # a wide window: black space beside the photo
    open_first_photo(page)
    page.locator("img.v-media").wait_for()
    page.locator("img.v-media").click()                                    # on the photo: zooms, stays open
    expect(page.locator("#viewer")).to_be_visible()
    assert page.evaluate("zoom.s") > 1
    page.mouse.click(*black_spot(page))                                    # zoomed: first click zooms back out
    expect(page.locator("#viewer")).to_be_visible()
    assert page.evaluate("zoom.s") == 1
    page.mouse.click(*black_spot(page))                                    # then: back to the gallery
    expect(page.locator("#viewer")).to_be_hidden()
    expect(page).to_have_url(page.base + "/#/album/vacation")
    open_first_photo(page)
    page.click("#vNext")                                                   # buttons still just do their job
    expect(page.locator("#viewer")).to_be_visible()


def test_back_arrow_visible_on_huge_photo(page):
    """Regression: the old viewer pushed its close button off-screen for big photos."""
    page.set_viewport_size({"width": 1000, "height": 500})
    open_first_photo(page)
    b = page.locator("#vBack").bounding_box()
    assert b and 0 <= b["y"] and b["y"] + b["height"] <= 500
    img = page.locator("img.v-media").bounding_box()
    assert img["height"] <= 500 + 1


def test_escape_and_browser_back_close_viewer(page):
    open_first_photo(page)
    page.keyboard.press("Escape")
    expect(page.locator("#viewer")).to_be_hidden()
    page.locator(".tile").first.click()
    expect(page.locator("#viewer")).to_be_visible()
    page.go_back()
    expect(page.locator("#viewer")).to_be_hidden()
    expect(page).to_have_url(page.base + "/#/album/vacation")   # stayed on the album
    expect(page.locator(".tile")).to_have_count(3)


def test_next_previous_and_keyboard(page):
    open_first_photo(page)
    page.click("#vInfoBtn")
    expect(page.locator("#vInfo")).to_contain_text("beach.jpg")
    expect(page.locator("#vPrev")).to_be_hidden()
    page.keyboard.press("ArrowRight")
    expect(page.locator("#vInfo")).to_contain_text("sunset.jpg")
    page.keyboard.press("ArrowRight")
    expect(page.locator("#vInfo")).to_contain_text("pier.jpg")
    expect(page.locator("#vNext")).to_be_hidden()
    page.keyboard.press("ArrowRight")                 # past the end: stays put
    expect(page.locator("#vInfo")).to_contain_text("pier.jpg")
    page.keyboard.press("ArrowLeft")
    expect(page.locator("#vInfo")).to_contain_text("sunset.jpg")


def test_info_panel(page):
    open_first_photo(page)
    page.keyboard.press("i")
    info = page.locator("#vInfo")
    expect(info).to_be_visible()
    expect(info).to_contain_text("Feb 7, 2024")
    expect(info).to_contain_text("1200 × 800")
    expect(info).to_contain_text("Not backed up yet")
    shot(page, "viewer-info")
    page.click("#vInfoClose")
    expect(info).to_be_hidden()


def test_info_album_link_navigates(page):
    open_first_photo(page, "family%2Fwedding%2Fday%202")
    page.click("#vInfoBtn")
    page.click("#vInfo a[data-href^='#/album/']")
    expect(page.locator("#viewer")).to_be_hidden()
    expect(page.locator(".page-head h1")).to_have_text("Day 2")


def test_heic_photo_displays(page):
    open_first_photo(page, "family%2Fwedding%2Fday%202")
    w = page.evaluate("document.querySelector('img.v-media').naturalWidth")
    assert w > 0


def test_video_plays(page):
    go(page, "#/album/family%2Fwedding%2Fday%201")
    all_oldest_first(page)
    page.locator(".tile", has=page.locator(".dur")).click()
    page.wait_for_function("document.querySelector('video.v-media')?.readyState >= 1")
    assert page.evaluate("Math.round(document.querySelector('video.v-media').duration)") == 2


def test_download_link(page):
    open_first_photo(page)
    with page.expect_download() as d:
        page.click("#vDl")
    assert d.value.suggested_filename == "beach.jpg"


# ---------- mobile + light mode ----------
@pytest.mark.parametrize("page", [MOBILE], indirect=True)
def test_mobile_drawer(page):
    go(page)
    expect(page.locator("#side")).not_to_be_in_viewport()
    page.click("#menuBtn")
    page.wait_for_function("document.querySelector('#side').getBoundingClientRect().left === 0")
    expect(page.locator(".side a[data-route=photos]")).to_be_visible()
    shot(page, "mobile-drawer")
    page.locator("#topFolders a", has_text="Family").click()                # a folder, from the drawer
    expect(page.locator("#side")).not_to_be_in_viewport()
    expect(page.locator(".card")).to_have_count(2)
    a, b = page.locator(".card").nth(0).bounding_box(), page.locator(".card").nth(1).bounding_box()
    assert a["y"] == b["y"] and b["x"] > a["x"]   # two cards side by side on a phone
    shot(page, "mobile-folder")


@pytest.mark.parametrize("page", [MOBILE], indirect=True)
def test_mobile_viewer(page):
    open_first_photo(page)
    b = page.locator("#vBack").bounding_box()
    assert b["y"] >= 0 and b["x"] >= 0
    shot(page, "mobile-viewer")
    page.click("#vBack")
    expect(page.locator("#viewer")).to_be_hidden()


@pytest.mark.parametrize("page", [{"scheme": "light"}], indirect=True)
def test_light_mode(page):
    go(page)
    wait_thumbs(page)
    bg = page.evaluate("getComputedStyle(document.body).backgroundColor")
    assert bg == "rgb(255, 255, 255)"
    shot(page, "photos-light")


# ---------- folders ----------
def test_top_folder_shows_subfolders_not_a_dump(page):
    go(page, "#/album/family")
    expect(page.locator(".card b")).to_have_text(["Videos", "Wedding"])
    expect(page.locator(".card", has_text="Wedding")).to_contain_text("4 items · 2 folders")
    expect(page.locator(".tile")).to_have_count(0)          # no giant timeline
    expect(page.locator("#pgSub")).to_have_text("6 items in 2 folders")
    expect(page.locator(".all-link")).to_contain_text("View all 6 items")
    shot(page, "folder-family")


def test_folder_with_photos_and_subfolders(page):
    go(page, "#/album/family%2Fwedding")
    expect(page.locator(".card b")).to_have_text(["Day 1", "Day 2"])
    expect(page.locator(".sec")).to_have_text("Photos in this folder")
    expect(page.locator(".tile")).to_have_count(1)          # only group.jpg lives directly here
    expect(page.locator("#pgSub")).to_contain_text("1 item here · 4 items including sub-folders")
    expect(page.locator("#crumbs a")).to_have_text(["Family"])
    expect(page.locator("#pgSub")).to_contain_text("Fri, Jan 19, 2024")
    expect(page.locator("#pgSub")).not_to_contain_text("–")   # single day: no "X – X" range


def test_drill_down_breadcrumbs_and_back(page):
    go(page, "#/album/family")
    page.locator(".card", has_text="Wedding").click()
    page.locator(".card", has_text="Day 2").click()
    expect(page.locator(".page-head h1")).to_have_text("Day 2")
    expect(page.locator("#crumbs a")).to_have_text(["Family", "Wedding"])
    page.locator("#crumbs a", has_text="Family").click()
    expect(page.locator(".page-head h1")).to_have_text("Family")
    page.click("#pgBack")                                    # top-level folder: back to Photos
    expect(page).to_have_url(page.base + "/#/photos")


def test_view_all_as_one_timeline(page):
    go(page, "#/album/family")
    page.click(".all-link")
    expect(page.locator(".page-head h1")).to_contain_text("everything")   # the new page is up
    all_oldest_first(page)
    expect(page.locator(".tile")).to_have_count(6)
    page.click("#pgBack")
    expect(page).to_have_url(page.base + "/#/album/family")
    expect(page.locator(".card")).to_have_count(2)


def test_viewer_inside_folder_steps_only_through_that_folder(page):
    go(page, "#/album/family%2Fwedding")
    page.locator(".tile").first.click()
    page.click("#vInfoBtn")
    expect(page.locator("#vInfo")).to_contain_text("group.jpg")
    expect(page.locator("#vNext")).to_be_hidden()            # sub-folder photos aren't mixed in


# ---------- sort order ----------
def test_sort_newest_first_by_default_and_toggle(page):
    go(page)
    page.click(".chip[data-f='']")                                        # All (pages open on Photos)
    expect(page.locator("#sortBtn")).to_have_text("Newest first")         # newest first on every visit
    expect(page.locator(".day h3").first).to_contain_text("Aug 26")       # the newest day leads
    expect(page.locator(".tile")).to_have_count(10)
    expect(page.locator("#pgSub")).to_contain_text("May 1, 2023 – Mon, Aug 26, 2024")  # full range either way
    page.click("#sortBtn")
    expect(page.locator("#sortBtn")).to_have_text("Oldest first")
    assert "2023" in page.locator(".day h3").first.inner_text()          # the oldest day now leads
    page.reload()
    page.wait_for_selector(".tile")
    expect(page.locator("#sortBtn")).to_have_text("Newest first")         # not remembered: back to newest
    shot(page, "photos-newest-first")


def test_sort_applies_to_albums_and_viewer(page):
    go(page, "#/album/vacation")
    all_oldest_first(page)
    page.click("#sortBtn")
    page.locator(".tile").first.click()
    page.click("#vInfoBtn")
    expect(page.locator("#vInfo")).to_contain_text("pier.jpg")            # newest photo first
    page.keyboard.press("ArrowRight")
    expect(page.locator("#vInfo")).to_contain_text("sunset.jpg")          # next = older


def test_no_sort_button_on_folder_with_only_subfolders(page):
    go(page, "#/album/family")
    expect(page.locator(".card")).to_have_count(2)
    expect(page.locator("#sortBtn")).to_have_count(0)


def test_sidebar_shows_backed_up_percentage(page):
    go(page)
    expect(page.locator(".side")).to_contain_text("Backed up 0%")                 # Telegram is off in the test world
    assert page.locator(".side [title='0 of 10 photos & videos in Telegram']").count() == 1   # exact numbers on hover
    assert page.evaluate("[backedPct(9999, 10000), backedPct(5, 10), backedPct(10, 10), backedPct(0, 0)]") == [99, 50, 100, 100]


def test_refresh_button_reloads_in_place(page):
    go(page, "#/album/vacation")
    expect(page.locator(".head-actions #refreshBtn")).to_be_visible()
    assert page.locator("#restoreBtn").count() == 0                                          # Restore is on Backup now
    page.click(".chip[data-f='']")
    with page.expect_response(lambda r: "/api/status" in r.url):
        page.click("#refreshBtn")
    expect(page.locator(".chip.on")).to_have_attribute("data-f", "")                          # the filter stays
    expect(page).to_have_url(page.base + "/#/album/vacation")


def test_view_options_layout_size_and_shape(page):
    go(page)
    all_oldest_first(page)
    expect(page.locator(".day h3").first).to_be_visible()
    h_medium = page.locator(".tile").first.bounding_box()["height"]
    page.click("#viewBtn")
    expect(page.locator("#viewMenu")).to_be_visible()
    page.click('.seg[data-k="group"][data-v="none"]')                     # continuous: no day headings
    expect(page.locator("#days .day h3")).to_have_count(0)
    expect(page.locator("#days .day")).to_have_count(1)
    page.click('.seg[data-k="shape"][data-v="square"]')                   # square tiles
    for t in page.locator(".tile").all()[:5]:
        b = t.bounding_box()
        assert abs(b["width"] - b["height"]) <= 2
    page.click('.seg[data-k="size"][data-v="l"]')                         # bigger
    assert page.locator(".tile").first.bounding_box()["height"] > h_medium
    shot(page, "view-continuous-square-large")
    page.reload()
    page.wait_for_selector(".tile")
    expect(page.locator("#days .day h3")).to_have_count(0)                # remembered
    page.click("#viewBtn")
    expect(page.locator('.seg.on[data-k="shape"]')).to_have_text("Square")
    for k, v in (("group", "day"), ("size", "m"), ("shape", "actual")):
        page.click(f'.seg[data-k="{k}"][data-v="{v}"]')
    expect(page.locator(".day h3").first).to_be_visible()
    page.mouse.click(5, 300)                                              # a click elsewhere closes the menu
    expect(page.locator("#viewMenu")).to_be_hidden()


def test_folders_fold_away_and_stay_folded(page):
    go(page)
    expect(page.locator("#topFolders a").first).to_be_visible()
    page.click("#foldersToggle")
    expect(page.locator("#topFolders")).to_be_hidden()
    expect(page.locator("#foldersToggle")).to_have_attribute("aria-expanded", "false")
    page.reload()
    page.wait_for_selector(".side a.active")
    expect(page.locator("#topFolders")).to_be_hidden()
    page.click("#foldersToggle")
    expect(page.locator("#topFolders a").first).to_be_visible()
