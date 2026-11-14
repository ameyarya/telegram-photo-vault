"""Photo categories (Food, Selfies, Travel...) chosen on this Mac by the same CLIP model that powers smart search.

Nothing is sent anywhere. Each photo's CLIP fingerprint (made by "Analyse photos", see ml.py) is compared with a
few descriptions of each category ("a meal on a restaurant table", "a sunset sky"...), and the faces the gallery
already found decide Selfies vs People. Needs smart search set up (./vault smart). Nothing runs unless you start it:

    ../.venv/bin/python categories.py run [--limit 300]      label photos that have no category yet
    ../.venv/bin/python categories.py status

Labels live in the `categories` table; the gallery's filter menu then offers each category that has photos.
"""
import json
import os
import signal
import subprocess
import sys
import time

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import ml
from config import BASE
from db import connect

router = APIRouter()

# key -> (menu label, how the model is told what it looks like: several short descriptions, averaged)
CATEGORIES = {
    "selfies": ("Selfies", ["a selfie", "a close-up portrait of a person looking at the camera"]),
    "people": ("People", ["a group of people posing together", "friends and family at an event", "a candid photo of people"]),
    "food": ("Food", ["food on a plate", "a meal on a restaurant table", "a drink in a glass", "a dessert"]),
    "travel": ("Travel", ["a famous landmark", "a city street with buildings", "a hotel or resort", "tourists sightseeing"]),
    "nature": ("Nature", ["a landscape", "a sunset over the sky", "mountains and trees", "a beach and the sea", "flowers and plants"]),
    "pets": ("Pets & animals", ["a pet dog", "a cat", "a wild animal", "a bird"]),
    "documents": ("Documents", ["a photo of a document or paper", "a receipt or a ticket", "a whiteboard or a page of text"]),
    "screens": ("Screens & memes", ["a screenshot of a phone screen", "a meme with text", "a digital graphic or an illustration"]),
    "other": ("Other", []),   # no description: it is what is left when nothing fits well
}
LOGIT_SCALE = 100         # CLIP's own scale: turns similarities into confidences
MIN_CONFIDENCE = 0.30     # below this the photo fits none of the categories well: "Other"
FACE_SCORE = ml.MIN_FACE_SCORE
FACE_MIN_WIDTH = 0.05     # smaller faces are background people
SELFIE_FACE = 0.25        # one or two faces, one at least this wide (share of the photo): a selfie
CHUNK = int(os.environ.get("CATEGORIES_CHUNK", 200))     # photos labelled per step
PACE = float(os.environ.get("CATEGORIES_PACE", 0.25))    # seconds between steps, so the page can show them landing and Stop has time to work
SAMPLE = 100              # "Try it": how many photos


def ensure_table(con):  # (db.py makes it too; this covers a script run on an old index)
    con.execute("CREATE TABLE IF NOT EXISTS categories (seq INTEGER PRIMARY KEY AUTOINCREMENT, media_id INTEGER NOT NULL UNIQUE, cat TEXT NOT NULL)")
    con.execute("CREATE INDEX IF NOT EXISTS categories_cat ON categories(cat)")


def prompt_matrix():
    """(keys, one unit vector per category) from the descriptions above."""
    keys = [k for k, (_, d) in CATEGORIES.items() if d]
    if ml.FAKE:   # the stand-in model only knows colours: red is Food, green Nature, blue Travel
        fake = {"food": (1, 0, 0), "nature": (0, 1, 0), "travel": (0, 0, 1)}
        return keys, ml._norm([[*fake.get(k, (0, 0, 0)), 0.02] for k in keys])
    clip = ml.Clip()
    return keys, ml._norm([ml._norm([clip.text(d, template="{}") for d in CATEGORIES[k][1]]).mean(axis=0) for k in keys])


def pick(probs, keys, faces):
    """The category for one photo. probs: confidence per key; faces: widths of the faces found in it (share of the photo)."""
    top = keys[int(np.argmax(probs))]
    if probs.max() < MIN_CONFIDENCE:
        top = "other"
    if faces and top in ("selfies", "people", "other"):
        return "selfies" if len(faces) <= 2 and max(faces) >= SELFIE_FACE else "people"
    if not faces and top == "selfies":
        return "people"   # no face was found: not a selfie, but the model still thinks it shows a person
    return top


