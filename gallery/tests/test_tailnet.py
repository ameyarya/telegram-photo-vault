"""Reaching the gallery through `tailscale serve`: that proxy runs on the Mac, so its requests arrive from loopback, but the
people behind it are remote. serve.tailnet_app makes them sign in and keeps the Mac's no-login access to the other port."""
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from conftest import ROOT, free_port


@pytest.fixture(scope="module")
def tail(world):
    port = free_port()
    log = open(Path(world["env"]["GALLERY_DATA"]) / f"tail-{port}.log", "w")
    code = ("import sys, uvicorn; sys.path.insert(0, '.'); import server, serve; "
            f"uvicorn.run(serve.tailnet_app(server.app), host='127.0.0.1', port={port}, log_level='warning')")
    proc = subprocess.Popen([sys.executable, "-c", code], cwd=ROOT, env=world["env"], stdout=log, stderr=log)
    url = f"http://127.0.0.1:{port}"
    for _ in range(150):
        try:
            httpx.get(url + "/", timeout=1)
            break
        except httpx.HTTPError:
            import time; time.sleep(0.1)
    with httpx.Client(base_url=world["url"], timeout=30) as mac:
        mac.post("/api/users", json={"name": "Tailer", "password": "tail-pass-123"})
        yield url, mac
        for u in mac.get("/api/users").json()["users"]:
            mac.post(f"/api/users/{u['id']}/delete")
    proc.terminate()
    proc.wait()


def test_through_tailscale_you_must_sign_in_even_though_the_proxy_is_on_the_mac(tail):
    url, mac = tail
    assert mac.get("/api/me").json()["local"] is True                       # the Mac itself still needs no password
    with httpx.Client(base_url=url, timeout=30) as t:
        assert t.get("/api/me").status_code == 401
        assert t.get("/api/items").status_code == 401
        assert t.post("/api/settings", json={"memories": False}).status_code == 401
        assert t.post("/api/trash", json={"ids": [1]}).status_code == 401
        assert t.post("/api/login", json={"name": "Tailer", "password": "nope"}).status_code == 401
        r = t.post("/api/login", json={"name": "Tailer", "password": "tail-pass-123"})
        assert r.status_code == 200 and "secure" in r.headers["set-cookie"].lower()     # https at the edge
        t.cookies.set("pv_session", r.cookies["pv_session"])
        assert t.get("/api/me").json()["name"] == "Tailer"
        assert t.post("/api/trash", json={"ids": [1]}).status_code == 403              # a member still can't delete


def test_failed_sign_ins_are_counted_per_device(tail):
    url, _ = tail
    with httpx.Client(base_url=url, timeout=30) as t:
        for _ in range(5):
            t.post("/api/login", json={"name": "ghost", "password": "x"}, headers={"X-Forwarded-For": "100.64.0.9"})
        assert t.post("/api/login", json={"name": "ghost2", "password": "x"}, headers={"X-Forwarded-For": "100.64.0.9"}).status_code == 429
        assert t.post("/api/login", json={"name": "ghost3", "password": "x"}, headers={"X-Forwarded-For": "100.64.0.10"}).status_code == 401
