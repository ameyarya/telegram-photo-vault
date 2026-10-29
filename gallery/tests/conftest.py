"""Builds small fake drives with real photos/videos, indexes them, and runs isolated servers on them.

Worlds (each has its own drive, index, state folder and server):
  world      the main sample library; Telegram disabled; also used for the dashboard
  cloud      the same library plus a fake Telegram channel holding every file but the screenshot (restore, streaming)
  mlworld    coloured photos, named "faces" and a burst of near-identical shots, analysed with fake ML models
  bigworld   240 photos over two years, for the date scrubber and windowed loading
  liveworld  Live Photos, a burst with one sharp shot, three people; analysed, fake Telegram holding every file
             (Live Photos, trash, cleaning up similar shots, telling the face grouping who's who)
Nothing here touches the real drive, index, thumbnails or Telegram.
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
sys.path.insert(0, str(ROOT))  # the gallery's modules, wherever pytest is started from
SHOTS = ROOT / "tests" / "screenshots"


def run(*cmd, **kw):
    subprocess.run(cmd, check=True, capture_output=True, **kw)


def photo(path, w, h, date=None, orientation=None, color="testsrc", tags=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    src = path if path.suffix.lower() in (".jpg", ".png") else path.with_suffix(".tmp.jpg")
    source = f"color=c={color[2:]}:size={w}x{h}" if color.startswith("c=") else f"{color}=size={w}x{h}"
    run("ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i", source, "-frames:v", "1", str(src))
    if src != path:  # HEIC: convert with the same tool macOS uses
        run("sips", "-s", "format", "heic", str(src), "--out", str(path))
        src.unlink()
    args = list(tags)
    if date:
        args += [f"-DateTimeOriginal={date}", f"-CreateDate={date}"]
    if orientation:
        args += [f"-Orientation#={orientation}"]
    if args:
        run("exiftool", "-overwrite_original", *args, str(path))


def video(path, seconds, created_utc, w=640, h=360):
    path.parent.mkdir(parents=True, exist_ok=True)
    run("ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate=25", "-f", "lavfi", "-i", "sine",
        "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart",
        "-metadata", f"creation_time={created_utc}", str(path))


SONY = ["-Make=SONY", "-Model=ILCE-7M3", "-LensModel=FE 24-70mm F2.8 GM", "-ISO=200", "-FNumber=2.8",
        "-ExposureTime=1/250", "-FocalLength=35"]
GPS = ["-GPSLatitude=26.9124", "-GPSLatitudeRef=N", "-GPSLongitude=75.7873", "-GPSLongitudeRef=E"]


def build_drive(d: Path):
    photo(d / "vacation/beach.jpg", 1200, 800, "2024:02:07 10:00:00", tags=SONY)
    photo(d / "vacation/sunset.jpg", 1200, 800, "2024:02:07 18:30:00", color="mandelbrot")
    photo(d / "vacation/pier.jpg", 1200, 800, "2024:02:08 09:15:00", color="rgbtestsrc", tags=GPS)
    photo(d / "family/wedding/day 1/portrait.jpg", 1200, 800, "2024:01:19 09:00:00", orientation=6)  # stored sideways
    photo(d / "family/wedding/group.jpg", 1200, 800, "2024:01:19 11:00:00", color="smptehdbars")  # beside sub-folders
    video(d / "family/wedding/day 1/clip.mp4", 2, "2024-01-19T18:00:00Z")
    photo(d / "family/wedding/day 2/IMG_0001.heic", 1000, 750, "2024:01:20 12:00:00", color="smptebars")
    photo(d / "iphone/screenshot.png", 600, 1300)  # no camera date: falls back to the file date
    ts = datetime(2023, 5, 1, 8, 0).timestamp()
    os.utime(d / "iphone/screenshot.png", (ts, ts))
    (d / "iphone/IMG_0001.AAE").write_text("<plist/>")     # sidecar: must be ignored
    (d / "iphone/.DS_Store").write_bytes(b"\0")           # junk: must be ignored
    (d / "iphone/notes.txt").write_text("not media")      # not media: must be ignored
    (d / "iphone/broken.MP4").write_bytes(b"")            # empty file: must be ignored
    video(d / "_split/family/videos/film_part00.mp4", 2, "2024-08-26T07:00:00Z")  # parts of a >2GB original
    video(d / "_split/family/videos/film_part01.mp4", 2, "2024-08-26T07:00:00Z")
    (d / "_split/.complete").touch()


def build_ml_drive(d: Path):
    photo(d / "colors/red.jpg", 400, 300, "2022:03:01 10:00:00", color="c=red")
    photo(d / "colors/green.jpg", 400, 300, "2022:04:01 10:00:00", color="c=green")
    photo(d / "colors/blue.jpg", 400, 300, "2022:05:01 10:00:00", color="c=blue")
    for i, (who, day) in enumerate([("alice", 2), ("alice", 3), ("alice", 4), ("bob", 5), ("bob", 6), ("bob", 7),
                                    ("carol", 8)]):
        photo(d / f"people/face_{who}_{i}.jpg", 400, 300, f"2022:06:{day:02d} 12:00:00", color="testsrc")
    for i, sec in enumerate([0, 20, 45]):  # a burst: near-identical, within a minute
        photo(d / f"burst/shot_{i}.jpg", 400, 300, f"2022:07:01 15:00:{sec:02d}", color="c=orange")
    photo(d / "burst/later.jpg", 400, 300, "2022:07:01 17:00:00", color="c=orange")  # same look, hours later


def build_live_drive(d: Path):
    photo(d / "phone/IMG_0100.JPG", 400, 300, "2023:03:01 10:00:00", color="c=red")      # a Live Photo ...
    video(d / "phone/IMG_0100.MOV", 2, "2023-03-01T10:00:00Z", 320, 240)                 # ... and its motion
    photo(d / "phone/IMG_0200.JPG", 400, 300, "2023:03:02 10:00:00", color="c=green")    # same name, but a real
    video(d / "phone/IMG_0200.MOV", 8, "2023-03-02T10:00:00Z", 320, 240)                 # 8 s video: not a Live Photo
    photo(d / "phone/plain.jpg", 400, 300, "2023:03:03 10:00:00", color="c=blue")
    photo(d / "burst/b_0.jpg", 400, 300, "2023:04:01 12:00:00", color="testsrc")         # sharp
    for i, sec in ((1, 10), (2, 20)):                                                     # same scene, blurred
        photo(d / f"burst/b_{i}.jpg", 400, 300, f"2023:04:01 12:00:{sec:02d}", color="testsrc")
        run("ffmpeg", "-nostdin", "-y", "-i", str(d / f"burst/b_{i}.jpg"), "-vf", "gblur=sigma=6", "-q:v", "2",
            str(d / f"burst/b_{i}.tmp.jpg"))
        (d / f"burst/b_{i}.tmp.jpg").replace(d / f"burst/b_{i}.jpg")
        run("exiftool", "-overwrite_original", f"-DateTimeOriginal=2023:04:01 12:00:{sec:02d}", str(d / f"burst/b_{i}.jpg"))
    photo(d / "big/huge.jpg", 4000, 3000, "2023:05:01 10:00:00", color="testsrc")   # bigger than the 2048px view
    for i, who in enumerate(["alice"] * 3 + ["bob"] * 3 + ["dave"] * 3):
        photo(d / f"people/face_{who}_{i}.jpg", 400, 300, f"2023:06:{i + 1:02d} 12:00:00", color="c=gray")


def build_big_drive(d: Path):
    (d / "years").mkdir(parents=True)
    base = d / "base.jpg"
    run("ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i", "testsrc=size=120x80", "-frames:v", "1", str(base))
    for month in range(24):
        y, m = 2022 + month // 12, month % 12 + 1
        for k in range(10):
            shutil.copyfile(base, d / "years" / f"{y}-{m:02d}-{k + 1:02d} 120000.jpg")
    base.unlink()
    run("exiftool", "-overwrite_original", "-DateTimeOriginal<Filename", "-q", str(d / "years"))


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(env, port):
    log = open(Path(env["GALLERY_DATA"]) / f"server-{port}.log", "w")
    proc = subprocess.Popen([PY, "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=log, stderr=log)
    url = f"http://127.0.0.1:{port}"
    for _ in range(150):
        try:
            if httpx.get(url + "/api/status", timeout=1).status_code == 200:
                return proc, url
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    proc.kill()
    raise RuntimeError(f"server didn't start, see {log.name}")


def make_world(base, builder, drive=None, **extra_env):
    """A drive (built, or shared), its own index/state, indexed, with a server running on it."""
    if drive is None:
        drive = base / "drive"
        drive.mkdir()
        builder(drive)
    data, state = base / "data", base / "state"
    data.mkdir()
    state.mkdir()
    # bare PATH like launchd gives the real service (regression: ffmpeg not found under launchd)
    env = {**os.environ, "GALLERY_DRIVE": str(drive), "GALLERY_DATA": str(data), "GALLERY_TELEGRAM": "0",
           "STATE_DIR": str(state), "VAULT_CONFIG": str(base / "no-config.env"),
           "GALLERY_TIMESTAMPS": str(state / "timestamps.tsv"), "GALLERY_RESTORE_DIR": str(base / "restored"),
           "GALLERY_MODELS": str(base / "models"), "GALLERY_ML_FAKE": "1", "GALLERY_NO_FINDER": "1",
           "GALLERY_DUPLICATES": str(state / "duplicates.tsv"), "FOLDERS": "", "PATH": "/usr/bin:/bin:/usr/sbin:/sbin", **extra_env}
    run(PY, "build_index.py", "--no-thumbs", cwd=ROOT, env=env)
    return {"drive": drive, "data": data, "state": state, "base": base, "env": env}


def serve(w):
    w["proc"], w["url"] = start_server(w["env"], free_port())
    return w


def stop(w):
    w["proc"].terminate()
    w["proc"].wait()


@pytest.fixture(scope="session")
def world(tmp_path_factory):
    w = make_world(tmp_path_factory.mktemp("vault"), build_drive)
    s = w["state"]  # what the backup leaves behind, for the dashboard
    (s / "all.txt").write_text("a\nb\nc\nd\n")
    (s / "done.log").write_text("a\nb\nc\n")
    (s / "duplicates.tsv").write_text("x/copy.jpg\tx/orig.jpg\n")
    (s / "empty_files.txt").write_text("iphone/broken.MP4\n")
    (s / "upload.log").write_text("2026-10-16 10:00:00  pass 1: 3 to go\n2026-10-16 10:01:00  batch failed (first: a)\n"
                                  "2026-10-16 10:02:00      3/4 done\n")
    yield serve(w)
    stop(w)


@pytest.fixture(scope="session")
def cloud(tmp_path_factory, world):
    """Same drive, own index, plus a fake Telegram channel with every file except the screenshot."""
    base = tmp_path_factory.mktemp("cloud")
    store = base / "channel"
    store.mkdir()
    w = make_world(base, None, drive=world["drive"], GALLERY_TELEGRAM="1", GALLERY_FAKE_TG=str(store))
    rels = sorted(str(p.relative_to(world["drive"])) for p in world["drive"].rglob("*")
                  if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in (".jpg", ".heic", ".mp4", ".png")
                  and p.stat().st_size and p.name != "screenshot.png")
    msgs = []
    for i, rel in enumerate(rels, start=100):
        shutil.copyfile(world["drive"] / rel, store / f"{i}.bin")
        msgs.append({"id": i, "caption": rel, "file": f"{i}.bin"})
    (store / "messages.json").write_text(json.dumps(msgs))
    # the timestamp manifest the backup saved: deliberately old, distinctive dates
    lines = ["path\tsize\tcreated\tmodified"] + [
        f"{rel}\t{(world['drive'] / rel).stat().st_size}\t2020-01-02T03:04:05\t2021-02-03T04:05:06" for rel in rels]
    (w["state"] / "timestamps.tsv").write_text("\n".join(lines) + "\n")
    w["messages"] = msgs
    serve(w)
    with httpx.Client(base_url=w["url"], timeout=30) as c:  # wait for the first sync to link uploads
        for _ in range(100):
            if c.get("/api/status").json()["uploaded"]:
                break
            time.sleep(0.1)
    yield w
    stop(w)


@pytest.fixture(scope="session")
def mlworld(tmp_path_factory):
    w = make_world(tmp_path_factory.mktemp("ml"), build_ml_drive)
    run(PY, "ml.py", "run", cwd=ROOT, env=w["env"])
    serve(w)
    yield w
    stop(w)


@pytest.fixture(scope="session")
def liveworld(tmp_path_factory):
    base = tmp_path_factory.mktemp("live")
    drive = base / "drive"
    build_live_drive(drive)
    store = base / "channel"
    store.mkdir()
    msgs = []
    for i, p in enumerate(sorted(p for p in drive.rglob("*") if p.is_file()), start=100):
        shutil.copyfile(p, store / f"{i}.bin")
        msgs.append({"id": i, "caption": str(p.relative_to(drive)), "file": f"{i}.bin"})
    (store / "messages.json").write_text(json.dumps(msgs))
    w = make_world(base, None, drive=drive, GALLERY_TELEGRAM="1", GALLERY_FAKE_TG=str(store))
    run(PY, "ml.py", "run", cwd=ROOT, env=w["env"])
    serve(w)
    with httpx.Client(base_url=w["url"], timeout=30) as c:
        for _ in range(100):
            if c.get("/api/status").json()["uploaded"]:
                break
            time.sleep(0.1)
    yield w
    stop(w)


@pytest.fixture(scope="session")
def bigworld(tmp_path_factory):
    w = serve(make_world(tmp_path_factory.mktemp("big"), build_big_drive))
    yield w
    stop(w)


@pytest.fixture(scope="session")
def api(world):
    with httpx.Client(base_url=world["url"], timeout=30) as c:
        yield c


@pytest.fixture(scope="session")
def items(api):
    return {i["name"]: i for i in api.get("/api/items").json()["items"]}


@pytest.fixture(scope="session")
def unplugged(world):
    """A second server over the same index, but with the drive 'unplugged' and no Telegram."""
    env = {**world["env"], "GALLERY_DRIVE": str(world["drive"]) + "-missing"}
    proc, url = start_server(env, free_port())
    with httpx.Client(base_url=url, timeout=30) as c:
        yield c
    proc.terminate()
    proc.wait()


@pytest.fixture(scope="session")
def browser():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception:  # Playwright's own browser not downloaded: use the installed Google Chrome
            b = p.chromium.launch(channel="chrome")
        yield b
        b.close()


@pytest.fixture
def page(browser, request):
    """A fresh browser tab on a world (default: world); fails the test if the page logs any JavaScript error.
    Parametrize indirectly with {"world": "mlworld", "viewport": {...}, "scheme": "light"} to change it."""
    opts = getattr(request, "param", {}) or {}
    w = request.getfixturevalue(opts.get("world", "world"))
    ctx = browser.new_context(viewport=opts.get("viewport", {"width": 1440, "height": 900}), has_touch=opts.get("touch", False),
                              color_scheme=opts.get("scheme", "dark"), accept_downloads=True)
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    # 401 (not signed in), 404 (no thumbnail) and 503 (Telegram unavailable) are expected answers the page handles and explains
    pg.on("console", lambda m: m.type == "error" and not re.search(r"status of (401|404|503)", m.text) and errors.append(m.text))
    pg.base = w["url"]
    pg.world = w
    yield pg
    ctx.close()
    assert not errors, f"JavaScript errors: {errors}"


def shot(page, name):
    SHOTS.mkdir(exist_ok=True)
    page.wait_for_timeout(400)  # let fade-ins and drawer slides finish
    page.screenshot(path=str(SHOTS / f"{name}.png"))


def all_oldest_first(page):
    """Pages open on Photos, newest first; most UI tests were written for everything, oldest first."""
    page.wait_for_selector("#chips, .tile, .empty, .cards")
    if page.locator("#chips .chip.on").count() and page.locator("#chips .chip.on").first.get_attribute("data-f") != "":
        pick_filter(page, "")
        page.wait_for_selector("#chips .chip.on[data-f='']", state="attached")
    if page.locator("#sortBtn").count() and "Newest" in page.locator("#sortBtn").inner_text():
        page.click("#sortBtn")
        page.wait_for_function("document.querySelector('#sortBtn')?.innerText.includes('Oldest')")
    page.wait_for_load_state("networkidle")              # the page has reloaded its photos


def pick_filter(page, f):
    """Choose a filter from the filter dropdown ("Photos ▾")."""
    if page.locator("#filterMenu").is_hidden():
        page.click("#filterBtn")
    page.click(f"#filterMenu .chip[data-f='{f}']")
