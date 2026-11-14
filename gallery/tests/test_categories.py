"""Photo categories, chosen on this Mac from the smart-search fingerprints (a colour stand-in model in the tests)."""
import re
import subprocess

import httpx
import numpy as np
import pytest

from conftest import PY, ROOT, SHOTS
import categories as cats

KEYS = [k for k, (_, d) in cats.CATEGORIES.items() if d]


def probs(**scores):
    p = np.full(len(KEYS), 0.001)
    for k, v in scores.items():
        p[KEYS.index(k)] = v
    return p / p.sum() if p.sum() > 1 else p


def test_pick_follows_the_model_unless_faces_say_otherwise():
    assert cats.pick(probs(food=0.9), KEYS, []) == "food"
    assert cats.pick(probs(food=0.9), KEYS, [0.4]) == "food"                 # a face doesn't turn a meal into a selfie
    assert cats.pick(probs(selfies=0.5, people=0.4), KEYS, [0.4]) == "selfies"   # one big face
    assert cats.pick(probs(people=0.9), KEYS, [0.4]) == "selfies"
    assert cats.pick(probs(people=0.9), KEYS, [0.1, 0.1, 0.1, 0.1]) == "people"  # a group
    assert cats.pick(probs(selfies=0.9), KEYS, [0.1, 0.1, 0.1]) == "people"
    assert cats.pick(probs(selfies=0.9), KEYS, []) == "people"               # no face found: not a selfie
    flat = np.full(len(KEYS), 1 / len(KEYS))
    assert cats.pick(flat, KEYS, []) == "other"                              # nothing fits well
    assert cats.pick(flat, KEYS, [0.3]) == "selfies"                         # ... but a face is a person


def test_every_category_has_a_label_and_the_prompt_matrix_is_unit_length():
    keys, m = cats.prompt_matrix()
    assert keys == KEYS and "other" not in keys
    assert np.allclose(np.linalg.norm(m, axis=1), 1, atol=1e-4)


@pytest.fixture(scope="module")
def labelled(mlworld):
    r = subprocess.run([PY, "categories.py", "run"], cwd=ROOT, env={**mlworld["env"], "CATEGORIES_PACE": "0"},
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def names(c, key):
    return sorted(i["name"] for i in c.get("/api/items", params={"filter": f"cat:{key}"}).json()["items"])


def test_labels_every_photo_from_its_fingerprint(labelled, mlworld):
    assert "'labelled': 14" in labelled
    with httpx.Client(base_url=mlworld["url"], timeout=30) as c:
        found = {x["key"]: x["count"] for x in c.get("/api/categories").json()["categories"]}
        assert sum(found.values()) == 14
        assert "red.jpg" in names(c, "food") and "green.jpg" in names(c, "nature") and "blue.jpg" in names(c, "travel")
        assert len(names(c, "food")) == found["food"]
        assert c.get("/api/items", params={"filter": "cat:food,cat:nature"}).json()["items"] == []   # one category each


def test_second_run_only_labels_new_photos(labelled, mlworld):
    r = subprocess.run([PY, "categories.py", "run"], cwd=ROOT, env=mlworld["env"], capture_output=True, text=True)
    assert "0 photos to label" in r.stdout


def test_nothing_is_sent_anywhere():
    """The sorter never touches the network: no http client, no subprocess to an outside service."""
    src = (ROOT / "categories.py").read_text().lower()
    for word in ("urllib", "requests", "httpx", "http.client", "socket"):
        assert word not in src, word


def test_filter_menu_lists_categories(labelled, mlworld, page):
    page.goto(mlworld["url"] + "/#/photos")
    page.click("#filterBtn")
    assert page.locator("#filterMenu .chip", has_text="Food").count() == 1
    page.click("#filterMenu .chip:has-text('Food')")
    page.wait_for_selector("#days .tile")
    with httpx.Client(base_url=mlworld["url"]) as c:
        assert page.locator("#days .tile").count() == len(names(c, "food"))


def test_status_reports_readiness(mlworld):
    with httpx.Client(base_url=mlworld["url"], timeout=30) as c:
        s = c.get("/api/categories/status").json()
        assert s["photos"] == 14 and s["ready"] and s["unanalysed"] == 0 and s["sample"] == 100 and "job" in s
        c.post("/api/categories/stop")      # nothing running: harmless


# ---- the buttons, end to end, against a server that sorts a few photos at a time
@pytest.fixture(scope="module")
def sortworld(tmp_path_factory):
    from conftest import build_ml_drive, make_world, run, serve, stop
    base = tmp_path_factory.mktemp("sort")
    w = make_world(base, build_ml_drive, CATEGORIES_CHUNK="2", CATEGORIES_PACE="1.5")
    run(PY, "ml.py", "run", cwd=ROOT, env=w["env"])
    serve(w)
    yield w
    stop(w)


def sorted_count(page):
    return int(re.search(r"([\d,]+) of ([\d,]+) photos sorted", page.inner_text("#catBox")).group(1).replace(",", ""))


def test_buttons_start_stop_and_sort(sortworld, page):
    w = sortworld
    page.goto(w["url"] + "/#/categories")
    page.wait_for_selector("#catTry")
    assert "0 of 14 photos sorted" in page.inner_text("#catBox")
    assert page.locator("#catTry").inner_text() == "Sort 14 at once"     # fewer photos than the sample size
    # start, watch the progress bar, stop part-way: what was sorted is kept
    page.click("#catTry")
    page.wait_for_selector(".cat-prog")
    page.wait_for_function("document.querySelector('.cat-prog')?.innerText.includes('of 14')", timeout=20000)
    page.click("#catStop")
    page.wait_for_selector("#catAll")
    part = sorted_count(page)
    assert 0 < part < 14, part
    # finish the rest: the page updates without a reload
    page.click("#catAll")
    page.wait_for_function("!document.querySelector('#catStop') && document.querySelector('#catBox').innerText.startsWith('14 of 14')", timeout=60000)
    page.wait_for_function("document.querySelector('#catFly').children.length === 0 && [...document.querySelectorAll('.cat-card .cnt')].reduce((n, e) => n + +e.textContent, 0) === 14", timeout=30000)
    shown = {c.query_selector(".top b").inner_text(): int(c.query_selector(".cnt").inner_text()) for c in page.query_selector_all(".cat-card")}
    with httpx.Client(base_url=w["url"]) as c:
        api = {x["label"]: x["count"] for x in c.get("/api/categories").json()["categories"]}
    assert sum(api.values()) == 14 and all(shown[k] == v for k, v in api.items())   # the cards say what the database says
    assert "Food" in api and "Nature" in api and "Travel" in api
    page.screenshot(path=str(SHOTS / "sort-photos.png"))
    # the filter menu now offers the categories, and "Start over" forgets them
    page.goto(w["url"] + "/#/photos")
    page.click("#filterBtn")
    page.wait_for_selector("#filterMenu .chip:has-text('Nature')", state="attached", timeout=5000)
    page.goto(w["url"] + "/#/categories")
    page.once("dialog", lambda d: d.accept())
    page.click("#catClear")
    page.wait_for_selector("#catTry")
    assert "0 of 14 photos sorted" in page.inner_text("#catBox")


def test_page_says_it_is_private(sortworld, page):
    page.goto(sortworld["url"] + "/#/categories")
    page.wait_for_selector("#catBox .cat-stats")
    text = page.inner_text("#page")
    assert "runs entirely on this Mac" in text and "sent anywhere" in text
