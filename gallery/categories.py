"""Photo categories (Food, Selfies, Travel...) chosen by Claude from contact sheets of the local thumbnails.

Optional: it needs the `claude` command (Claude Code, signed in) and sends small previews (the 400px thumbnails,
shrunk further, 30 to a sheet) to Anthropic through it. Nothing runs unless you start it:

    ../.venv/bin/python categories.py run [--limit 300]      label photos that have no category yet
    ../.venv/bin/python categories.py status

Labels live in the `categories` table; the gallery's filter menu then offers each category that has photos.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from build_index import make_thumb, thumb_path
from config import BASE, DATA, local_path
from db import connect

router = APIRouter()

# key -> (menu label, what Claude is told it means)
CATEGORIES = {
    "selfies": ("Selfies", "a selfie, or a close portrait of one or two people looking at the camera"),
    "people": ("People", "people (not a selfie): groups, candid shots, events, family"),
    "food": ("Food", "food, drinks, meals, restaurant tables"),
    "travel": ("Travel", "landmarks, cities, buildings, streets, hotels, trips, sightseeing"),
    "nature": ("Nature", "landscapes, sky, sunsets, beaches, mountains, plants, flowers"),
    "pets": ("Pets & animals", "pets, wildlife and other animals"),
    "documents": ("Documents", "papers, receipts, tickets, forms, whiteboards, slides, text-heavy photos"),
    "screens": ("Screens & memes", "screenshots, memes, graphics, downloaded or app-made images"),
    "other": ("Other", "anything that fits none of the above"),
}
SHEET_TILES, SHEET_COLS, TILE = 30, 6, 220
BATCH_SHEETS = 3          # sheets per Claude call (about 90 photos)
WORKERS = int(os.environ.get("CLAUDE_WORKERS", 3))
MODEL = os.environ.get("CLAUDE_MODEL", "sonnet")
CLAUDE = os.environ.get("CLAUDE_BIN") or shutil.which("claude") or str(os.path.expanduser("~/.local/bin/claude"))
WORK = DATA / "categories"


def ensure_table(con):  # (db.py makes it too; this covers a script run on an old index)
    con.execute("CREATE TABLE IF NOT EXISTS categories (media_id INTEGER PRIMARY KEY, cat TEXT NOT NULL)")
    con.execute("CREATE INDEX IF NOT EXISTS categories_cat ON categories(cat)")


def make_sheet(ids, out):
    """One JPG with each thumbnail in a numbered tile (the number is the photo's position in this sheet)."""
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    rows = -(-len(ids) // SHEET_COLS)
    sheet = Image.new("RGB", (SHEET_COLS * TILE, rows * TILE), "white")
    draw = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.load_default(size=26)
    except TypeError:  # older Pillow
        font = ImageFont.load_default()
    for n, mid in enumerate(ids):
        x, y = (n % SHEET_COLS) * TILE, (n // SHEET_COLS) * TILE
        try:
            im = ImageOps.fit(ImageOps.exif_transpose(Image.open(thumb_path(mid))).convert("RGB"), (TILE - 4, TILE - 4))
            sheet.paste(im, (x + 2, y + 2))
        except OSError:
            pass
        draw.rectangle((x + 2, y + 2, x + 46, y + 34), fill="black")
        draw.text((x + 6, y + 4), str(n + 1), fill="yellow", font=font)
    sheet.save(out, quality=80)


def prompt_for(sheets):
    cats = "\n".join(f'  "{k}": {desc}' for k, (_, desc) in CATEGORIES.items())
    files = "\n".join(f"  {name}: photos numbered 1-{n}" for name, n in sheets)
    return (f"Each of these image files is a contact sheet of numbered photo tiles (the number is in the tile's top-left corner):\n"
            f"{files}\nOpen each one with the Read tool. Give every numbered photo exactly one category:\n{cats}\n"
            'Reply with ONLY a JSON object mapping "<sheet file name>:<number>" to a category key, e.g. {"s1.jpg:1": "food"}. '
            "No other text.")


def ask_claude(prompt, cwd):
    """Run Claude Code headless; returns the parsed {"sheet:number": category} answer or raises."""
    cmd = [CLAUDE, "-p", prompt, "--model", MODEL, "--allowedTools", "Read", "--strict-mcp-config",
           "--setting-sources", "project", "--no-session-persistence", "--output-format", "json"]
    last = None
    for attempt in range(3):
        try:
            r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=600, stdin=subprocess.DEVNULL)
            text = json.loads(r.stdout).get("result", "") if r.stdout.strip().startswith("{") else r.stdout
            m = re.search(r"\{.*\}", text, re.S)
            if m:
                return json.loads(m.group(0))
            last = f"no JSON in answer: {text[:200]!r} {r.stderr[:200]!r}"
        except (subprocess.TimeoutExpired, ValueError) as e:
            last = str(e)
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(last)


def label_batch(batch_no, groups, run_dir):
    """groups: [[media ids of one sheet], ...] -> [(media_id, category)]"""
    sheets, index = [], {}
    for i, ids in enumerate(groups, 1):
        name = f"b{batch_no}s{i}.jpg"
        make_sheet(ids, run_dir / name)
        sheets.append((name, len(ids)))
        for n, mid in enumerate(ids, 1):
            index[f"{name}:{n}"] = mid
    answer = ask_claude(prompt_for(sheets), run_dir)
    out = []
    for key, mid in index.items():
        cat = str(answer.get(key, "")).strip().lower()
        out.append((mid, cat if cat in CATEGORIES else "other"))
    return out


ELIGIBLE = """kind = 'image' AND live_of IS NULL AND thumb != -1 AND id NOT IN (SELECT media_id FROM trash)"""


def _job(con, job_id, **fields):
    con.execute(f"UPDATE jobs SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ?", [*fields.values(), job_id])
    con.commit()


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
        ready = []
        for mid in todo:
            if not thumb_path(mid).exists():
                row = con.execute("SELECT id, path, kind FROM media WHERE id = ?", (mid,)).fetchone()
                if local_path(row["path"]).is_file():
                    make_thumb(dict(row))
            if thumb_path(mid).exists():
                ready.append(mid)
        sheets = [ready[i:i + SHEET_TILES] for i in range(0, len(ready), SHEET_TILES)]
        batches = [sheets[i:i + BATCH_SHEETS] for i in range(0, len(sheets), BATCH_SHEETS)]
        log(f"{len(ready)} photos to label in {len(batches)} Claude calls")
        _job(con, job, total=len(ready), done=0)
        run_dir = WORK / time.strftime("%Y%m%d-%H%M%S")
        run_dir.mkdir(parents=True, exist_ok=True)
        done = failed = 0
        error = None
        with ThreadPoolExecutor(WORKERS) as pool:
            futures = [(b, pool.submit(label_batch, n, b, run_dir)) for n, b in enumerate(batches, 1)]
            for b, f in futures:
                try:
                    rows = f.result()
                except Exception as e:  # leave those photos unlabelled: the next run retries them
                    failed += 1
                    error = str(e)
                    log(f"batch failed: {e}")
                    continue
                con.executemany("INSERT OR REPLACE INTO categories(media_id, cat) VALUES (?, ?)", rows)
                con.commit()
                done += len(rows)
                _job(con, job, done=done)
                log(f"labelled {done}/{len(ready)}")
        shutil.rmtree(run_dir, ignore_errors=True)
        detail.update(failed_batches=failed, **({"error": error} if error else {}))
        _job(con, job, status="failed" if failed and not done else "done", detail=json.dumps(detail),
             finished=time.strftime("%Y-%m-%dT%H:%M:%S"))
        return {"labelled": done, "failed_batches": failed}
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
    latest = {k: [r[0] for r in con.execute("SELECT media_id FROM categories WHERE cat = ? ORDER BY rowid DESC LIMIT 6", (k,))]
              for k in CATEGORIES}
    last = con.execute("SELECT coalesce(max(rowid), 0) FROM categories").fetchone()[0]
    return {"categories": [{"key": k, "label": CATEGORIES[k][0], "count": have.get(k, 0), "latest": latest[k]} for k in CATEGORIES],
            "last": last, "labelled": sum(have.values()), "unsorted": max(0, eligible - sum(have.values())), "photos": eligible,
            "claude": bool(shutil.which(CLAUDE) or os.path.exists(CLAUDE)), "job": _running(),
            "calls_per": BATCH_SHEETS * SHEET_TILES}


@router.get("/api/categories/sphere")
def categories_sphere(n: int = 150):
    """Photos for the sphere on the Sorting page: random ones still waiting (else any), as media ids."""
    con = connect()
    n = max(1, min(n, 400))
    ids = [r[0] for r in con.execute(
        f"SELECT id FROM media WHERE {ELIGIBLE} AND id NOT IN (SELECT media_id FROM categories) ORDER BY random() LIMIT ?", (n,))]
    if len(ids) < n:
        more = con.execute(f"SELECT id FROM media WHERE {ELIGIBLE} ORDER BY random() LIMIT ?", (n,))
        ids += [r[0] for r in more if r[0] not in set(ids)][:n - len(ids)]
    return {"ids": ids}


@router.get("/api/categories/recent")
def categories_recent(since: int = 0, limit: int = 400):
    """Photos labelled after label number `since` (so the page can fly them into their folders as they are sorted)."""
    rows = connect().execute("SELECT rowid, media_id, cat FROM categories WHERE rowid > ? ORDER BY rowid LIMIT ?",
                             (since, min(limit, 2000))).fetchall()
    return {"items": [{"id": r[1], "cat": r[2]} for r in rows], "last": rows[-1][0] if rows else since}


class RunBody(BaseModel):
    limit: int | None = None


@router.post("/api/categories/run")
def categories_run(body: RunBody):
    job = _running()
    if job and job["status"] == "running":
        return {"started": False, "job": job}
    if not (shutil.which(CLAUDE) or os.path.exists(CLAUDE)):
        raise HTTPException(400, "Claude Code isn't installed on this Mac (the `claude` command was not found).")
    cmd = [sys.executable, str(BASE / "categories.py"), "run"] + (["--limit", str(max(1, body.limit))] if body.limit else [])
    subprocess.Popen(cmd, cwd=BASE, stdout=open(BASE / "categories.log", "a"), stderr=subprocess.STDOUT, start_new_session=True)
    return {"started": True}


@router.post("/api/categories/stop")
def categories_stop():
    job = _running()
    if job and job["status"] == "running":
        try:
            os.killpg(job["detail"]["pid"], signal.SIGTERM)  # the run and the `claude` calls it started
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
