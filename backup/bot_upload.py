"""EXPERIMENTAL: upload alongside upload.sh as a Telegram bot, sending file parts in parallel.

Telegram limits each account's upload speed; a bot is a separate account, so its speed adds to yours.
Create a bot with @BotFather, add it to your channel as an admin that can post, put its token in config.env.

Run:         .venv/bin/python backup/bot_upload.py run [workers] [bot number]
             Bot 1 uses TG_BOT_TOKEN, bot 2 uses TG_BOT_TOKEN_2, and so on (all in config.env).
             Works backwards from the END of the upload list while upload.sh (your account) works forwards.
             Both record finished files in state/done.log and skip what the other did; the bot stops when
             they meet. Several bots can run at once: each claims a file before uploading it (state/claims.txt).
Speed test:  .venv/bin/python backup/bot_upload.py speedtest [MB] [workers]   (random data, deleted afterwards)
"""
import asyncio
import fcntl
import os
import traceback
import random
import sys
import time
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.tl.functions.upload import SaveBigFilePartRequest
from telethon.tl.types import InputFileBig, PeerChannel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gallery"))
from config import DRIVE, STATE, load_env  # noqa: E402

PART = 512 * 1024  # Telegram's maximum part size


def bot_client(name="bot", token_var="TG_BOT_TOKEN"):
    cfg = load_env()
    token = os.environ.get(token_var, "").strip()
    if not (cfg["api_id"] and cfg["api_hash"] and token):
        sys.exit(f"Missing TG_API_ID / TG_API_HASH / {token_var} in config.env")
    client = TelegramClient(str(STATE / name), cfg["api_id"], cfg["api_hash"])
    return client, token, cfg


async def upload_parallel(client, path, workers=8, progress=None):
    """Upload one file (up to 2 GB) with `workers` parts in flight; returns an InputFileBig to send."""
    size = os.path.getsize(path)
    parts = (size + PART - 1) // PART
    file_id = random.getrandbits(63)
    queue = asyncio.Queue()
    for i in range(parts):
        queue.put_nowait(i)
    sent = 0

    async def worker():
        nonlocal sent
        with open(path, "rb") as f:
            while not queue.empty():
                i = queue.get_nowait()
                f.seek(i * PART)
                data = f.read(PART)
                for attempt in range(5):
                    try:
                        await client(SaveBigFilePartRequest(file_id, i, parts, data))
                        break
                    except Exception:
                        if attempt == 4:
                            raise
                        await asyncio.sleep(2 ** attempt)
                sent += len(data)
                if progress:
                    progress(sent, size)

    await asyncio.gather(*(worker() for _ in range(workers)))
    return InputFileBig(file_id, parts, os.path.basename(path))


async def speedtest(mb=512, workers=8):
    client, token, cfg = bot_client()
    await client.start(bot_token=token)
    channel = await client.get_entity(PeerChannel(cfg["channel_id"]))
    tmp = STATE / f"speedtest-{mb}MB.bin"
    with open(tmp, "wb") as f:
        f.write(os.urandom(mb * 1024 * 1024))
    try:
        t = time.time()
        last = [t]

        def show(done, total):
            if time.time() - last[0] > 5:
                last[0] = time.time()
                print(f"  {done / 1e6:.0f}/{total / 1e6:.0f} MB  {done / 1e6 / (time.time() - t):.1f} MB/s", flush=True)

        f = await upload_parallel(client, tmp, workers, show)
        msg = await client.send_file(channel, f, caption="speed test (will be deleted)", force_document=True)
        secs = time.time() - t
        print(f"RESULT: {mb} MB in {secs:.0f}s = {mb * 1.048576 / secs:.1f} MB/s with {workers} parallel parts", flush=True)
        await client.delete_messages(channel, [msg.id])
        print("test message deleted", flush=True)
    finally:
        tmp.unlink(missing_ok=True)
        await client.disconnect()


