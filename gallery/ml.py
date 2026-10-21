"""Smart search, people and similar photos — all computed on this Mac from the local thumbnails.

    ../.venv/bin/python ml.py download  # fetch the models once (~650 MB: OpenCLIP ViT-B/32 + OpenCV YuNet/SFace)
    ../.venv/bin/python ml.py run       # embed photos, find faces, group people and similar shots (resumable)

Models:
  * OpenCLIP ViT-B/32 (LAION-2B) turns photos and search text into comparable vectors: "beach", "cake", "red dress".
  * OpenCV YuNet finds faces; OpenCV SFace describes each face so the same person's faces can be grouped.
GALLERY_ML_FAKE=1 swaps both for tiny deterministic stand-ins (used by the tests).
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from build_index import make_thumb, thumb_path
from config import BASE, CACHE, DRIVE, MODELS, local_path
from db import connect
from search import selection

router = APIRouter()
FAKE = os.environ.get("GALLERY_ML_FAKE") == "1"
CLIP_ARCH, CLIP_WEIGHTS = "ViT-B-32", "laion2b_s34b_b79k"
FACE_FILES = {
    "yunet.onnx": "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "sface.onnx": "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
}
FACE_NEIGHBOURS = 20      # each face links only to faces that are among each other's 20 most similar ...
FACE_LINK = 0.45          # ... and at least this similar (SFace cosine)
PEOPLE_MERGE = 0.70       # tight groups whose average faces are this similar are the same person
MIN_FACE_FRACTION = 0.06  # faces narrower than this share of the photo are too small to identify reliably
MIN_FACE_SCORE = 0.85     # detector confidence needed to use a face for grouping
MIN_FACES_PER_PERSON = 3  # smaller groups aren't shown as people
MAYBE_SAME = 0.55         # suggested as "maybe the same person?" (on a real library: mostly right above 0.55,
                          # about half wrong at 0.50, mostly different people at 0.45)
SIMILAR_COS = 0.93        # CLIP similarity for "near-identical shot"
SIMILAR_WINDOW_S = 120    # ... taken within two minutes of each other


# ---------------------------------------------------------------- models
def _norm(v):
    v = np.asarray(v, dtype=np.float32)
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(n, 1e-8)


class Clip:
    """Image and text embeddings in the same space."""

    def __init__(self):
        if FAKE:
            self.dim = 4
            return
        import open_clip
        import torch
        self.torch = torch
        self.device = "mps" if torch.backends.mps.is_available() else "cpu"
        self.model, _, self.preprocess = open_clip.create_model_and_transforms(
            CLIP_ARCH, pretrained=CLIP_WEIGHTS, cache_dir=str(MODELS / "clip"), device=self.device)
        self.model.eval()
        self.tokenizer = open_clip.get_tokenizer(CLIP_ARCH)
        self.dim = 512

    def images(self, pil_images):
        if FAKE:  # colour stand-in: the photo's average red, green and blue
            out = []
            for im in pil_images:
                a = np.asarray(im.convert("RGB").resize((32, 32)), dtype=np.float32) / 255
                out.append([a[..., 0].mean(), a[..., 1].mean(), a[..., 2].mean(), 0.02])
            return _norm(out)
        from PIL import ImageOps
        with self.torch.no_grad():  # upright first (sideways-stored photos), like the gallery shows them
            batch = self.torch.stack([self.preprocess(ImageOps.exif_transpose(im).convert("RGB"))
                                      for im in pil_images]).to(self.device)
            return _norm(self.model.encode_image(batch).float().cpu().numpy())

    def text(self, q):
        if FAKE:
            words = q.lower()
            return _norm([("red" in words) * 1.0, ("green" in words) * 1.0, ("blue" in words) * 1.0, 0.02])
        with self.torch.no_grad():
            tokens = self.tokenizer([f"a photo of {q}"]).to(self.device)
            return _norm(self.model.encode_text(tokens).float().cpu().numpy())[0]


class Faces:
    """Find faces (box as fractions of the image) and a vector per face."""

    def __init__(self):
        if FAKE:
            return
        import cv2
        self.cv2 = cv2
        self.detector = cv2.FaceDetectorYN.create(str(MODELS / "yunet.onnx"), "", (320, 320), 0.8, 0.3, 500)
        self.recognizer = cv2.FaceRecognizerSF.create(str(MODELS / "sface.onnx"), "")

    def find(self, image_path, name=""):
        if FAKE:  # files named ..._face_<who>_... have one face of <who>
            import hashlib
            parts = name.lower().split("face_")
            if len(parts) < 2:
                return []
            who = parts[1].split("_")[0].split(".")[0]
            v = np.zeros(64, dtype=np.float32)  # one direction per person: different people never look alike
            v[int(hashlib.md5(who.encode()).hexdigest()[:8], 16) % 64] = 1
            return [((0.3, 0.2, 0.4, 0.5), 0.99, v)]
        img = self.cv2.imread(str(image_path))  # OpenCV applies the EXIF rotation itself
        if img is None:
            return []
        h, w = img.shape[:2]
        scale = 640 / max(h, w) if max(h, w) < 640 else 1.0  # small faces in 400px thumbnails: upscale first
        if scale != 1.0:
            img = self.cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=self.cv2.INTER_CUBIC)
            h, w = img.shape[:2]
        self.detector.setInputSize((w, h))
        _, found = self.detector.detect(img)
        out = []
        for f in (found if found is not None else []):
            x, y, fw, fh, score = float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[-1])
            if fw < 24 or fh < 24:
                continue  # too small to tell people apart
            aligned = self.recognizer.alignCrop(img, f)
            feat = self.recognizer.feature(aligned).flatten()
            out.append(((max(x, 0) / w, max(y, 0) / h, fw / w, fh / h), score, _norm(feat)))
        return out


def download_models():
    MODELS.mkdir(parents=True, exist_ok=True)
    for name, url in FACE_FILES.items():
        target = MODELS / name
        if not target.exists():
            print(f"downloading {name} ...", flush=True)
            urllib.request.urlretrieve(url, target.with_suffix(".part"))
            target.with_suffix(".part").rename(target)
    print("downloading OpenCLIP weights (first time only) ...", flush=True)
    Clip()
    print("models ready", flush=True)


def models_ready():
    if FAKE:
        return True
    return all((MODELS / n).exists() for n in FACE_FILES) and (MODELS / "clip").exists()


# ---------------------------------------------------------------- the background job
def _job(con, **fields):
    sets = ", ".join(f"{k} = ?" for k in fields)
    con.execute(f"UPDATE jobs SET {sets} WHERE id = ?", list(fields.values()) + [JOB_ID])
    con.commit()


JOB_ID = None


def _image_for(row):
    """The local thumbnail (made from the drive if missing)."""
    p = thumb_path(row["id"])
    if not p.exists() and local_path(row["path"]).is_file():
        make_thumb({"id": row["id"], "path": row["path"], "kind": row["kind"]})
    return p if p.exists() else None


def run(steps=("clip", "faces", "people", "similar")):
    global JOB_ID
    from PIL import Image
    con = connect(timeout=600)
    cur = con.execute("INSERT INTO jobs(kind, status, detail) VALUES ('ml', 'running', ?)",
                      (json.dumps({"steps": list(steps), "pid": os.getpid()}),))
    con.commit()
    JOB_ID = cur.lastrowid
    try:
        if "clip" in steps:
            todo = [dict(r) for r in con.execute(
                "SELECT id, path, kind FROM media WHERE id NOT IN (SELECT media_id FROM ml_done WHERE task = 'clip')")]
            _job(con, total=len(todo), done=0, detail=json.dumps({"step": "Understanding photos", "pid": os.getpid()}))
            clip = Clip() if todo else None
            for i in range(0, len(todo), 64):
                batch, imgs = [], []
                for r in todo[i:i + 64]:
                    p = _image_for(r)
                    if p:
                        try:
                            imgs.append(Image.open(p)); batch.append(r["id"])
                        except OSError:
                            pass
                if imgs:
                    embs = clip.images(imgs)
                    con.executemany("INSERT OR REPLACE INTO clip(media_id, emb) VALUES (?, ?)",
                                    [(m, e.astype(np.float16).tobytes()) for m, e in zip(batch, embs)])
                con.executemany("INSERT OR IGNORE INTO ml_done VALUES (?, 'clip')", [(r["id"],) for r in todo[i:i + 64]])
                _job(con, done=min(i + 64, len(todo)))
        if "faces" in steps:
            todo = [dict(r) for r in con.execute(
                """SELECT id, path, kind, name FROM media WHERE kind = 'image'
                   AND id NOT IN (SELECT media_id FROM ml_done WHERE task = 'faces')""")]
            _job(con, total=len(todo), done=0, detail=json.dumps({"step": "Finding faces", "pid": os.getpid()}))
            faces = Faces() if todo else None
            rows = []
            for n, r in enumerate(todo, 1):
                p = _image_for(r)
                if p:
                    for (x, y, w, h), score, emb in faces.find(p, r["name"]):
                        rows.append((r["id"], x, y, w, h, score, emb.astype(np.float32).tobytes()))
                if n % 200 == 0 or n == len(todo):
                    con.executemany("INSERT INTO faces(media_id, x, y, w, h, score, emb) VALUES (?,?,?,?,?,?,?)", rows)
                    con.executemany("INSERT OR IGNORE INTO ml_done VALUES (?, 'faces')",
                                    [(t["id"],) for t in todo[max(0, n - 200):n]])
                    rows = []
                    _job(con, done=n)
        if "people" in steps:
            _job(con, detail=json.dumps({"step": "Grouping people", "pid": os.getpid()}))
            group_people(con)
        if "similar" in steps:
            _job(con, detail=json.dumps({"step": "Finding similar shots", "pid": os.getpid()}))
            group_similar(con)
        _job(con, status="done", finished=time.strftime("%Y-%m-%dT%H:%M:%S"), detail=json.dumps({"step": "Done"}))
    except Exception as e:
        _job(con, status="failed", finished=time.strftime("%Y-%m-%dT%H:%M:%S"), detail=json.dumps({"error": str(e)}))
        raise


class _Union:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def join(self, a, b):
        a, b = self.find(a), self.find(b)
        if a != b:
            self.p[max(a, b)] = min(a, b)


def cluster_faces(embs, k=FACE_NEIGHBOURS, link=FACE_LINK, merge=PEOPLE_MERGE, iters=25, seed=0):
    """Group face vectors into people. Returns a label per face (-1 = not grouped).

    1. Link each face only to *mutual* close matches (each among the other's k most similar, and similar enough).
       "Generic" faces resemble everyone but are nobody's closest match, so they can't bridge different people.
    2. Chinese whispers on that graph (the method dlib uses): tight, reliable groups.
    3. Merge groups whose average faces match strongly; averages are far less noisy than single faces.
    Linking faces directly (A~B, B~C, ...) chained almost everyone into one person on a real library.
    """
    n = len(embs)
    if n == 0:
        return np.zeros(0, dtype=np.int64)
    k = min(k, n - 1) if n > 1 else 0
    nbr = np.zeros((n, max(k, 1)), dtype=np.int64)
    sim = np.full((n, max(k, 1)), -1.0, dtype=np.float32)
    if k:
        for i in range(0, n, 2048):
            s = embs[i:i + 2048] @ embs.T
            s[np.arange(s.shape[0]), np.arange(i, i + s.shape[0])] = -1  # not yourself
            idx = np.argpartition(-s, k - 1, axis=1)[:, :k]
            nbr[i:i + 2048] = idx
            sim[i:i + 2048] = np.take_along_axis(s, idx, axis=1)
    close = [set(nbr[i][sim[i] >= link].tolist()) for i in range(n)]
    adj = [[(int(j), float(sim[i][m])) for m, j in enumerate(nbr[i]) if sim[i][m] >= link and i in close[j]]
           for i in range(n)]
    labels = np.arange(n)
    rng = np.random.default_rng(seed)
    for _ in range(iters):
        changed = 0
        for i in rng.permutation(n):
            if adj[i]:
                votes = {}
                for j, w in adj[i]:
                    votes[labels[j]] = votes.get(labels[j], 0.0) + w
                best = max(votes, key=votes.get)
                if best != labels[i]:
                    labels[i] = best
                    changed += 1
        if not changed:
            break
    groups = {}
    for i in range(n):
        if adj[i]:
            groups.setdefault(int(labels[i]), []).append(i)
    tight = [g for g in groups.values() if len(g) >= 2]
    out = np.full(n, -1, dtype=np.int64)
    if not tight:
        return out
    cents = _norm(np.stack([embs[g].mean(axis=0) for g in tight]))
    u = _Union(len(tight))
    for i in range(0, len(tight), 2048):
        s = cents[i:i + 2048] @ cents.T
        for a, b in zip(*np.nonzero(s >= merge)):
            if a + i < b:
                u.join(a + i, int(b))
    for g, members in enumerate(tight):
        out[members] = u.find(g)
    return out


def group_people(con):
    """Cluster faces into people; keep the ids, names and your merges of people found before.

    A person you named or merged is "confirmed": every group of faces that was theirs before stays theirs (so two
    groups you merged stay one person), and their faces that no longer fall into any group are kept too.
    """
    rows = con.execute("""SELECT id, media_id, emb, person, score FROM faces
                          WHERE score >= ? AND w >= ? ORDER BY score DESC, id""",
                       (MIN_FACE_SCORE, MIN_FACE_FRACTION)).fetchall()
    old = {r["id"]: r["person"] for r in con.execute("SELECT id, person FROM faces")}
    confirmed = {r["id"] for r in con.execute("SELECT id FROM people WHERE confirmed = 1")}
    con.execute("UPDATE faces SET person = NULL")
    ids = [r["id"] for r in rows]
    labels = (cluster_faces(_norm(np.stack([np.frombuffer(r["emb"], dtype=np.float32) for r in rows])))
              if rows else [])
    clusters = {}
    for k, lab in enumerate(labels):
        if lab >= 0:
            clusters.setdefault(int(lab), []).append(k)
    used, keep, assigned = set(), set(), set()
    for members in sorted(clusters.values(), key=len, reverse=True):
        # reuse the previous person id this cluster overlaps most, so names survive regrouping
        votes = {}
        for k in members:
            p = old.get(ids[k])
            if p is not None and p not in used:
                votes[p] = votes.get(p, 0) + 1
        pid = max(votes, key=votes.get) if votes else None
        if pid not in confirmed and len({rows[k]["media_id"] for k in members}) < MIN_FACES_PER_PERSON:
            continue
        if pid is None:
            pid = con.execute("INSERT INTO people(name) VALUES (NULL)").lastrowid
        if pid not in confirmed:
            used.add(pid)          # a confirmed person can take several groups (you said they're one person)
        con.executemany("UPDATE faces SET person = ? WHERE id = ?", [(pid, ids[k]) for k in members])
        assigned.update(ids[k] for k in members)
        keep.add(pid)
    stay = [(p, f) for f, p in old.items() if p in confirmed and f not in assigned]
    con.executemany("UPDATE faces SET person = ? WHERE id = ?", stay)
    keep.update(p for p, _ in stay)
    keep = sorted(keep)
    con.execute(f"DELETE FROM people WHERE id NOT IN ({','.join('?' * len(keep)) or 'NULL'})", keep)
    con.execute("DELETE FROM people_not_same WHERE a NOT IN (SELECT id FROM people) OR b NOT IN (SELECT id FROM people)")
    for pid in keep:
        _refresh_person(con, pid)
    con.commit()


def _refresh_person(con, pid):
    """Photo count, and a cover face (keeps the current one if it's still theirs)."""
    n = con.execute("SELECT count(DISTINCT media_id) FROM faces WHERE person = ?", (pid,)).fetchone()[0]
    cover = con.execute("""SELECT f.id FROM faces f JOIN people p ON p.id = f.person
                           WHERE f.person = ? ORDER BY f.id = p.cover_face DESC, f.score DESC, f.id LIMIT 1""",
                        (pid,)).fetchone()
    con.execute("UPDATE people SET n = ?, cover_face = ? WHERE id = ?", (n, cover[0] if cover else None, pid))


def person_centroids(con):
    """{person id: average face vector}."""
    sums = {}
    for r in con.execute("SELECT person, emb FROM faces WHERE person IS NOT NULL"):
        v = np.frombuffer(r["emb"], dtype=np.float32)
        s = sums.get(r["person"])
        sums[r["person"]] = v.copy() if s is None else s + v
    return {p: _norm(v) for p, v in sums.items()}


def maybe_same(cents, not_same=(), person=None, threshold=MAYBE_SAME, limit=60):
    """[(a, b, similarity)] pairs of people who might be one person, most likely first."""
    pids = sorted(cents) if person is None else [person]
    if not cents or (person is not None and person not in cents):
        return []
    others = sorted(cents)
    m = np.stack([cents[p] for p in others])
    out = []
    for a in pids:
        sims = m @ cents[a]
        for b, s in zip(others, sims):
            if b == a or (person is None and b < a):
                continue
            if float(s) >= threshold and (min(a, b), max(a, b)) not in not_same:
                out.append((a, b, float(s)))
    return sorted(out, key=lambda t: -t[2])[:limit]  # limit=None: all of them


def group_similar(con):
    """Near-identical shots: very similar CLIP vectors, taken within a couple of minutes of each other."""
    rows = con.execute("""SELECT m.id, m.taken_at, c.emb FROM media m JOIN clip c ON c.media_id = m.id
                          WHERE m.kind = 'image' ORDER BY m.taken_at, m.id""").fetchall()
    con.execute("DELETE FROM similar")
    if len(rows) < 2:
        con.commit()
        return
    from datetime import datetime
    t = np.array([datetime.fromisoformat(r["taken_at"]).timestamp() for r in rows])
    embs = _norm(np.stack([np.frombuffer(r["emb"], dtype=np.float16).astype(np.float32) for r in rows]))
    u = _Union(len(rows))
    for i in range(len(rows)):
        j = i + 1
        while j < len(rows) and j - i <= 40 and t[j] - t[i] <= SIMILAR_WINDOW_S:
            if float(embs[i] @ embs[j]) >= SIMILAR_COS:
                u.join(i, j)
            j += 1
    groups = {}
    for k in range(len(rows)):
        groups.setdefault(u.find(k), []).append(rows[k]["id"])
    gid = 0
    for members in groups.values():
        if len(members) > 1:
            gid += 1
            con.executemany("INSERT INTO similar(group_id, media_id) VALUES (?, ?)", [(gid, m) for m in members])
    con.commit()


# ---------------------------------------------------------------- API
_text_model = {"clip": None, "matrix": None, "ids": None, "count": -1}


def _search_matrix():
    con = connect()
    n = con.execute("SELECT count(*) FROM clip").fetchone()[0]
    if n != _text_model["count"]:
        rows = con.execute("SELECT media_id, emb FROM clip").fetchall()
        _text_model["ids"] = np.array([r["media_id"] for r in rows], dtype=np.int64)
        _text_model["matrix"] = (_norm(np.stack([np.frombuffer(r["emb"], dtype=np.float16).astype(np.float32)
                                                 for r in rows])) if rows else np.zeros((0, 1), np.float32))
        _text_model["count"] = n
    return _text_model["ids"], _text_model["matrix"]


def smart_rank(q, limit=200):
    """[(media_id, score)] best first, only clearly relevant ones."""
    if _text_model["clip"] is None:
        _text_model["clip"] = Clip()
    ids, matrix = _search_matrix()
    if len(ids) == 0:
        return []
    scores = matrix @ _text_model["clip"].text(q)
    order = np.argsort(-scores)[:limit]
    top = float(scores[order[0]])
    cutoff = top - (0.15 if FAKE else 0.06)  # CLIP scores are relative: keep what's close to the best match
    return [(int(ids[k]), float(scores[k])) for k in order if scores[k] >= cutoff]


@router.get("/api/smart-search")
def smart_search(q: str, limit: int = 200, album: str = "", filter: str = ""):
    import server
    if not q.strip():
        return {"items": [], "ready": True}
    if not models_ready() or connect().execute("SELECT count(*) FROM clip").fetchone()[0] == 0:
        return {"items": [], "ready": False}
    ranked = smart_rank(q.strip(), limit)
    if not ranked:
        return {"items": [], "ready": True}
    where, args = selection(album, True, filter, ids=[m for m, _ in ranked])
    rows = {r["id"]: dict(r) for r in connect().execute(f"SELECT {server.ITEM_COLS} FROM media WHERE {where}", args)}
    return {"ready": True, "items": [{**rows[m], "score": round(s, 4)} for m, s in ranked if m in rows]}


@router.get("/api/people")
def people():
    rows = connect().execute("""SELECT id, name, n, cover_face FROM people WHERE n >= 1
                                ORDER BY name IS NULL, n DESC, id""").fetchall()
    return [dict(r) for r in rows]


@router.get("/api/people/suggestions")
def people_suggestions(person: int | None = None):
    """People the face model thinks might be the same person (it wasn't sure enough to merge them itself)."""
    con = connect()
    not_same = {(r["a"], r["b"]) for r in con.execute("SELECT a, b FROM people_not_same")}
    info = {r["id"]: dict(r) for r in con.execute("SELECT id, name, n, cover_face FROM people WHERE n >= 1")}
    cents = {p: v for p, v in person_centroids(con).items() if p in info}
    total = len(maybe_same(cents, not_same, person, limit=None))
    pairs = maybe_same(cents, not_same, person)
    faces = {}
    for pid in {p for a, b, _ in pairs for p in (a, b)}:   # a few clear faces each: one cover is often too little to judge
        faces[pid] = [r[0] for r in con.execute("""SELECT id FROM faces WHERE person = ? AND id != coalesce(?, -1)
                                                   ORDER BY score DESC, w DESC LIMIT 3""", (pid, info[pid]["cover_face"]))]
    return {"total": total, "pairs": [{"a": {**info[a], "faces": faces[a]}, "b": {**info[b], "faces": faces[b]},
                                       "similarity": round(s, 3)} for a, b, s in pairs]}


class Pair(BaseModel):
    a: int
    b: int


@router.post("/api/people/not-same")
def not_same(body: Pair):
    """'These two are different people': stop suggesting them."""
    if body.a == body.b:
        raise HTTPException(400, "pick two different people")
    con = connect()
    con.execute("INSERT OR IGNORE INTO people_not_same(a, b) VALUES (?, ?)", (min(body.a, body.b), max(body.a, body.b)))
    con.commit()
    return {"ok": True}


class PersonName(BaseModel):
    name: str


@router.post("/api/people/{person_id}/name")
def name_person(person_id: int, body: PersonName):
    con = connect()
    if not con.execute("SELECT 1 FROM people WHERE id = ?", (person_id,)).fetchone():
        raise HTTPException(404, "no such person")
    con.execute("UPDATE people SET name = ?, confirmed = 1 WHERE id = ?", (body.name.strip()[:100] or None, person_id))
    con.commit()
    return {"ok": True}


class Merge(BaseModel):
    into: int


class MergeMany(BaseModel):
    ids: list[int]
    into: int | None = None


def _merge(con, ids, into):
    """These people are all one person. Keeps `into` (its name, or the first name among them)."""
    ids = [i for i in dict.fromkeys(ids) if i != into]
    found = {r["id"]: r["name"] for r in con.execute(
        f"SELECT id, name FROM people WHERE id IN ({','.join('?' * (len(ids) + 1))})", ids + [into])}
    if not ids or into not in found or any(i not in found for i in ids):
        raise HTTPException(400, "pick at least two different people")
    name = found[into] or next((found[i] for i in ids if found[i]), None)
    q = ",".join("?" * len(ids))
    con.execute(f"UPDATE faces SET person = ? WHERE person IN ({q})", [into] + ids)
    con.execute(f"DELETE FROM people WHERE id IN ({q})", ids)
    con.execute(f"DELETE FROM people_not_same WHERE a IN ({q}) OR b IN ({q})", ids + ids)
    con.execute("UPDATE people SET name = ?, confirmed = 1 WHERE id = ?", (name, into))
    _refresh_person(con, into)
    con.commit()


@router.post("/api/people/{person_id}/merge")
def merge_person(person_id: int, body: Merge):
    """'These two are the same person.'"""
    _merge(connect(), [person_id], body.into)
    return {"ok": True}


@router.post("/api/people/merge")
def merge_people(body: MergeMany):
    """Several people are one person: merged into `into`, or into the named / biggest of them."""
    con = connect()
    into = body.into
    if into is None:
        best = con.execute(f"""SELECT id FROM people WHERE id IN ({','.join('?' * len(body.ids)) or 'NULL'})
                               ORDER BY name IS NULL, n DESC, id LIMIT 1""", body.ids).fetchone()
        into = best[0] if best else -1
    _merge(con, body.ids, into)
    return {"ok": True, "into": into}


@router.get("/face/{face_id}")
def face_crop(face_id: int):
    out = CACHE / "faces" / f"{face_id}.jpg"
    if not out.exists():
        f = connect().execute("SELECT * FROM faces WHERE id = ?", (face_id,)).fetchone()
        if not f or not thumb_path(f["media_id"]).exists():
            raise HTTPException(404, "no face")
        from PIL import Image, ImageOps
        # upright first: faces were found on the rotated image (OpenCV applies EXIF orientation)
        im = ImageOps.exif_transpose(Image.open(thumb_path(f["media_id"]))).convert("RGB")
        W, H = im.size
        cx, cy, side = (f["x"] + f["w"] / 2) * W, (f["y"] + f["h"] / 2) * H, max(f["w"] * W, f["h"] * H) * 1.6
        box = (int(max(cx - side / 2, 0)), int(max(cy - side / 2, 0)), int(min(cx + side / 2, W)), int(min(cy + side / 2, H)))
        out.parent.mkdir(parents=True, exist_ok=True)
        im.crop(box).resize((160, 160)).save(out, quality=85)
    return FileResponse(out, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"})


_sharpness = {}


def sharpness(media_id):
    """How much fine detail the thumbnail has (blurry / shaken shots score low). Cached."""
    if media_id not in _sharpness:
        from PIL import Image, ImageFilter
        p = thumb_path(media_id)
        try:
            g = Image.open(p).convert("L")
            _sharpness[media_id] = float(np.asarray(g.filter(ImageFilter.FIND_EDGES), dtype=np.float32).var())
        except OSError:
            _sharpness[media_id] = 0.0
    return _sharpness[media_id]


def best_of(items):
    """The shot to keep: a favourite if there is one, else the sharpest, bigger photos winning ties."""
    def score(i):
        px = (i.get("width") or 0) * (i.get("height") or 0)
        return (bool(i.get("fav")), sharpness(i["id"]) * max(px, 1) ** 0.25)
    return max(items, key=score)["id"]


def _group_key(ids):
    return ",".join(map(str, sorted(ids)))


@router.get("/api/similar")
def similar(offset: int = 0, limit: int = 30):
    import server
    from search import VISIBLE
    con = connect()
    dismissed = {r[0] for r in con.execute("SELECT group_key FROM similar_dismissed")}
    members = {}
    for r in con.execute(f"""SELECT s.group_id, s.media_id FROM similar s JOIN media ON media.id = s.media_id
                             WHERE {VISIBLE} ORDER BY s.group_id"""):
        members.setdefault(r[0], []).append(r[1])
    groups = sorted(((g, m) for g, m in members.items() if len(m) > 1 and _group_key(m) not in dismissed),
                    key=lambda t: (-len(t[1]), t[0]))
    out = []
    for g, ids in groups[offset:offset + limit]:
        items = [dict(i) for i in con.execute(
            f"SELECT {server.ITEM_COLS} FROM media WHERE id IN ({','.join('?' * len(ids))}) "
            "ORDER BY taken_at, id", ids)]
        out.append({"group": g, "key": _group_key(ids), "best": best_of(items), "items": items})
    return {"total": len(groups), "groups": out}


class GroupKey(BaseModel):
    key: str


@router.post("/api/similar/dismiss")
def dismiss_similar(body: GroupKey):
    """'These aren't duplicates' / 'keep them all': stop showing this group."""
    con = connect()
    con.execute("INSERT OR IGNORE INTO similar_dismissed(group_key) VALUES (?)", (body.key,))
    con.commit()
    return {"ok": True}


def _ml_running():
    r = connect().execute("SELECT * FROM jobs WHERE kind = 'ml' ORDER BY id DESC LIMIT 1").fetchone()
    if not r:
        return None
    job = {**dict(r), "detail": json.loads(r["detail"] or "{}")}
    pid = job["detail"].get("pid")
    if job["status"] == "running" and pid:
        try:
            os.kill(pid, 0)
        except OSError:
            job["status"] = "stopped"
    return job


@router.get("/api/ml/status")
def ml_status():
    con = connect()
    c = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
    return {"models": models_ready(), "images": c("SELECT count(*) FROM media"),
            "understood": c("SELECT count(*) FROM clip"), "faces": c("SELECT count(*) FROM faces"),
            "people": c("SELECT count(*) FROM people"), "similar_groups": c("SELECT count(DISTINCT group_id) FROM similar"),
            "job": _ml_running()}


@router.post("/api/ml/run")
def ml_run():
    job = _ml_running()
    if job and job["status"] == "running":
        return {"started": False, "job": job}
    if not models_ready():
        raise HTTPException(400, "Models aren't downloaded yet: run  ../.venv/bin/python ml.py download  (in the gallery folder)")
    log = open(BASE / "ml.log", "a")
    subprocess.Popen([sys.executable, str(BASE / "ml.py"), "run"], cwd=BASE, stdout=log, stderr=log,
                     start_new_session=True)
    return {"started": True}


if __name__ == "__main__":
    if sys.argv[1:2] == ["download"]:
        download_models()
    elif sys.argv[1:2] == ["run"]:
        run(tuple(sys.argv[2:]) or ("clip", "faces", "people", "similar"))
    else:
        print(__doc__)