def classify(embs, keys, matrix, faces_by_row):
    sims = np.asarray(embs, dtype=np.float32) @ matrix.T * LOGIT_SCALE
    e = np.exp(sims - sims.max(axis=1, keepdims=True))
    probs = e / e.sum(axis=1, keepdims=True)
    return [pick(p, keys, f) for p, f in zip(probs, faces_by_row)]


# photos that can be sorted: real photos (not videos, trash or Live Photo clips) that the model has analysed
BASE_ELIGIBLE = "kind = 'image' AND live_of IS NULL AND thumb != -1 AND id NOT IN (SELECT media_id FROM trash)"
ELIGIBLE = BASE_ELIGIBLE + " AND id IN (SELECT media_id FROM clip)"


def _job(con, job_id, **fields):
    con.execute(f"UPDATE jobs SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", [*fields.values(), job_id])
    con.commit()


def label_chunk(con, ids, keys, matrix):
    """[(media_id, category)] for these photos."""
    marks = ",".join("?" * len(ids))
    emb = {r["media_id"]: r["emb"] for r in con.execute(f"SELECT media_id, emb FROM clip WHERE media_id IN ({marks})", ids)}
    ids = [i for i in ids if i in emb]
    faces = {i: [] for i in ids}
    for r in con.execute(f"SELECT media_id, w FROM faces WHERE score >= ? AND w >= ? AND media_id IN ({marks})",
                         [FACE_SCORE, FACE_MIN_WIDTH, *ids]):
        faces[r["media_id"]].append(r["w"])
    vectors = ml._norm(np.stack([np.frombuffer(emb[i], dtype=np.float16).astype(np.float32) for i in ids]))
    return list(zip(ids, classify(vectors, keys, matrix, [faces[i] for i in ids])))


def run(limit=None, log=print):
    con = connect(timeout=600)
    ensure_table(con)
    job = con.execute("INSERT INTO jobs(kind, status, detail) VALUES ('categories', 'running', ?)",
                      (json.dumps({"pid": os.getpid()}),)).lastrowid
    con.commit()
    detail = {"pid": os.getpid()}
    try:
        todo = [r["id"] for r in con.execute(
            f"SELECT id FROM media WHERE {ELIGIBLE} AND id NOT IN (SELECT media_id FROM categories) ORDER BY taken_at DESC")]
        if limit:
            todo = todo[:limit]
        log(f"{len(todo)} photos to label")
        _job(con, job, total=len(todo), done=0)
        keys, matrix = prompt_matrix() if todo else ([], None)
        done = 0
        for i in range(0, len(todo), CHUNK):
            rows = label_chunk(con, todo[i:i + CHUNK], keys, matrix)
            con.executemany("INSERT OR REPLACE INTO categories(media_id, cat) VALUES (?, ?)", rows)
            con.commit()
            done += len(rows)
            _job(con, job, done=done)
            log(f"labelled {done}/{len(todo)}")
            if PACE and i + CHUNK < len(todo):
                time.sleep(PACE)
        _job(con, job, status="done", detail=json.dumps(detail), finished=time.strftime("%Y-%m-%dT%H:%M:%S"))
        return {"labelled": done}
    except BaseException as e:
        _job(con, job, status="failed", detail=json.dumps({**detail, "error": str(e) or type(e).__name__}),
             finished=time.strftime("%Y-%m-%dT%H:%M:%S"))
        raise


def counts(con):
    ensure_table(con)
    return {r["cat"]: r["n"] for r in con.execute("SELECT cat, count(*) n FROM categories GROUP BY cat")}


@router.get("/api/categories")
def list_categories():
    """The categories that have photos, with counts, in menu order."""
    con = connect()
    have = counts(con)
    return {"categories": [{"key": k, "label": CATEGORIES[k][0], "count": have[k]} for k in CATEGORIES if have.get(k)],
            "labelled": sum(have.values())}


