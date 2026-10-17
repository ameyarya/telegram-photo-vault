"""Against a fake Telegram channel: syncing captions, streaming with the drive unplugged, and restoring."""
import hashlib
import os
import time
from datetime import datetime

import httpx
import pytest

from conftest import free_port, start_server


@pytest.fixture(scope="module")
def capi(cloud):
    with httpx.Client(base_url=cloud["url"], timeout=60) as c:
        yield c


@pytest.fixture(scope="module")
def citems(capi):
    return {i["name"]: i for i in capi.get("/api/items").json()["items"]}


def sha(p):
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def test_sync_links_every_uploaded_file(capi, cloud):
    s = capi.get("/api/status").json()
    assert s["telegram"] is True and s["uploaded"] == 9 and s["total"] == 10
    missing = capi.get("/api/items", params={"filter": "notbackedup"}).json()["items"]
    assert [m["name"] for m in missing] == ["screenshot.png"]


@pytest.fixture(scope="module")
def cloud_unplugged(cloud):
    env = {**cloud["env"], "GALLERY_DRIVE": str(cloud["drive"]) + "-missing"}
    proc, url = start_server(env, free_port())
    with httpx.Client(base_url=url, timeout=60) as c:
        yield c
    proc.terminate()
    proc.wait()


def test_streams_from_telegram_when_drive_is_unplugged(cloud_unplugged, citems, cloud):
    it = citems["clip.mp4"]
    whole = (cloud["drive"] / "family/wedding/day 1/clip.mp4").read_bytes()
    assert cloud_unplugged.get(f"/original/{it['id']}").content == whole
    r = cloud_unplugged.get(f"/original/{it['id']}", headers={"Range": "bytes=1000-1999"})
    assert r.status_code == 206 and r.content == whole[1000:2000]
    assert r.headers["content-range"] == f"bytes 1000-1999/{len(whole)}"
    heic = cloud_unplugged.get(f"/view/{citems['IMG_0001.heic']['id']}")   # downloaded, converted for viewing
    assert heic.status_code == 200 and heic.content[:2] == b"\xff\xd8"
    r = cloud_unplugged.get(f"/view/{citems['screenshot.png']['id']}")
    assert r.status_code == 503 and "not in Telegram yet" in r.json()["detail"]


def wait_job(capi, job, timeout=60):
    for _ in range(timeout * 10):
        j = capi.get(f"/api/jobs/{job}").json()
        if j["status"] != "running":
            return j
        time.sleep(0.1)
    raise AssertionError("restore didn't finish")


def test_restore_folder_brings_back_exact_files_with_original_dates(capi, cloud):
    r = capi.post("/api/restore", json={"album": "family"}).json()
    assert r["files"] == 6 and r["not_backed_up"] == 0
    job = wait_job(capi, r["job"])
    assert job["status"] == "done" and job["done"] == 6 and job["detail"]["errors"] == []
    assert job["detail"]["verified"] == 6 and job["detail"]["dated"] == 6
    dest = r["dest"]
    restored = sorted(os.path.relpath(os.path.join(d, f), dest) for d, _, fs in os.walk(dest) for f in fs)
    assert restored == sorted([
        "family/wedding/group.jpg", "family/wedding/day 1/portrait.jpg", "family/wedding/day 1/clip.mp4",
        "family/wedding/day 2/IMG_0001.heic", "_split/family/videos/film_part00.mp4", "_split/family/videos/film_part01.mp4"])
    for rel in restored:                                   # byte-for-byte the originals
        assert sha(os.path.join(dest, rel)) == sha(cloud["drive"] / rel), rel
    st = os.stat(os.path.join(dest, "family/wedding/group.jpg"))   # dates from the timestamp manifest
    assert datetime.fromtimestamp(st.st_birthtime) == datetime(2020, 1, 2, 3, 4, 5)
    assert datetime.fromtimestamp(st.st_mtime) == datetime(2021, 2, 3, 4, 5, 6)
    assert not [f for _, _, fs in os.walk(dest) for f in fs if f.endswith(".part")]


