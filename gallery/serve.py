"""Run the gallery on https://photos.local (port 443) and redirect http:// (port 80) to it.

Certificates come from mkcert (see setup_https.sh). Other devices are refused unless sharing on the home Wi-Fi is
switched on (see auth.py). While it is, photos.local is announced on the network (Bonjour) so phones find this
Mac by name, and http:// also serves the phone setup page and certificate (a phone can't use https:// before that).
Older addresses (GALLERY_OLD_HOSTNAMES, e.g. photo-vault.local) are announced too and forward to the new one.
"""
import asyncio
import os
import subprocess

import uvicorn
from starlette.responses import RedirectResponse, Response

from config import BASE, HOSTNAME, OLD_HOSTNAMES

HOST = HOSTNAME
HTTPS_PORT = int(os.environ.get("GALLERY_HTTPS_PORT", 443))
HTTP_PORT = int(os.environ.get("GALLERY_HTTP_PORT", 80))
CERT = os.environ.get("GALLERY_CERT", str(BASE / "certs" / f"{HOST}.pem"))
KEY = os.environ.get("GALLERY_KEY", str(BASE / "certs" / f"{HOST}-key.pem"))
LOCAL = ("127.0.0.1", "::1", "::ffff:127.0.0.1")


async def redirect(scope, receive, send):
    """Tiny ASGI app: send every http:// request to the same path on https://."""
    if scope["type"] != "http":
        return
    import auth
    client = scope["client"][0] if scope["client"] else ""
    path = scope.get("raw_path", scope["path"].encode()).decode()
    if client in LOCAL:
        pass
    elif not (auth.is_home_network(client) and await asyncio.to_thread(auth.sharing_on)):
        return await Response("This gallery is only available on the Mac it runs on.", status_code=403)(scope, receive, send)
    elif scope["path"] in ("/setup", "/ca.pem"):  # before the phone trusts the certificate
        import server
        return await server.app(scope, receive, send)
    port = "" if HTTPS_PORT == 443 else f":{HTTPS_PORT}"
    query = scope.get("query_string", b"").decode()
    resp = RedirectResponse(f"https://{HOST}{port}{path}{'?' + query if query else ''}", status_code=308)
    await resp(scope, receive, send)


async def announce():
    """While sharing is on, tell the home network that photos.local (and any older address) is this Mac (follows IP
    changes)."""
    import auth
    procs, ip = [], None
    while True:
        want = await asyncio.to_thread(auth.sharing_on) and await asyncio.to_thread(auth.lan_address)
        if want != ip:
            for p in procs:
                p.terminate()
            procs = []
            if want:
                for k, name in enumerate([HOST] + OLD_HOSTNAMES):
                    procs.append(subprocess.Popen(["dns-sd", "-P", "Photos" if k == 0 else f"Photos ({name})", "_https._tcp",
                                                   "local", str(HTTPS_PORT), name, want],
                                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            ip = want
        await asyncio.sleep(20)


async def main():
    os.chdir(BASE)  # launchd can't start us inside ~/Documents, so it doesn't set the working folder
    # macOS lets normal users bind ports below 1024 only on the wildcard address
    https = uvicorn.Server(uvicorn.Config("server:app", host="0.0.0.0", port=HTTPS_PORT,
                                          ssl_certfile=CERT, ssl_keyfile=KEY, log_level="info"))
    http = uvicorn.Server(uvicorn.Config(redirect, host="0.0.0.0", port=HTTP_PORT, log_level="warning"))
    await asyncio.gather(https.serve(), http.serve(), announce())


if __name__ == "__main__":
    asyncio.run(main())
