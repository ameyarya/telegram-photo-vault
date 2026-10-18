"""Who may use the gallery.

* This Mac (loopback) is always the owner and never needs to sign in.
* Other devices only when "Share on home Wi-Fi" is switched on (from this Mac), only from private home-network
  addresses, and only after signing in with an account made on this Mac. Accounts are managed only from this Mac.
* "View" accounts can browse, favorite, add to albums and name people. Trash, restore, merging people and running
  the analysis are for full-access accounts (and this Mac).
Passwords are stored as scrypt hashes; sessions are random tokens (only their SHA-256 is stored) and can be revoked.
"""
import hashlib
import hmac
import ipaddress
import os
import secrets
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from db import connect

router = APIRouter()
COOKIE = "pv_session"
SESSION_DAYS = 180
LOOPBACK = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
# test-only: treat this server's loopback clients as phones on the home network
LOOPBACK_IS_REMOTE = os.environ.get("GALLERY_LOOPBACK_IS_REMOTE") == "1"
MAX_FAILURES, FAILURE_WINDOW = 5, 15 * 60
_failures = {}  # ip or username -> [failure times]

# paths anyone may load (no data in them): the app shell, signing in, phone setup
PUBLIC = {"/", "/api/login", "/api/me", "/setup", "/ca.pem", "/favicon.ico"}
# what only full-access accounts (and this Mac) may do
OWNER_ONLY = ("/api/trash", "/api/restore", "/api/jobs", "/api/ml/run", "/api/people/merge", "/api/people/not-same",
              "/api/similar/dismiss", "/api/users", "/api/sessions", "/api/sharing")
OWNER_ONLY_RE = ("/merge", "/delete")  # /api/people/{id}/merge, /api/user-albums/{id}/delete


def is_loopback(request):
    return request.client is not None and request.client.host in LOOPBACK and not LOOPBACK_IS_REMOTE


def is_home_network(host):
    if LOOPBACK_IS_REMOTE and host in LOOPBACK:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return (ip.is_private or ip.is_link_local) and not ip.is_loopback


def sharing_on(con=None):
    con = con or connect()
    r = con.execute("SELECT value FROM settings WHERE key = 'share_lan'").fetchone()
    return bool(r and r[0] == "1")


