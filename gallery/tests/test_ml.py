"""Smart search, people and similar shots (fake models: colour = meaning, file name = face)."""
import os
import sqlite3
import subprocess

import httpx
import pytest

from conftest import PY, ROOT


@pytest.fixture(scope="module")
def mapi(mlworld):
    with httpx.Client(base_url=mlworld["url"], timeout=60) as c:
        yield c


@pytest.fixture(scope="module")
def mitems(mapi):
    return {i["name"]: i for i in mapi.get("/api/items").json()["items"]}


def test_analysis_covers_every_photo(mapi):
    s = mapi.get("/api/ml/status").json()
    assert s["models"] is True and s["images"] == 14 and s["understood"] == 14
    assert s["faces"] == 7 and s["people"] == 2 and s["similar_groups"] == 1
    assert s["job"]["status"] == "done"


@pytest.mark.parametrize("q, first", [("red", "red.jpg"), ("something green", "green.jpg"), ("BLUE sky", "blue.jpg")])
def test_smart_search_ranks_by_meaning(mapi, q, first):
    r = mapi.get("/api/smart-search", params={"q": q}).json()
    assert r["ready"] is True
    assert r["items"][0]["name"] == first
    scores = [i["score"] for i in r["items"]]
    assert scores == sorted(scores, reverse=True)


def test_smart_search_only_returns_close_matches(mapi):
    names = [i["name"] for i in mapi.get("/api/smart-search", params={"q": "red"}).json()["items"]]
    assert "blue.jpg" not in names and "green.jpg" not in names


def test_smart_search_respects_folder_and_empty_query(mapi):
    r = mapi.get("/api/smart-search", params={"q": "red", "album": "people"}).json()
    assert all(i["album"] == "people" for i in r["items"])
    assert mapi.get("/api/smart-search", params={"q": "  "}).json()["items"] == []


def test_smart_search_not_ready_before_analysis(api):
    assert api.get("/api/smart-search", params={"q": "red"}).json() == {"items": [], "ready": False}


def test_people_grouped_by_face(mapi):
    people = mapi.get("/api/people").json()
    assert sorted(p["n"] for p in people) == [3, 3]          # alice ×3, bob ×3; carol (1 face) isn't a person yet
    for p in people:
        names = sorted(i["name"] for i in mapi.get("/api/items", params={"person": p["id"]}).json()["items"])
        assert len({n.split("_")[1] for n in names}) == 1     # one person per group
    r = mapi.get(f"/face/{people[0]['cover_face']}")
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"
    assert mapi.get("/face/999999").status_code == 404


def test_naming_survives_regrouping_and_merge(mapi, mlworld):
    people = mapi.get("/api/people").json()
    alice = next(p for p in people if "alice" in mapi.get("/api/items", params={"person": p["id"]}).json()["items"][0]["name"])
    assert mapi.post(f"/api/people/{alice['id']}/name", json={"name": "Alice"}).status_code == 200
    subprocess.run([PY, "ml.py", "run", "people"], cwd=ROOT, env=mlworld["env"], check=True, capture_output=True)
    after = {p["id"]: p["name"] for p in mapi.get("/api/people").json()}
    assert after[alice["id"]] == "Alice"                         # same id, name kept
    info = mapi.get(f"/api/item/{mapi.get('/api/items', params={'person': alice['id']}).json()['items'][0]['id']}").json()
    assert {"id": alice["id"], "name": "Alice"} in info["people"]
    bob = next(i for i in after if i != alice["id"])
    assert mapi.post(f"/api/people/{bob}/merge", json={"into": bob}).status_code == 400
    assert mapi.post(f"/api/people/{bob}/merge", json={"into": alice["id"]}).status_code == 200
    merged = mapi.get("/api/people").json()
    assert [(p["name"], p["n"]) for p in merged] == [("Alice", 6)]
    assert mapi.post("/api/people/999999/name", json={"name": "x"}).status_code == 404
    # put things back for other tests
    subprocess.run([PY, "-c", "import db; c = db.connect(); c.execute('UPDATE faces SET person = NULL'); "
                    "c.execute('DELETE FROM people'); c.commit()"], cwd=ROOT, env=mlworld["env"], check=True)
    subprocess.run([PY, "ml.py", "run", "people"], cwd=ROOT, env=mlworld["env"], check=True, capture_output=True)


def test_similar_groups_bursts_only(mapi):
    r = mapi.get("/api/similar").json()
    assert r["total"] == 1
    assert sorted(i["name"] for i in r["groups"][0]["items"]) == ["shot_0.jpg", "shot_1.jpg", "shot_2.jpg"]
    # same colour, but taken hours later: not part of the burst


def test_analysis_is_incremental(mlworld):
    """Running again only processes new photos (nothing new here), and doesn't duplicate faces."""
    subprocess.run([PY, "ml.py", "run"], cwd=ROOT, env=mlworld["env"], check=True, capture_output=True)
    con = sqlite3.connect(mlworld["data"] / "index.db")
    assert con.execute("SELECT count(*) FROM faces").fetchone()[0] == 7
    assert con.execute("SELECT count(*) FROM clip").fetchone()[0] == 14


def test_ml_run_endpoint_refuses_when_running(mapi):
    r = mapi.post("/api/ml/run").json()
    assert "started" in r


@pytest.mark.skipif(not (ROOT / "models" / "yunet.onnx").exists(), reason="real models not downloaded")
def test_real_models_smoke(tmp_path):
    """With the real models: CLIP ranks a red picture first for 'red', and YuNet loads and runs."""
    env = {**os.environ, "GALLERY_ML_FAKE": "0", "GALLERY_MODELS": str(ROOT / "models")}
    code = """
import numpy as np
from PIL import Image
import ml
clip = ml.Clip()
imgs = [Image.new("RGB", (224, 224), c) for c in ("red", "green", "blue")]
e = clip.images(imgs)
s = e @ clip.text("the colour red")
assert int(np.argmax(s)) == 0, s
import sys
Image.new("RGB", (320, 240), "white").save(sys.argv[1])
assert ml.Faces().find(sys.argv[1]) == []
print("ok")
"""
    r = subprocess.run([PY, "-c", code, str(tmp_path / "blank.jpg")], cwd=ROOT, env=env, capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip().endswith("ok"), r.stderr[-2000:]
