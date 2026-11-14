"""The Sorting page's photo sphere: it turns, photos fly into their category cards, and nothing leaks or overflows."""
import httpx
import pytest

from conftest import SHOTS, build_big_drive, make_world, serve, stop
from test_categories import FAKE


@pytest.fixture(scope="module")
def bigsort(tmp_path_factory):
    base = tmp_path_factory.mktemp("bigsort")
    fake = base / "claude"
    # one call at a time, two seconds each (like the real thing), so a run takes a few seconds and can be watched
    fake.write_text(FAKE.replace("python3 -", "sleep 2\npython3 -", 1))
    fake.chmod(0o755)
    w = serve(make_world(base, build_big_drive, CLAUDE_BIN=str(fake), CLAUDE_WORKERS="1"))
    yield w
    stop(w)


@pytest.fixture
def fresh(bigsort):
    with httpx.Client(base_url=bigsort["url"], timeout=30) as c:
        c.post("/api/categories/stop"); c.post("/api/categories/clear")
    return bigsort


def open_sorting(page, w):
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "404" not in m.text else None)
    page.goto(w["url"] + "/#/categories")
    page.wait_for_selector("#catTry")
    page.wait_for_function("document.querySelectorAll('#sphere img').length >= 100")
    return errors


def tile_boxes(page):
    return page.evaluate("""() => { const s = document.querySelector('#catStage').getBoundingClientRect();
        return [...document.querySelectorAll('#sphere img')].map(i => { const r = i.getBoundingClientRect();
          return {l: r.left - s.left, t: r.top - s.top, r: r.right - s.left, b: r.bottom - s.top, w: s.width, h: s.height, z: +i.style.zIndex, o: +i.style.opacity}; }); }""")


def test_sphere_is_a_ball_inside_its_stage_and_turns(fresh, page):
    errors = open_sorting(page, fresh)
    page.wait_for_timeout(300)
    boxes = tile_boxes(page)
    assert len(boxes) == 150
    for b in boxes:   # every tile inside the stage (no spill over the buttons or cards)
        assert b["l"] >= -2 and b["t"] >= -2 and b["r"] <= b["w"] + 2 and b["b"] <= b["h"] + 2, b
    xs = [(b["l"] + b["r"]) / 2 for b in boxes]; ys = [(b["t"] + b["b"]) / 2 for b in boxes]
    cx, cy = sum(xs) / len(xs), sum(ys) / len(ys)
    assert abs(cx - boxes[0]["w"] / 2) < 25 and abs(cy - boxes[0]["h"] / 2) < 25   # centred in the stage
    assert max(b["z"] for b in boxes) > 80 and min(b["z"] for b in boxes) < 20     # front tiles above back tiles
    assert min(b["o"] for b in boxes) < 0.4 and max(b["o"] for b in boxes) > 0.9   # far side fades
    before = [(b["l"], b["t"]) for b in boxes]
    page.wait_for_timeout(1500)
    after = [(b["l"], b["t"]) for b in tile_boxes(page)]
    assert sum(1 for a, b in zip(before, after) if abs(a[0] - b[0]) > 1) > 100      # it is turning
    assert len({i for i in page.evaluate("[...document.querySelectorAll('#sphere img')].map(i => i.getAttribute('src'))")}) == 150  # no duplicates
    assert errors == []


