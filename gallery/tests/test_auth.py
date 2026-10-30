"""Sharing on the home Wi-Fi: who gets in, signing in, what each kind of account may do, and taking access away.

`phone` is a second server on the same library that treats its (loopback) clients as phones on the home network.
"""
import sqlite3

import httpx
import pytest
from playwright.sync_api import expect

from conftest import shot, start_server, free_port


@pytest.fixture(scope="module")
def phone_world(world):
    env = {**world["env"], "GALLERY_LOOPBACK_IS_REMOTE": "1"}
    proc, url = start_server_remote(env)
    yield {**world, "url": url}
    proc.terminate()
    proc.wait()
    with httpx.Client(base_url=world["url"]) as mac:   # leave the library as other tests expect it
        mac.post("/api/sharing", json={"on": False})
        for u in mac.get("/api/users").json()["users"]:
            mac.post(f"/api/users/{u['id']}/delete")


def start_server_remote(env):
    import subprocess, sys, time
    from pathlib import Path
    from conftest import ROOT
    port = free_port()
    log = open(Path(env["GALLERY_DATA"]) / f"server-{port}.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "server:app", "--host", "127.0.0.1", "--port", str(port)],
                            cwd=ROOT, env=env, stdout=log, stderr=log)
    url = f"http://127.0.0.1:{port}"
    for _ in range(150):
        try:
            httpx.get(url + "/api/me", timeout=1)
            return proc, url
        except httpx.HTTPError:
            time.sleep(0.1)
    raise RuntimeError("phone server didn't start")


@pytest.fixture(scope="module")
def mac(world):
    with httpx.Client(base_url=world["url"], timeout=30) as c:
        yield c


def phone(phone_world, device="iPhone test"):
    return httpx.Client(base_url=phone_world["url"], timeout=30, headers={"User-Agent": device})


def sign_in(c, name, pw):
    r = c.post("/api/login", json={"name": name, "password": pw})
    if r.status_code == 200:
        c.cookies.set("pv_session", r.cookies["pv_session"])
    return r


def test_this_mac_never_needs_to_sign_in(mac):
    assert mac.get("/api/me").json() == {"id": 0, "name": "This Mac", "role": "owner", "local": True}
    assert mac.get("/api/items").status_code == 200


def test_phones_are_refused_until_sharing_is_on(mac, phone_world):
    with phone(phone_world) as p:
        for path in ("/", "/api/items", "/setup", "/api/login"):
            assert p.get(path).status_code == 403, path
    assert mac.post("/api/sharing", json={"on": True}).status_code == 400          # needs an account first
    assert mac.post("/api/users", json={"name": "Partner", "password": "short"}).status_code == 400
    assert mac.post("/api/users", json={"name": "Partner", "password": "sunflower-42"}).status_code == 200
    assert mac.post("/api/users", json={"name": "partner", "password": "another-pass"}).status_code == 400  # taken
    assert mac.post("/api/users", json={"name": "Me", "password": "owner-pass-1", "role": "owner"}).status_code == 200
    assert mac.post("/api/sharing", json={"on": True}).json()["sharing"] is True


def test_signed_out_phone_sees_only_the_sign_in_page(phone_world, items):
    with phone(phone_world) as p:
        assert p.get("/").status_code == 200                                         # the app shell: no data in it
        assert p.get("/setup").status_code == 200 and "Download certificate" in p.get("/setup").text
        assert p.get("/api/me").status_code == 401
        some = next(iter(items.values()))["id"]
        for path in ("/api/items", "/api/albums", "/api/status", f"/thumb/{some}", f"/original/{some}", "/api/people"):
            assert p.get(path).status_code == 401, path
        assert sign_in(p, "Partner", "wrong-password").status_code == 401
        assert sign_in(p, "nobody", "sunflower-42").status_code == 401


