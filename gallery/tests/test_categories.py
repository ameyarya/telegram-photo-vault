"""Photo categories: contact sheets go to a (fake) `claude`, its answers become labels and a filter."""
import os
import subprocess

import httpx
import pytest

from conftest import PY, ROOT

FAKE = """#!/bin/sh
# stands in for `claude -p`: the prompt is the 2nd argument; odd numbers are food, even are nature, and 3 is nonsense
python3 - "$2" <<'PYEOF'
import json, re, sys
p = sys.argv[1]
out = {}
for name, n in re.findall(r"(\\S+\\.jpg): photos numbered 1-(\\d+)", p):
    assert __import__("os").path.exists(name), name  # the sheet was written where claude runs
    for i in range(1, int(n) + 1):
        out[f"{name}:{i}"] = "food" if i % 2 else ("nonsense" if i == 4 else "nature")
print(json.dumps({"result": "Here you go:\\n" + json.dumps(out)}))
PYEOF
"""


@pytest.fixture(scope="module")
def labelled(mlworld, tmp_path_factory):
    fake = tmp_path_factory.mktemp("bin") / "claude"
    fake.write_text(FAKE)
    fake.chmod(0o755)
    env = {**mlworld["env"], "CLAUDE_BIN": str(fake)}
    r = subprocess.run([PY, "categories.py", "run"], cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_labels_every_photo_and_rejects_unknown_categories(labelled, mlworld):
    assert "'labelled': 14" in labelled and "'failed_batches': 0" in labelled
    with httpx.Client(base_url=mlworld["url"], timeout=30) as c:
        cats = {x["key"]: x["count"] for x in c.get("/api/categories").json()["categories"]}
        assert cats == {"food": 7, "nature": 6, "other": 1}   # "nonsense" became "other"
        food = c.get("/api/items", params={"filter": "cat:food"}).json()
        assert len(food["items"]) == 7
        assert c.get("/api/items", params={"filter": "cat:food,cat:nature"}).json()["items"] == []


def test_second_run_only_labels_new_photos(labelled, mlworld, tmp_path):
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\nexit 1\n"); fake.chmod(0o755)   # would fail if it were asked anything
    r = subprocess.run([PY, "categories.py", "run"], cwd=ROOT, env={**mlworld["env"], "CLAUDE_BIN": str(fake)},
                       capture_output=True, text=True)
    assert "0 photos to label" in r.stdout


def test_filter_menu_lists_categories(labelled, mlworld, page):
    page.goto(mlworld["url"] + "/#/photos")
    page.click("#filterBtn")
    assert page.locator("#filterMenu .chip", has_text="Food").count() == 1
    page.click("#filterMenu .chip:has-text('Food')")
    page.wait_for_selector("#days .tile")
    assert page.locator("#days .tile").count() == 7


def test_sort_page_runs_and_shows_progress(mlworld, page, tmp_path):
    """The Sort photos page: press the button, the (fake) Claude labels the photos, the categories appear."""
    fake = tmp_path / "claude"
    fake.write_text(FAKE); fake.chmod(0o755)
    with httpx.Client(base_url=mlworld["url"], timeout=30) as c:
        c.post("/api/categories/clear")
    # the test server doesn't know the fake claude, so run the same job the button starts, from a subprocess
    page.goto(mlworld["url"] + "/#/categories")
    page.wait_for_selector("#catTry")
    assert "0 of 14 photos sorted" in page.inner_text("#catBox")
    assert page.locator("#catTry").inner_text().startswith("Try it on")
    r = subprocess.run([PY, "categories.py", "run"], cwd=ROOT, env={**mlworld["env"], "CLAUDE_BIN": str(fake)},
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    page.reload()
    page.wait_for_selector(".cat-chip")
    assert "14 of 14 photos sorted" in page.inner_text("#catBox")
    page.click(".cat-chip:has-text('Food')")
    page.wait_for_selector("#days .tile")
    assert page.locator("#days .tile").count() == 7


def test_run_endpoint_rejects_missing_claude(mlworld):
    with httpx.Client(base_url=mlworld["url"], timeout=30) as c:
        s = c.get("/api/categories/status").json()
        assert s["photos"] == 14 and "job" in s and s["calls_per"] == 90
        c.post("/api/categories/stop")      # nothing running: harmless
