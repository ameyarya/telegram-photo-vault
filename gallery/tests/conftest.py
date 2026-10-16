"""Builds a small fake drive with real photos/videos, indexes it, and runs an isolated server on it.

Nothing here touches the real drive, index, thumbnails or Telegram.
"""
import os
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
SHOTS = ROOT / "tests" / "screenshots"


def run(*cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def photo(path, w, h, date=None, orientation=None, color="testsrc"):
    path.parent.mkdir(parents=True, exist_ok=True)
    src = path if path.suffix.lower() in (".jpg", ".png") else path.with_suffix(".tmp.jpg")
    run("ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i", f"{color}=size={w}x{h}", "-frames:v", "1", str(src))
    if src != path:  # HEIC: convert with the same tool macOS uses
        run("sips", "-s", "format", "heic", str(src), "--out", str(path))
        src.unlink()
    tags = []
    if date:
        tags += [f"-DateTimeOriginal={date}", f"-CreateDate={date}"]
    if orientation:
        tags += [f"-Orientation#={orientation}"]
    if tags:
        run("exiftool", "-overwrite_original", *tags, str(path))


def video(path, seconds, created_utc, w=640, h=360):
    path.parent.mkdir(parents=True, exist_ok=True)
    run("ffmpeg", "-nostdin", "-y", "-f", "lavfi", "-i", f"testsrc=size={w}x{h}:rate=25", "-f", "lavfi", "-i", "sine",
        "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart",
        "-metadata", f"creation_time={created_utc}", str(path))


def build_drive(d: Path):
    photo(d / "vacation/beach.jpg", 1200, 800, "2024:02:07 10:00:00")
    photo(d / "vacation/sunset.jpg", 1200, 800, "2024:02:07 18:30:00", color="mandelbrot")
    photo(d / "vacation/pier.jpg", 1200, 800, "2024:02:08 09:15:00", color="rgbtestsrc")
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


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(env, port):
    log = open(Path(env["GALLERY_DATA"]) / f"server-{port}.log", "w")
    proc = subprocess.Popen([PY, "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=log, stderr=log)
    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(url + "/api/status", timeout=1).status_code == 200:
                return proc, url
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    proc.kill()
    raise RuntimeError(f"server didn't start, see {log.name}")


@pytest.fixture(scope="session")
def world(tmp_path_factory):
    base = tmp_path_factory.mktemp("vault")
    drive, data = base / "drive", base / "data"
    drive.mkdir()
    data.mkdir()
    build_drive(drive)
    # bare PATH like launchd gives the real service (regression: ffmpeg not found under launchd)
    env = {**os.environ, "GALLERY_DRIVE": str(drive), "GALLERY_DATA": str(data), "GALLERY_TELEGRAM": "0",
           "STATE_DIR": str(base / "state"), "FOLDERS": "", "VAULT_CONFIG": str(base / "no-config.env"),
           "PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    subprocess.run([PY, "build_index.py", "--no-thumbs"], cwd=ROOT, env=env, check=True, capture_output=True)
    proc, url = start_server(env, free_port())
    yield {"url": url, "drive": drive, "data": data, "env": env}
    proc.terminate()
    proc.wait()


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
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser, world, request):
    """A fresh browser tab; fails the test if the page logs any JavaScript error."""
    opts = getattr(request, "param", {}) or {}
    ctx = browser.new_context(viewport=opts.get("viewport", {"width": 1440, "height": 900}),
                              color_scheme=opts.get("scheme", "dark"))
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.on("console", lambda m: m.type == "error" and "404" not in m.text and errors.append(m.text))
    pg.base = world["url"]
    yield pg
    ctx.close()
    assert not errors, f"JavaScript errors: {errors}"


def shot(page, name):
    SHOTS.mkdir(exist_ok=True)
    page.wait_for_timeout(400)  # let fade-ins and drawer slides finish
    page.screenshot(path=str(SHOTS / f"{name}.png"))