def test_member_can_browse_and_favorite_but_not_delete_or_manage(phone_world, items, mac):
    with phone(phone_world) as p:
        r = sign_in(p, "PARTNER", "sunflower-42")                                       # names aren't case-sensitive
        assert r.status_code == 200 and r.json()["role"] == "member"
        assert "httponly" in r.headers["set-cookie"].lower()
        assert p.get("/api/me").json()["name"] == "Partner"
        assert len(p.get("/api/items").json()["items"]) == len(items)
        some = items["beach.jpg"]["id"]
        assert p.get(f"/thumb/{some}").status_code == 200
        assert p.post("/api/favorites", json={"ids": [some]}).status_code == 200
        assert p.post("/api/favorites/remove", json={"ids": [some]}).status_code == 200
        for path, body in [("/api/trash", {"ids": [some]}), ("/api/trash/empty", {}), ("/api/restore", {"ids": [some]}),
                           ("/api/ml/run", {}), ("/api/people/merge", {"ids": [1, 2]}), ("/api/people/1/merge", {"into": 2}),
                           ("/api/users", {"name": "x", "password": "12345678"}), ("/api/sharing", {"on": False}),
                           ("/api/backup/approve-upload", {})]:
            assert p.post(path, json=body).status_code == 403, path
        for path in ("/api/trash", "/api/jobs", "/api/users"):
            assert p.get(path).status_code == 403, path
    assert mac.get("/api/trash").json()["items"] == []                               # nothing was trashed


def test_full_access_account_still_cant_manage_accounts_from_a_phone(phone_world):
    with phone(phone_world) as p:
        assert sign_in(p, "Me", "owner-pass-1").json()["role"] == "owner"
        assert p.get("/api/trash").status_code == 200
        assert p.get("/api/users").status_code == 403
        assert p.post("/api/users", json={"name": "x", "password": "12345678"}).status_code == 403


def test_passwords_and_sessions_are_stored_hashed(phone_world):
    with phone(phone_world) as p:
        token = sign_in(p, "Partner", "sunflower-42").cookies["pv_session"]
    con = sqlite3.connect(phone_world["data"] / "index.db")
    dump = "\n".join(con.iterdump())
    assert "sunflower-42" not in dump and token not in dump and "scrypt$" in dump


