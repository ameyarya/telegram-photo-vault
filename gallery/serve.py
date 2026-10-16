"""Run the gallery on https://photo-vault.local (port 443) and redirect http:// (port 80) to it.

Certificates come from mkcert (see setup_https.sh). Both ports refuse other devices, like the app itself.
"""
import asyncio
import os

import uvicorn
from starlette.responses import RedirectResponse, Response

from config import BASE

HOST = os.environ.get("GALLERY_HOSTNAME", "photo-vault.local")
HTTPS_PORT = int(os.environ.get("GALLERY_HTTPS_PORT", 443))
HTTP_PORT = int(os.environ.get("GALLERY_HTTP_PORT", 80))
CERT = os.environ.get("GALLERY_CERT", str(BASE / "certs" / "photo-vault.local.pem"))
KEY = os.environ.get("GALLERY_KEY", str(BASE / "certs" / "photo-vault.local-key.pem"))
LOCAL = ("127.0.0.1", "::1", "::ffff:127.0.0.1")


async def redirect(scope, receive, send):
    """Tiny ASGI app: send every http:// request to the same path on https://."""
    if scope["type"] != "http":
        return
    if scope["client"] is None or scope["client"][0] not in LOCAL:
        resp = Response("This gallery is only available on the Mac it runs on.", status_code=403)
    else:
        port = "" if HTTPS_PORT == 443 else f":{HTTPS_PORT}"
        path = scope.get("raw_path", scope["path"].encode()).decode()
        query = scope.get("query_string", b"").decode()
        resp = RedirectResponse(f"https://{HOST}{port}{path}{'?' + query if query else ''}", status_code=308)
    await resp(scope, receive, send)


async def main():
    # macOS lets normal users bind ports below 1024 only on the wildcard address
    https = uvicorn.Server(uvicorn.Config("server:app", host="0.0.0.0", port=HTTPS_PORT,
                                          ssl_certfile=CERT, ssl_keyfile=KEY, log_level="info"))
    http = uvicorn.Server(uvicorn.Config(redirect, host="0.0.0.0", port=HTTP_PORT, log_level="warning"))
    await asyncio.gather(https.serve(), http.serve())


if __name__ == "__main__":
    asyncio.run(main())
