"""Download a sample of uploaded files back from the channel and compare them with the drive, byte for byte.

Checks, per file: the message exists, its caption is the drive path, the file name and size match,
and the SHA-256 of the downloaded copy equals the original's.
Uses the gallery's Telegram login and index (run the gallery and let it sync first).
Usage (stop the gallery service first: it holds the same Telegram session):
    .venv/bin/python backup/verify_uploads.py [bot_samples] [account_samples] [max_mb]
"""
import asyncio
import hashlib
import os
import random
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gallery"))
import tg  # noqa: E402  (reuses the gallery's logged-in Telegram session)
from config import DB_PATH, DRIVE, STATE  # noqa: E402


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def samples(n_bot, n_acct, max_bytes):
    bot = []
    if (STATE / "bot_uploads.tsv").exists():
        for line in (STATE / "bot_uploads.tsv").read_text().splitlines():
            path, msg_id, idx = line.split("\t")
            if (DRIVE / path).stat().st_size <= max_bytes:
                bot.append((path, int(msg_id), f"bot {idx}"))
    con = sqlite3.connect(DB_PATH)
    bot_paths = {p for p, _, _ in bot}
    acct = [(p, m, "account") for p, m in con.execute("SELECT path, msg_id FROM tg_msgs")
            if p not in bot_paths and (DRIVE / p).exists() and (DRIVE / p).stat().st_size <= max_bytes]
    random.shuffle(bot)
    random.shuffle(acct)
    return bot[:n_bot] + acct[:n_acct]


async def main(n_bot=5, n_acct=3, max_mb=200):
    client, cfg = tg.client_from_env()
    await client.start()
    channel = await tg.channel_entity(client, cfg)
    ok = bad = 0
    for path, msg_id, who in samples(n_bot, n_acct, max_mb * 1024 * 1024):
        local = DRIVE / path
        msg = await client.get_messages(channel, ids=msg_id)
        problems = []
        if not msg or not msg.file:
            problems.append("message missing")
        else:
            if (msg.message or "").strip() != path:
                problems.append(f"caption is {msg.message!r}")
            if msg.file.name != local.name:
                problems.append(f"file name is {msg.file.name!r}")
            if msg.file.size != local.stat().st_size:
                problems.append(f"size {msg.file.size} vs {local.stat().st_size}")
            if not problems:
                with tempfile.TemporaryDirectory() as d:
                    got = await client.download_media(msg, file=os.path.join(d, "f"))
                    if sha256(got) != sha256(local):
                        problems.append("CONTENT DIFFERS")
        status = "OK  " if not problems else "FAIL"
        ok, bad = (ok + 1, bad) if not problems else (ok, bad + 1)
        print(f"{status} [{who:7}] msg {msg_id:6}  {local.stat().st_size / 1e6:7.1f} MB  {path}"
              + ("" if not problems else "  -> " + "; ".join(problems)), flush=True)
    print(f"\n{ok} identical, {bad} problems")
    await client.disconnect()


if __name__ == "__main__":
    a = [int(x) for x in sys.argv[1:4]]
    asyncio.run(main(*a))
