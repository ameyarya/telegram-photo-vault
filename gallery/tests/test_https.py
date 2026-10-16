"""serve.py: HTTPS with a certificate valid for photo-vault.local, and http:// redirecting to it."""
import shutil
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from conftest import ROOT, free_port
from test_api import lan_ip, refused

pytestmark = pytest.mark.skipif(not shutil.which("mkcert") and not Path("/opt/homebrew/bin/mkcert").exists(),
                                reason="mkcert not installed")
MKCERT = shutil.which("mkcert") or "/opt/homebrew/bin/mkcert"


@pytest.fixture(scope="module")
def tls(world, tmp_path_factory):
    d = tmp_path_factory.mktemp("certs")
    cert, key = d / "c.pem", d / "k.pem"
    subprocess.run([MKCERT, "-cert-file", cert, "-key-file", key, "photo-vault.local", "localhost", "127.0.0.1"],
                   check=True, capture_output=True)
    ca = Path(subprocess.run([MKCERT, "-CAROOT"], capture_output=True, text=True).stdout.strip()) / "rootCA.pem"
    https_port, http_port = free_port(), free_port()
    env = {**world["env"], "GALLERY_CERT": str(cert), "GALLERY_KEY": str(key),
           "GALLERY_HTTPS_PORT": str(https_port), "GALLERY_HTTP_PORT": str(http_port)}
    proc = subprocess.Popen([sys.executable, "serve.py"], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            socket.create_connection(("127.0.0.1", https_port), timeout=0.2).close()
            socket.create_connection(("127.0.0.1", http_port), timeout=0.2).close()
            break
        except OSError:
            time.sleep(0.1)
    yield {"https": https_port, "http": http_port, "ca": ca}
    proc.terminate()
    proc.wait()


def test_certificate_is_valid_for_photo_vault_local(tls):
    ctx = ssl.create_default_context(cafile=str(tls["ca"]))
    with socket.create_connection(("127.0.0.1", tls["https"])) as raw:
        with ctx.wrap_socket(raw, server_hostname="photo-vault.local") as s:  # raises if the name or chain is wrong
            s.sendall(b"GET /api/status HTTP/1.1\r\nHost: photo-vault.local\r\nConnection: close\r\n\r\n")
            assert s.recv(64).startswith(b"HTTP/1.1 200")


def test_https_serves_the_gallery(tls):
    r = httpx.get(f"https://127.0.0.1:{tls['https']}/", verify=ssl.create_default_context(cafile=str(tls["ca"])))
    assert r.status_code == 200 and "Photo Vault" in r.text


def test_http_redirects_to_https_keeping_the_path(tls):
    r = httpx.get(f"http://127.0.0.1:{tls['http']}/thumb/1?x=2", follow_redirects=False)
    assert r.status_code == 308
    assert r.headers["location"] == f"https://photo-vault.local:{tls['https']}/thumb/1?x=2"


def test_both_ports_refuse_other_devices(tls):
    ip = lan_ip()
    if not ip or ip.startswith("127."):
        pytest.skip("no network interface")
    assert refused(f"http://{ip}:{tls['http']}/")
    assert refused(f"https://{ip}:{tls['https']}/api/status", verify=False)