def _running():
    r = connect().execute("SELECT * FROM jobs WHERE kind = 'categories' ORDER BY id DESC LIMIT 1").fetchone()
    if not r:
        return None
    job = {**dict(r), "detail": json.loads(r["detail"] or "{}")}
    if job["status"] == "running":
        try:
            os.kill(job["detail"].get("pid", 0), 0)
        except OSError:
            job["status"] = "stopped"
    return job


@router.get("/api/categories/status")
def categories_status():
    con = connect()
    have = counts(con)
    eligible = con.execute(f"SELECT count(*) FROM media WHERE {ELIGIBLE}").fetchone()[0]
    unanalysed = con.execute(f"SELECT count(*) FROM media WHERE {BASE_ELIGIBLE} AND id NOT IN (SELECT media_id FROM clip)").fetchone()[0]
    latest = {k: [r[0] for r in con.execute("SELECT media_id FROM categories WHERE cat = ? ORDER BY seq DESC LIMIT 6", (k,))]
              for k in CATEGORIES}
    last = con.execute("SELECT coalesce(max(seq), 0) FROM categories").fetchone()[0]
    return {"categories": [{"key": k, "label": CATEGORIES[k][0], "count": have.get(k, 0), "latest": latest[k]} for k in CATEGORIES],
            "last": last, "labelled": sum(have.values()), "unsorted": max(0, eligible - sum(have.values())), "photos": eligible + unanalysed,
            "ready": ml.models_ready() and eligible > 0, "unanalysed": unanalysed, "job": _running(), "sample": SAMPLE}


@router.get("/api/categories/sphere")
def categories_sphere(n: int = 150):
    """Photos for the sphere on the Sorting page: random ones still waiting (else any), as media ids."""
    con = connect()
    n = max(1, min(n, 400))
    ids = [r[0] for r in con.execute(
        f"SELECT id FROM media WHERE {BASE_ELIGIBLE} AND id NOT IN (SELECT media_id FROM categories) ORDER BY random() LIMIT ?", (n,))]
    if len(ids) < n:
        more = con.execute(f"SELECT id FROM media WHERE {BASE_ELIGIBLE} ORDER BY random() LIMIT ?", (n,))
        ids += [r[0] for r in more if r[0] not in set(ids)][:n - len(ids)]
    return {"ids": ids}


@router.get("/api/categories/recent")
def categories_recent(since: int = 0, limit: int = 400):
    """Photos labelled after label number `since` (so the page can fly them into their folders as they are sorted)."""
    rows = connect().execute("SELECT seq, media_id, cat FROM categories WHERE seq > ? ORDER BY seq LIMIT ?",
                             (since, min(limit, 2000))).fetchall()
    return {"items": [{"id": r[1], "cat": r[2]} for r in rows], "last": rows[-1][0] if rows else since}


class RunBody(BaseModel):
    limit: int | None = None


@router.post("/api/categories/run")
def categories_run(body: RunBody):
    job = _running()
    if job and job["status"] == "running":
        return {"started": False, "job": job}
    if not ml.models_ready():
        raise HTTPException(400, "Sorting uses the smart-search model, which isn't set up yet. Run ./vault smart first.")
    cmd = [sys.executable, str(BASE / "categories.py"), "run"] + (["--limit", str(max(1, body.limit))] if body.limit else [])
    subprocess.Popen(cmd, cwd=BASE, stdout=open(BASE / "categories.log", "a"), stderr=subprocess.STDOUT, start_new_session=True)
    return {"started": True}


@router.post("/api/categories/stop")
def categories_stop():
    job = _running()
    if job and job["status"] == "running":
        try:
            os.killpg(job["detail"]["pid"], signal.SIGTERM)  
        except OSError:
            pass
        con = connect()
        con.execute("UPDATE jobs SET status = 'stopped', finished = datetime('now') WHERE id = ?", (job["id"],))
        con.commit()
    return {"stopped": True}


@router.post("/api/categories/clear")
def categories_clear():
    """Forget every label (the next run sorts everything again)."""
    con = connect()
    con.execute("DELETE FROM categories")
    con.commit()
    return {"cleared": True}


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["status"]:
        print(counts(connect()))
    elif args[:1] == ["run"]:
        lim = int(args[args.index("--limit") + 1]) if "--limit" in args else None
        print(run(lim))
    else:
        print(__doc__)