def test_restore_counts_what_isnt_backed_up(capi):
    r = capi.post("/api/restore", json={"album": "iphone"})
    assert r.status_code == 400 and "backed up" in r.json()["detail"]
    r = capi.post("/api/restore", json={}).json()          # everything: 9 backed up, 1 not
    assert r["files"] == 9 and r["not_backed_up"] == 1
    assert wait_job(capi, r["job"])["status"] == "done"


def test_restore_selection_search_and_label(capi, citems):
    ids = [citems["beach.jpg"]["id"], citems["pier.jpg"]["id"]]
    r = capi.post("/api/restore", json={"ids": ids, "label": "Picked/by me"}).json()
    assert r["files"] == 2 and "Picked-by me" in r["dest"]
    j = wait_job(capi, r["job"])
    assert j["done"] == 2 and os.path.exists(os.path.join(r["dest"], "vacation/pier.jpg"))
    r = capi.post("/api/restore", json={"q": "sony"}).json()
    assert r["files"] == 1


def test_restore_skips_files_already_there_and_checks_them(tmp_path, cloud, citems, monkeypatch):
    """Restoring into a folder that already has a file (e.g. an interrupted restore) skips it, but still verifies it."""
    import asyncio, json, sqlite3, types
    import restore as R
    import server
    monkeypatch.setattr(R, "DRIVE", cloud["drive"])
    con = sqlite3.connect(cloud["data"] / "index.db"); con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM media WHERE name IN ('beach.jpg', 'sunset.jpg')")]
    dest = tmp_path / "out"
    (dest / "vacation").mkdir(parents=True)
    (dest / "vacation/beach.jpg").write_bytes((cloud["drive"] / "vacation/beach.jpg").read_bytes())  # already done
    calls = []

    async def fake_bytes(row, start, end):
        calls.append(row["path"])
        yield (cloud["drive"] / row["path"]).read_bytes()

    monkeypatch.setattr(server, "tg_bytes", fake_bytes, raising=False)
    jobs = {}
    monkeypatch.setattr(R, "_update", lambda job_id, **f: jobs.update(f))
    asyncio.run(R._run(1, rows, dest, True))
    assert calls == ["vacation/sunset.jpg"]                   # beach was skipped
    detail = json.loads(jobs["detail"])
    assert detail["skipped"] == 1 and detail["verified"] == 2 and detail["errors"] == []
    (dest / "vacation/beach.jpg").write_bytes(b"corrupt")      # a wrong file of a different size is re-downloaded
    calls.clear()
    asyncio.run(R._run(1, rows, dest, True))
    assert sorted(calls) == ["vacation/beach.jpg"]
    assert sha(dest / "vacation/beach.jpg") == sha(cloud["drive"] / "vacation/beach.jpg")


def test_jobs_api_errors(capi):
    assert capi.get("/api/jobs/999999").status_code == 404
    assert capi.post("/api/jobs/999999/cancel").status_code == 400


def test_restore_needs_telegram(api):
    r = api.post("/api/restore", json={})
    assert r.status_code == 503


def test_timestamp_manifest_parsing(tmp_path):
    import restore as R
    p = tmp_path / "t.tsv"
    p.write_text("path\tsize\tcreated\tmodified\na/b.jpg\t5\t2020-01-02T03:04:05\t2021-02-03T04:05:06\n"
                 "bad line\nc.jpg\t1\tnot-a-date\t2021-01-01T00:00:00\n")
    stamps = R.load_timestamps(p)
    assert list(stamps) == ["a/b.jpg"]
    assert stamps["a/b.jpg"] == (datetime(2020, 1, 2, 3, 4, 5).timestamp(), datetime(2021, 2, 3, 4, 5, 6).timestamp())
    assert R.load_timestamps(tmp_path / "missing.tsv") == {}
