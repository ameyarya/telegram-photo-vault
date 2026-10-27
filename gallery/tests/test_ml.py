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


# ---------- face clustering itself (synthetic face vectors) ----------
def _people(rng, n_people, faces_each, noise=0.06, dim=128):
    import ml, numpy as np
    centres = [ml._norm(rng.normal(size=dim)) for _ in range(n_people)]
    return centres, [ml._norm(c + rng.normal(scale=noise, size=dim)) for c in centres for _ in range(faces_each)]


def test_clustering_separates_people_and_keeps_each_together():
    import ml, numpy as np
    rng = np.random.default_rng(1)
    _, faces = _people(rng, 6, 40)
    lab = ml.cluster_faces(np.stack(faces).astype(np.float32))
    per_person = [set(lab[i * 40:(i + 1) * 40]) for i in range(6)]
    assert all(len(s) == 1 for s in per_person)              # nobody is split
    assert len({next(iter(s)) for s in per_person}) == 6      # nobody is merged


def test_clustering_resists_generic_faces_and_chains():
    """Regression: linking faces directly chained almost the whole real library into one person."""
    import ml, numpy as np
    rng = np.random.default_rng(2)
    centres, faces = _people(rng, 3, 30)
    hub = ml._norm(sum(centres))                              # looks a bit like everyone
    a, b = ml._norm(rng.normal(size=128)), ml._norm(rng.normal(size=128))
    b = ml._norm(b - (b @ a) * a)
    chain = [ml._norm(np.cos(t) * a + np.sin(t) * b) for t in np.linspace(0, np.pi, 60)]  # drifts to its opposite
    lab = ml.cluster_faces(np.stack(faces + [hub] * 8 + chain).astype(np.float32))
    assert len({lab[0], lab[30], lab[60]}) == 3               # the generic faces didn't bridge the people
    assert lab[98] == -1 or lab[98] != lab[-1]                # the two ends of the chain aren't one person


def test_clustering_big_person_isnt_split_into_fragments():
    """The other failure: mutual neighbours alone split one person with many photos into dozens of groups."""
    import ml, numpy as np
    rng = np.random.default_rng(3)
    _, faces = _people(rng, 2, 400, noise=0.08)
    lab = ml.cluster_faces(np.stack(faces).astype(np.float32))
    assert len(set(lab[:400]) - {-1}) == 1 and len(set(lab[400:]) - {-1}) == 1
    assert (lab >= 0).mean() > 0.95


def test_clustering_edge_cases():
    import ml, numpy as np
    assert len(ml.cluster_faces(np.zeros((0, 128), np.float32))) == 0
    assert list(ml.cluster_faces(ml._norm(np.ones((1, 128))).astype(np.float32))) == [-1]


def test_look_alike_chains_dont_merge_different_people():
    """Regression: merging every similar pair at once chained A~B, B~C ... into one 'person' holding a quarter of a
    real library's faces. Merging the closest pair first and recomputing keeps A and C apart."""
    import numpy as np
    import ml
    rng = np.random.default_rng(0)
    def person(v, n=4):
        return [ml._norm(v + rng.normal(0, 0.02, v.shape)) for _ in range(n)]
    a = ml._norm(np.r_[1.0, 0.0, 0.0, np.zeros(61)])
    c = ml._norm(np.r_[0.2, 0.98, 0.0, np.zeros(61)])           # a and c: clearly different people
    b = ml._norm(a + c)                                          # b looks a bit like each
    groups = [list(range(0, 4)), list(range(4, 8)), list(range(8, 12))]
    embs = np.stack(person(a) + person(b) + person(c)).astype(np.float32)
    merged = ml.merge_groups(embs, groups, 0.70)
    assert len(merged) == 2                                      # b joins one of them, never all three in one
    assert not any(set(range(0, 4)) <= set(m) and set(range(8, 12)) <= set(m) for m in merged)


def test_search_understands_names_of_people_and_taught_things(mapi, mlworld):
    """"alice on red" = Alice's photos, ranked by 'red'; a pet/thing taught from example photos is found by its look."""
    people = mapi.get("/api/people").json()
    alice = next(p for p in people if "alice" in mapi.get("/api/items", params={"person": p["id"]}).json()["items"][0]["name"])
    mapi.post(f"/api/people/{alice['id']}/name", json={"name": "Alice Smith"})
    r = mapi.get("/api/smart-search", params={"q": "alice"}).json()                  # first name, any case
    assert r["understood"] == {"people": ["Alice Smith"], "subjects": [], "text": ""}
    assert sorted(i["name"] for i in r["items"]) == ["face_alice_0.jpg", "face_alice_1.jpg", "face_alice_2.jpg"]
    r = mapi.get("/api/smart-search", params={"q": "photos of Alice's face, with bob"}).json()
    assert r["understood"]["people"] == ["Alice Smith"] and "bob" in r["understood"]["text"]
    red = next(i for i in mapi.get("/api/items", params={"q": "red.jpg"}).json()["items"] if i["name"] == "red.jpg")
    t = mapi.post("/api/subjects", json={"name": "Rover", "ids": [red["id"]]}).json()
    assert t == {"name": "Rover", "examples": 1, "matches": 1}
    r = mapi.get("/api/smart-search", params={"q": "rover"}).json()
    assert r["understood"]["subjects"] == ["Rover"] and [i["name"] for i in r["items"]] == ["red.jpg"]
    assert mapi.get("/api/smart-search", params={"q": "rover and alice"}).json()["items"] == []   # never together
    subs = mapi.get("/api/subjects").json()
    assert [s["name"] for s in subs] == ["Rover"] and subs[0]["cover"] == red["id"]
    assert mapi.post("/api/subjects", json={"name": " ", "ids": [1]}).status_code == 400
    mapi.delete(f"/api/subjects/{subs[0]['id']}")
    assert mapi.get("/api/smart-search", params={"q": "rover"}).json().get("understood") is None   # plain search again
    mapi.post(f"/api/people/{alice['id']}/name", json={"name": ""})
