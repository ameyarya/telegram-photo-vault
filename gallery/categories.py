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
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter

from build_index import make_thumb, thumb_path
from config import DATA, local_path
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


def run(limit=None, log=print):
    con = connect(timeout=600)
    ensure_table(con)
    todo = [r["id"] for r in con.execute(
        """SELECT id, path, kind FROM media WHERE kind = 'image' AND live_of IS NULL AND thumb != -1
           AND id NOT IN (SELECT media_id FROM categories) AND id NOT IN (SELECT media_id FROM trash)
           ORDER BY taken_at DESC""")]
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
    run_dir = WORK / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    done = failed = 0
    with ThreadPoolExecutor(WORKERS) as pool:
        futures = [pool.submit(label_batch, n, b, run_dir) for n, b in enumerate(batches, 1)]
        for f in futures:
            try:
                rows = f.result()
            except Exception as e:  # leave those photos unlabelled: the next run retries them
                failed += 1
                log(f"batch failed: {e}")
                continue
            con.executemany("INSERT OR REPLACE INTO categories(media_id, cat) VALUES (?, ?)", rows)
            con.commit()
            done += len(rows)
            log(f"labelled {done}/{len(ready)}")
    shutil.rmtree(run_dir, ignore_errors=True)
    return {"labelled": done, "failed_batches": failed}


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


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["status"]:
        print(counts(connect()))
    elif args[:1] == ["run"]:
        lim = int(args[args.index("--limit") + 1]) if "--limit" in args else None
        print(run(lim))
    else:
        print(__doc__)