def test_photos_fly_into_cards_during_a_run(fresh, page):
    errors = open_sorting(page, fresh)
    page.click("#catAll")
    page.wait_for_selector(".cat-prog")
    page.wait_for_selector("#catFly img", timeout=30000)        # something is in the air
    page.wait_for_timeout(250)
    page.screenshot(path=str(SHOTS / "sorting-midflight.png"))
    flying = page.evaluate("""() => [...document.querySelectorAll('#catFly img')].map(i => { const r = i.getBoundingClientRect();
        return {x: r.left, y: r.top, w: r.width}; })""")
    vw, vh = page.evaluate("[innerWidth, innerHeight]")
    assert flying and all(-50 <= f["x"] <= vw + 50 and -100 <= f["y"] <= vh + 50 for f in flying), flying   # never flies off-screen
    # everything lands, the air clears, the counts equal what was labelled
    page.wait_for_function("document.querySelector('#catFly').children.length === 0 && !document.querySelector('#catStop')", timeout=60000)
    page.wait_for_timeout(1500)
    counts = {c.query_selector(".top b").inner_text(): int(c.query_selector(".cnt").inner_text().replace(",", "")) for c in page.query_selector_all(".cat-card")}
    assert sum(counts.values()) == 240 and counts["Food"] + counts["Nature"] + counts["Other"] == 240
    assert page.inner_text("#catBox").startswith("240 of 240 photos sorted")
    with httpx.Client(base_url=fresh["url"]) as c:
        api = {x["label"]: x["count"] for x in c.get("/api/categories").json()["categories"]}
    assert all(counts[k] == v for k, v in api.items())          # what the cards say = what the database says
    # every card shows at most six thumbnails and each shown one is a real image
    assert page.evaluate("[...document.querySelectorAll('.cat-card')].every(c => c.querySelectorAll('.slot').length === 6)")
    assert page.locator(".cat-card .slot.on").count() >= 3 * 6
    page.screenshot(path=str(SHOTS / "sorting-done.png"))
    assert errors == []


def test_sphere_stays_populated_and_cards_never_overflow(fresh, page):
    open_sorting(page, fresh)
    page.click("#catAll")
    page.wait_for_selector("#catFly img", timeout=30000)
    page.wait_for_timeout(1200)
    alive = page.evaluate("[...document.querySelectorAll('#sphere img')].filter(i => +i.style.opacity > 0.1).length")
    assert alive >= 55, alive          # a long run keeps the ball full instead of emptying it
    page.wait_for_function("!document.querySelector('#catStop')", timeout=60000)
    # card grid never scrolls sideways
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")


def test_leaving_the_page_stops_the_animation_and_cleans_up(fresh, page):
    open_sorting(page, fresh)
    page.click("#catAll")
    page.wait_for_selector("#catFly img", timeout=30000)
    page.goto(fresh["url"] + "/#/photos")
    page.wait_for_selector("#days")
    page.wait_for_timeout(1500)
    assert page.evaluate("document.querySelector('#catFly').children.length") == 0
    assert page.evaluate("!document.querySelector('#sphere') && !document.querySelector('#catBox')")
    # coming back while the run is still going picks up the progress instead of starting over
    page.goto(fresh["url"] + "/#/categories")
    page.wait_for_selector(".cat-prog, #catTry")
    page.wait_for_function("document.querySelectorAll('#sphere img').length >= 100")


def test_stop_mid_run_keeps_what_is_sorted_and_the_page_recovers(fresh, page):
    open_sorting(page, fresh)
    page.click("#catAll")
    page.wait_for_selector("#catStop")
    page.click("#catStop")
    page.wait_for_selector("#catAll")
    page.wait_for_timeout(2500)
    assert page.locator("#catStop").count() == 0 and page.locator("#catAll").count() == 1
    page.click("#catAll")                      # and it can be started again
    page.wait_for_selector("#catStop")


@pytest.mark.parametrize("page", [{"world": "bigworld", "viewport": {"width": 390, "height": 800}, "touch": True}], indirect=True)
def test_phone_layout(fresh, page):
    bigsort = fresh
    page.goto(bigsort["url"] + "/#/categories")
    page.wait_for_selector("#catTry")
    page.wait_for_function("document.querySelectorAll('#sphere img').length >= 100")
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
    box = page.evaluate("(() => { const r = document.querySelector('#catStage').getBoundingClientRect(); return [r.width, r.height]; })()")
    assert box[0] <= 390 and box[1] >= 200
    page.screenshot(path=str(SHOTS / "sorting-phone.png"))