def test_another_website_cant_act_on_the_gallery(mac, items):
    r = mac.post("/api/trash", json={"ids": [items["beach.jpg"]["id"]]}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert mac.post("/api/trash/empty", headers={"Origin": "null"}).status_code == 403
    assert mac.get("/api/trash").json()["items"] == []


def test_signing_out_a_device_or_changing_the_password_locks_it_out(mac, phone_world):
    with phone(phone_world, "phone A") as p, phone(phone_world, "phone B") as p2:
        sign_in(p, "Partner", "sunflower-42")
        sign_in(p2, "Partner", "sunflower-42")
        partner = next(u for u in mac.get("/api/users").json()["users"] if u["name"] == "Partner")
        a = next(s for s in partner["sessions"] if s["device"] == "phone A")
        mac.post(f"/api/sessions/{a['id']}/revoke")
        assert p.get("/api/items").status_code == 401 and p2.get("/api/items").status_code == 200
        assert mac.post(f"/api/users/{partner['id']}/password", json={"password": "new-password-9"}).status_code == 200
        assert p.get("/api/items").status_code == 401 and p2.get("/api/items").status_code == 401
        assert sign_in(p, "Partner", "sunflower-42").status_code == 401
        assert sign_in(p, "Partner", "new-password-9").status_code == 200
        p.post("/api/logout")
        assert p.get("/api/items").status_code == 401


def test_switching_sharing_off_refuses_phones_again(mac, phone_world):
    with phone(phone_world) as p:
        sign_in(p, "Partner", "new-password-9")
        mac.post("/api/sharing", json={"on": False})
        assert p.get("/api/items").status_code == 403
        mac.post("/api/sharing", json={"on": True})
        assert p.get("/api/items").status_code == 200


@pytest.mark.parametrize("page", [{"world": "world", "viewport": {"width": 390, "height": 844}}], indirect=True)
def test_ui_phone_signs_in_and_sees_a_view_only_gallery(page, phone_world):
    page.goto(phone_world["url"] + "/")
    page.locator("#lName").wait_for()
    page.fill("#lName", "Partner")
    page.fill("#lPw", "wrong")
    page.click("text=Sign in")
    page.locator("#lErr", has_text="Wrong name or password").wait_for()
    shot(page, "phone-login")
    page.fill("#lPw", "new-password-9")
    page.click("text=Sign in")
    page.locator("#days .tile").first.wait_for()
    assert page.locator("#restoreBtn").is_hidden()
    assert page.locator('.side a[href="#/trash"]').is_hidden() and page.locator('.side a[href="#/sharing"]').is_hidden()
    shot(page, "phone-gallery")
    page.click("#acctBtn")                                                   # account menu, top right
    expect(page.locator("#acctMenu")).to_contain_text("View, favorite, albums")
    assert page.locator('#acctMenu a[href="#/sharing"]').count() == 0          # Sharing is for this Mac only
    page.locator("#signOut").click()
    page.locator("#lName").wait_for()


@pytest.mark.parametrize("page", [{"world": "world"}], indirect=True)
def test_ui_mac_sharing_page(page, mac):
    page.goto(page.base + "/#/sharing")
    page.locator("#shareOn").wait_for()
    assert page.locator("#shareOn").is_checked()
    page.fill("#aName", "Guest")
    page.fill("#aPw", "guest-pass-1")
    page.click("text=Add account")
    page.locator(".acct b", has_text="Guest").wait_for()
    shot(page, "sharing")
    assert any(u["name"] == "Guest" for u in mac.get("/api/users").json()["users"])
    page.locator("#shareOn").uncheck()
    page.locator("text=only this Mac can open the gallery").wait_for()
    assert mac.get("/api/sharing").json()["sharing"] is False


def test_too_many_wrong_passwords_are_slowed_down(phone_world, mac):
    mac.post("/api/sharing", json={"on": True})
    with phone(phone_world) as p:
        assert sign_in(p, "Guest", "guest-pass-1").status_code == 200              # starts the count afresh
        codes = [sign_in(p, "Guest", "nope-nope").status_code for _ in range(6)]
        assert codes[:5] == [401] * 5 and codes[5] == 429
        assert sign_in(p, "Guest", "guest-pass-1").status_code == 429               # even the right one, for a while


def test_http_port_refuses_redirects_and_serves_setup(monkeypatch):
    """Port 80: other devices are refused unless sharing is on; then it sends them to https, except the setup page."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    import auth, serve

    def call(client, path, sharing):
        monkeypatch.setattr(auth, "sharing_on", lambda con=None: sharing)
        sent = []

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(m):
            sent.append(m)
        scope = {"type": "http", "client": (client, 5000), "path": path, "raw_path": path.encode(), "query_string": b"",
                 "method": "GET", "headers": [], "scheme": "http", "server": ("photos.local", 80), "root_path": ""}
        with ThreadPoolExecutor(1) as ex:  # Playwright keeps an event loop running on this thread
            ex.submit(lambda: asyncio.run(serve.redirect(scope, receive, send))).result()
        start = next(m for m in sent if m["type"] == "http.response.start")
        return start["status"], dict(start["headers"]).get(b"location", b"").decode()

    assert call("127.0.0.1", "/x", False) == (308, "https://photos.local/x")
    assert call("192.168.1.50", "/x", False)[0] == 403
    assert call("8.8.8.8", "/x", True)[0] == 403                                   # not the home network
    assert call("192.168.1.50", "/api/items", True) == (308, "https://photos.local/api/items")
    assert call("192.168.1.50", "/setup", False)[0] == 403


def test_home_network_addresses():
    import auth
    assert all(auth.is_home_network(a) for a in ("192.168.1.5", "10.0.0.2", "172.16.4.1", "fe80::1", "fd00::5",
                                                 "::ffff:192.168.1.5"))
    assert not any(auth.is_home_network(a) for a in ("8.8.8.8", "2001:4860::1", "127.0.0.1", "", "nonsense"))