# ---------------------------------------------------------------- passwords and sessions
def hash_password(pw, salt=None):
    salt = salt or secrets.token_bytes(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${h.hex()}"


def check_password(pw, stored):
    try:
        _, salt, h = stored.split("$")
    except (AttributeError, ValueError):
        hashlib.scrypt(pw.encode(), salt=b"x" * 16, n=2**14, r=8, p=1, dklen=32)  # same time either way
        return False
    return hmac.compare_digest(hash_password(pw, bytes.fromhex(salt)), stored)


def _token_hash(t):
    return hashlib.sha256(t.encode()).hexdigest()


def session_user(request):
    t = request.cookies.get(COOKIE)
    if not t:
        return None
    con = connect()
    r = con.execute("""SELECT u.id, u.name, u.role, s.token, s.last_seen FROM sessions s JOIN users u ON u.id = s.user_id
                       WHERE s.token = ? AND s.created > datetime('now', ?)""",
                    (_token_hash(t), f"-{SESSION_DAYS} days")).fetchone()
    if not r:
        return None
    if time.time() - _epoch(r["last_seen"]) > 60:
        con.execute("UPDATE sessions SET last_seen = CURRENT_TIMESTAMP, ip = ? WHERE token = ?",
                    (request.client.host if request.client else None, r["token"]))
        con.commit()
    return {"id": r["id"], "name": r["name"], "role": r["role"]}


def _epoch(sqlite_ts):
    try:
        return time.mktime(time.strptime(sqlite_ts, "%Y-%m-%d %H:%M:%S")) - time.timezone
    except (TypeError, ValueError):
        return 0


def owner_only(path):
    return path.startswith(OWNER_ONLY) or (path.startswith(("/api/people/", "/api/user-albums/"))
                                           and path.endswith(OWNER_ONLY_RE))


def gate(request):
    """None if the request may go ahead, else the response to send instead. Sets request.state.user."""
    path = request.url.path
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin and origin.split("://", 1)[-1] != request.headers.get("host"):
        return Response("Cross-site request refused.", status_code=403)  # another website can't act on the gallery
    if is_loopback(request):
        request.state.user = {"id": 0, "name": "This Mac", "role": "owner", "local": True}
        return None
    host = request.client.host if request.client else ""
    if not is_home_network(host) or not sharing_on():
        return Response("This gallery is only available on the Mac it runs on.", status_code=403)
    user = session_user(request)
    request.state.user = user
    if path in PUBLIC:
        return None
    if user is None:
        return JSONResponse({"detail": "Sign in first"}, status_code=401)
    if user["role"] != "owner" and request.method != "GET" and owner_only(path):
        return JSONResponse({"detail": "Your account can't do this; ask the gallery's owner."}, status_code=403)
    if user["role"] != "owner" and path.startswith(("/api/jobs", "/api/users", "/api/sessions", "/api/sharing", "/api/trash")):
        return JSONResponse({"detail": "Your account can't see this."}, status_code=403)
    return None


def _too_many(key):
    now = time.time()
    _failures[key] = [t for t in _failures.get(key, []) if now - t < FAILURE_WINDOW]
    return len(_failures[key]) >= MAX_FAILURES


# ---------------------------------------------------------------- sign in / out
class Login(BaseModel):
    name: str
    password: str


@router.post("/api/login")
def login(body: Login, request: Request):
    ip = request.client.host if request.client else "?"
    name = body.name.strip().lower()
    if _too_many(ip) or _too_many("user:" + name):
        raise HTTPException(429, "Too many wrong passwords. Try again in 15 minutes.")
    con = connect()
    u = con.execute("SELECT id, name, role, pw FROM users WHERE lower(name) = ?", (name,)).fetchone()
    if not check_password(body.password, u["pw"] if u else None) or not u:
        for k in (ip, "user:" + name):
            _failures.setdefault(k, []).append(time.time())
        raise HTTPException(401, "Wrong name or password")
    _failures.pop(ip, None)
    _failures.pop("user:" + name, None)
    token = secrets.token_urlsafe(32)
    con.execute("INSERT INTO sessions(token, user_id, ip, device) VALUES (?, ?, ?, ?)",
                (_token_hash(token), u["id"], ip, request.headers.get("user-agent", "")[:300]))
    con.commit()
    resp = JSONResponse({"ok": True, "name": u["name"], "role": u["role"]})
    resp.set_cookie(COOKIE, token, max_age=SESSION_DAYS * 86400, httponly=True, samesite="lax",
                    secure=request.url.scheme == "https")
    return resp


@router.post("/api/logout")
def logout(request: Request):
    t = request.cookies.get(COOKIE)
    if t:
        con = connect()
        con.execute("DELETE FROM sessions WHERE token = ?", (_token_hash(t),))
        con.commit()
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp


@router.get("/api/me")
def me(request: Request):
    u = getattr(request.state, "user", None)
    if u is None:
        raise HTTPException(401, "Sign in first")
    return u


# ---------------------------------------------------------------- managing accounts (this Mac only)
def _mac_only(request):
    if not is_loopback(request):
        raise HTTPException(403, "Accounts can only be managed on the Mac the gallery runs on")


class NewUser(BaseModel):
    name: str
    password: str
    role: str = "member"


class Password(BaseModel):
    password: str


def _check_new_password(pw):
    if len(pw) < 8:
        raise HTTPException(400, "Use a password of at least 8 characters")


@router.get("/api/users")
def users(request: Request):
    _mac_only(request)
    con = connect()
    rows = con.execute("SELECT id, name, role, created FROM users ORDER BY id").fetchall()
    sess = con.execute("SELECT rowid AS id, user_id, created, last_seen, ip, device FROM sessions ORDER BY last_seen DESC").fetchall()
    return {"users": [{**dict(u), "sessions": [dict(s) for s in sess if s["user_id"] == u["id"]]} for u in rows],
            "sharing": sharing_on(con)}


@router.post("/api/users")
def add_user(body: NewUser, request: Request):
    _mac_only(request)
    name = body.name.strip()
    if not name or len(name) > 40:
        raise HTTPException(400, "Pick a name (up to 40 characters)")
    if body.role not in ("member", "owner"):
        raise HTTPException(400, "Unknown access level")
    _check_new_password(body.password)
    con = connect()
    if con.execute("SELECT 1 FROM users WHERE lower(name) = lower(?)", (name,)).fetchone():
        raise HTTPException(400, "There's already an account with that name")
    uid = con.execute("INSERT INTO users(name, role, pw) VALUES (?, ?, ?)",
                      (name, body.role, hash_password(body.password))).lastrowid
    con.commit()
    return {"id": uid}


@router.post("/api/users/{uid}/password")
def set_password(uid: int, body: Password, request: Request):
    _mac_only(request)
    _check_new_password(body.password)
    con = connect()
    if not con.execute("UPDATE users SET pw = ? WHERE id = ?", (hash_password(body.password), uid)).rowcount:
        raise HTTPException(404, "No such account")
    con.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))  # signed out everywhere
    con.commit()
    return {"ok": True}


@router.post("/api/users/{uid}/delete")
def delete_user(uid: int, request: Request):
    _mac_only(request)
    con = connect()
    con.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
    con.execute("DELETE FROM users WHERE id = ?", (uid,))
    con.commit()
    return {"ok": True}


@router.post("/api/sessions/{sid}/revoke")
def revoke(sid: int, request: Request):
    _mac_only(request)
    con = connect()
    con.execute("DELETE FROM sessions WHERE rowid = ?", (sid,))
    con.commit()
    return {"ok": True}


class Sharing(BaseModel):
    on: bool


@router.post("/api/sharing")
def set_sharing(body: Sharing, request: Request):
    _mac_only(request)
    con = connect()
    if body.on and not con.execute("SELECT 1 FROM users").fetchone():
        raise HTTPException(400, "Add an account first")
    con.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('share_lan', ?)", ("1" if body.on else "0",))
    con.commit()
    return {"sharing": body.on, "address": lan_address()}


@router.get("/api/sharing")
def get_sharing(request: Request):
    _mac_only(request)
    return {"sharing": sharing_on(), "address": lan_address()}


def lan_address():
    import subprocess
    for iface in ("en0", "en1"):
        r = subprocess.run(["ipconfig", "getifaddr", iface], capture_output=True, text=True)
        if r.stdout.strip():
            return r.stdout.strip()
    return None


# ---------------------------------------------------------------- phone setup (no data, so no sign-in)
@router.get("/ca.pem")
def ca_certificate():
    """The public half of this Mac's mkcert certificate authority, so a phone can trust https://photo-vault.local."""
    import subprocess
    root = subprocess.run(["mkcert", "-CAROOT"], capture_output=True, text=True).stdout.strip()
    pem = os.path.join(root, "rootCA.pem") if root else ""
    if not pem or not os.path.isfile(pem):
        raise HTTPException(404, "No certificate authority on this Mac (run setup_https.sh)")
    return FileResponse(pem, media_type="application/x-x509-ca-cert", filename="photo-vault-ca.pem")


@router.get("/setup")
def setup_page():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "setup.html"), headers={"Cache-Control": "no-cache"})
