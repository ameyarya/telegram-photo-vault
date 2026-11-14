"""First-run experience: setup wizard, doctor, plain local mode, and a gallery with no Telegram backup."""
import os
import subprocess
import sys

import httpx
import pytest
from playwright.sync_api import expect

from conftest import PY, ROOT as GALLERY, build_ml_drive, free_port, make_world, photo, serve, start_server, stop

ROOT = GALLERY.parent    # the project folder (conftest's ROOT is gallery/)


@pytest.fixture
def fresh(tmp_path):
    """A brand-new install: a folder of photos, an empty settings file location, nothing else."""
    pics = tmp_path / "pics"
    for folder, n in (("Holiday", 3), ("Family", 1)):
        for i in range(n):
            photo(pics / folder / f"img{i}.jpg", 800, 600, color="c=red" if i else "testsrc")
    photo(pics / "loose.jpg", 640, 480)
    env = {**os.environ, "VAULT_CONFIG": str(tmp_path / "config.env"), "STATE_DIR": str(tmp_path / "state"),
           "GALLERY_DATA": str(tmp_path / "data"), "PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"}
    for k in ("DRIVE", "GALLERY_DRIVE", "FOLDERS", "GALLERY_TELEGRAM", "GALLERY_BACKUP", "TG_API_ID", "TG_API_HASH", "TG_CHANNEL_ID"):
        env.pop(k, None)
    return {"pics": pics, "env": env, "config": tmp_path / "config.env", "tmp": tmp_path}


def setup(f, *extra):
    return subprocess.run([PY, "gallery/setup.py", "--folder", str(f["pics"]), "--no-telegram", "--no-service", "--yes", *extra],
                          cwd=ROOT, env=f["env"], capture_output=True, text=True, timeout=300)


def test_setup_wizard_builds_a_working_gallery_without_any_questions(fresh):
    r = setup(fresh)
    assert r.returncode == 0, r.stdout + r.stderr
    cfg = fresh["config"].read_text()
    assert f'DRIVE="{fresh["pics"]}"' in cfg
    assert 'GALLERY_TELEGRAM="0"' in cfg and 'GALLERY_BACKUP="0"' in cfg   # no Telegram: the gallery says nothing about backup
    assert "TG_API_ID=" in cfg                                              # the example's comments and blanks are kept
    assert "Found 4 photos and videos" in r.stdout and "1 more sit" in r.stdout    # the loose photo is called out
    import sqlite3
    n = sqlite3.connect(fresh["tmp"] / "data" / "index.db").execute("SELECT count(*) FROM media").fetchone()[0]
    assert n == 4
    again = setup(fresh)                                                      # safe to run twice
    assert again.returncode == 0
    lines = fresh["config"].read_text().splitlines()
    assert sum(l.startswith("DRIVE=") for l in lines) == 1 and sum(l.startswith("GALLERY_BACKUP=") for l in lines) == 1


def test_setup_wizard_rejects_a_missing_folder(fresh, tmp_path):
    fresh["pics"] = tmp_path / "nope"
    r = setup(fresh)
    assert r.returncode == 1 and "isn't a folder" in r.stdout


def test_setup_explains_loose_photos_and_empty_folders(fresh, tmp_path):
    only_loose = tmp_path / "loose"
    photo(only_loose / "a.jpg", 100, 100)
    fresh["pics"] = only_loose
    assert "put them in a" in setup(fresh).stdout
    empty = tmp_path / "empty"; empty.mkdir()
    fresh["pics"] = empty
    assert "No photos found yet" in setup(fresh).stdout


def test_doctor_says_what_is_missing_and_how_to_fix_it(fresh):
    setup(fresh)
    r = subprocess.run([PY, "doctor.py"], cwd=GALLERY, env=fresh["env"], capture_output=True, text=True)
    assert "✓  Photos indexed: 4" in r.stdout and "2 folders inside it" in r.stdout
    assert "Padlock address" in r.stdout and "./vault https" in r.stdout          # optional things say how to add them
    assert "Telegram backup (off" in r.stdout
    gone = {**fresh["env"], "DRIVE": "/nonexistent/photos"}
    r = subprocess.run([PY, "doctor.py"], cwd=GALLERY, env=gone, capture_output=True, text=True)
    assert r.returncode == 1 and "✗  Photos folder: /nonexistent/photos" in r.stdout and "Set DRIVE in config.env" in r.stdout
    assert subprocess.run([PY, "doctor.py", "--url"], cwd=GALLERY, env=fresh["env"], capture_output=True,
                          text=True).stdout.strip().startswith("http://localhost:")


def test_local_mode_needs_no_certificate_no_sudo_no_hosts_entry(fresh):
    setup(fresh)
    port = free_port()
    env = {**fresh["env"], "GALLERY_PORT": str(port), "GALLERY_CERT": "/nonexistent.pem", "GALLERY_KEY": "/nonexistent-key.pem",
           "GALLERY_TELEGRAM": "0", "GALLERY_BACKUP": "0", "DRIVE": str(fresh["pics"])}
    p = subprocess.Popen([PY, "serve.py"], cwd=GALLERY, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for _ in range(100):
            try:
                if httpx.get(f"http://localhost:{port}/api/status", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            import time; time.sleep(0.2)
        s = httpx.get(f"http://localhost:{port}/api/status").json()
        assert s["total"] == 4 and s["backup_on"] is False
        assert httpx.get(f"http://localhost:{port}/").status_code == 200
    finally:
        p.terminate(); p.wait(10)


def test_vault_command_lists_what_it_can_do():
    r = subprocess.run(["zsh", "vault", "help"], cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0
    for word in ("setup.sh", "doctor", "service on", "https", "telegram", "index", "update"):
        assert word in r.stdout


def test_set_env_keeps_comments_and_can_remove_a_key(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(GALLERY))
    import setup as wizard
    cfg = tmp_path / "c.env"
    cfg.write_text("# my notes\nA=1\nB=\"2\"  # two\n")
    monkeypatch.setattr(wizard, "CONFIG", cfg)
    wizard.set_env("A", "x y"); wizard.set_env("B", None); wizard.set_env("C", "3")
    assert cfg.read_text() == '# my notes\nA="x y"\nC="3"\n'


# ---- a gallery with no Telegram backup says nothing about backup
@pytest.fixture(scope="module")
def nobackup(tmp_path_factory):
    w = serve(make_world(tmp_path_factory.mktemp("nobackup"), build_ml_drive, GALLERY_BACKUP="0"))
    yield w
    stop(w)


@pytest.mark.parametrize("page", [{"world": "nobackup"}], indirect=True)
def test_no_backup_noise_when_telegram_is_off(nobackup, page):
    page.goto(nobackup["url"] + "/#/photos")
    page.wait_for_selector("#days .tile")
    assert page.locator("#days .tile .off").count() == 0                     # no crossed-out cloud on every photo
    assert "Telegram backup off" in page.inner_text("#stat") and "Backed up" not in page.inner_text("#stat")
    page.click("#filterBtn")
    assert page.locator("#filterMenu .chip[data-f='notbackedup']").count() == 0
    page.goto(nobackup["url"] + "/#/backup")
    expect(page.locator(".panel")).to_contain_text("Telegram backup is off")
    expect(page.locator(".panel")).to_contain_text("./vault telegram")