MARGIN = 60  # stop this many files short of the account uploader to avoid both taking the same batch


CLAIMS = STATE / "claims.txt"  # "pid<TAB>path" for files a bot is uploading right now


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def claim(failed):
    """Atomically pick the last file nobody has done or claimed, and claim it for this process."""
    with open(CLAIMS, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        rows = [l.split("\t", 1) for l in f.read().splitlines() if "\t" in l]
        rows = [(pid, p) for pid, p in rows if alive(int(pid))]  # drop claims of bots that died
        taken = {p for _, p in rows}
        todo = [p for p in remaining() if p not in failed and p not in taken]
        pick = todo[-1] if len(todo) > MARGIN else None
        if pick:
            rows.append((str(os.getpid()), pick))
        f.seek(0)
        f.truncate()
        f.write("".join(f"{pid}\t{p}\n" for pid, p in rows))
        return pick, len(todo)


def release(rel):
    with open(CLAIMS, "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        keep = [l for l in f.read().splitlines() if l != f"{os.getpid()}\t{rel}"]
        f.seek(0)
        f.truncate()
        f.write("".join(l + "\n" for l in keep))


def remaining():
    done = set((STATE / "done.log").read_text().splitlines())
    skip = set((STATE / "skip.txt").read_text().splitlines()) if (STATE / "skip.txt").exists() else set()
    return [p for p in (STATE / "all.txt").read_text().splitlines() if p not in done and p not in skip]


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S  ") + msg, flush=True)


async def run(workers=8, idx=1):
    client, token, cfg = bot_client("bot" if idx == 1 else f"bot{idx}",
                                    "TG_BOT_TOKEN" if idx == 1 else f"TG_BOT_TOKEN_{idx}")
    await client.start(bot_token=token)
    me = await client.get_me()
    log(f"bot {idx} (@{me.username}) started, pid {os.getpid()}")
    channel = await client.get_entity(PeerChannel(cfg["channel_id"]))
    failed, n, sent_bytes, t0 = set(), 0, 0, time.time()
    while True:
        if not DRIVE.is_dir():
            log("drive disconnected; stopping")
            break
        rel, left = claim(failed)
        if rel is None:
            log(f"met the account uploader ({left} left for it); stopping")
            break
        try:
            f = await upload_parallel(client, DRIVE / rel, workers)
            msg = await client.send_file(channel, f, caption=rel, force_document=True)
        except FloodWaitError as e:
            release(rel)
            log(f"Telegram asked to wait {e.seconds}s")
            await asyncio.sleep(e.seconds + 5)
            continue
        except Exception:
            release(rel)
            failed.add(rel)
            log(f"FAILED {rel}: {traceback.format_exc(limit=1).strip().splitlines()[-1]}")
            await asyncio.sleep(30)
            continue
        with open(STATE / "bot_uploads.tsv", "a") as u:  # path, message id, bot: for verify_uploads.py
            u.write(f"{rel}\t{msg.id}\t{idx}\n")
        with open(STATE / "done.log", "a") as d:
            d.write(rel + "\n")
        release(rel)
        n += 1
        sent_bytes += os.path.getsize(DRIVE / rel)
        if n % 20 == 0:
            log(f"bot {idx} uploaded {n} files, {sent_bytes / 1e9:.1f} GB, {sent_bytes / 1e6 / (time.time() - t0):.1f} MB/s")
    await client.disconnect()


if __name__ == "__main__":
    if sys.argv[1:2] == ["run"]:
        asyncio.run(run(int(sys.argv[2]) if len(sys.argv) > 2 else 8, int(sys.argv[3]) if len(sys.argv) > 3 else 1))
        sys.exit()
    if sys.argv[1:2] == ["speedtest"]:
        asyncio.run(speedtest(int(sys.argv[2]) if len(sys.argv) > 2 else 512,
                              int(sys.argv[3]) if len(sys.argv) > 3 else 8))
    else:
        print(__doc__)
