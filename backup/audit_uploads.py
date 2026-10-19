"""Check that EVERY file on the drive is safely in Telegram (not just a sample).

For each file the uploader is meant to back up (the same list it uses, all.txt):
  * a message in the channel has it (its caption is the drive path), or it's a known exact duplicate of one that does
  * that message's file is exactly the same size as the original (a cut-off upload shows up here)
Then it downloads a random sample back and compares it byte for byte (verify_uploads.py does the same, by hand).

    .venv/bin/python backup/audit_uploads.py [download_samples=10] [max_mb=100]

It reads the whole channel with the gallery's Telegram login, so it stops the gallery for those few minutes (if it's
running as a service) and starts it again. Writes the full result to audit-<date>.txt in the state folder.
"""
import asyncio
import hashlib
import os
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "gallery"))
sys.path.insert(0, str(HERE))
import tg  # noqa: E402
from config import DRIVE, DUPLICATES, STATE  # noqa: E402

LABEL = "local.photo-vault"


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def service(action):
    """Pause the gallery service (it holds the same Telegram login) and start it again afterwards."""
    plist = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
    dom = f"gui/{os.getuid()}"
    if action == "stop":
        return plist.exists() and subprocess.run(["launchctl", "bootout", f"{dom}/{LABEL}"], capture_output=True).returncode == 0
    subprocess.run(["launchctl", "bootstrap", dom, str(plist)], capture_output=True)


async def channel_files(client, cfg):
    """caption (drive path) -> (message id, size) for every file in the channel."""
    ch = await tg.channel_entity(client, cfg)
    out, n = {}, 0
    async for m in client.iter_messages(ch):
        n += 1
        if m.file and m.message:
            out.setdefault(m.message.strip(), (m.id, m.file.size))
        if n % 5000 == 0:
            print(f"  read {n} messages…", flush=True)
    return ch, out


async def main(n_samples=10, max_mb=100):
    import auto_upload                                     # the uploader's own list of what should be backed up
    files = auto_upload.list_files()
    dups = {}
    if DUPLICATES.exists():
        dups = dict(l.split("\t", 1) for l in DUPLICATES.read_text().splitlines() if "\t" in l)
    skip = set((STATE / "skip.txt").read_text().splitlines()) if (STATE / "skip.txt").exists() else set()
    client, cfg = tg.client_from_env()
    await client.connect()
    if not await client.is_user_authorized():
        sys.exit("Telegram login missing: run .venv/bin/python gallery/tg.py login")
    print(f"Reading the channel ({len(files)} files on the drive to check)…", flush=True)
    ch, tgf = await channel_files(client, cfg)
    import sqlite3
    from config import DB_PATH
    if DB_PATH.exists():                                   # files moved on the drive after upload: caption is the old path
        moved = dict(sqlite3.connect(DB_PATH).execute("SELECT old, new FROM path_moves").fetchall())
        for old, new in moved.items():
            if old in tgf:
                tgf.setdefault(new, tgf[old])
    ok, missing, wrong_size, via_dup, skipped = [], [], [], [], []
    for p in files:
        size = (DRIVE / p).stat().st_size
        hit = tgf.get(p)
        if hit is None and p in dups and dups[p] in tgf:
            hit = tgf[dups[p]]
            via_dup.append(p)
        if hit is None:
            (skipped if p in skip else missing).append(p)
        elif hit[1] != size:
            wrong_size.append((p, size, hit[1]))
        else:
            ok.append(p)
    pool = [p for p in ok if p in tgf and (DRIVE / p).stat().st_size <= max_mb * 1024**2]
    sample, bad_dl = random.sample(pool, min(n_samples, len(pool))), []
    with tempfile.TemporaryDirectory() as tmp:
        for i, p in enumerate(sample, 1):
            msg = await client.get_messages(ch, ids=tgf[p][0])
            got = await client.download_media(msg, file=os.path.join(tmp, "f"))
            same = sha256(got) == sha256(DRIVE / p)
            os.remove(got)
            print(f"  downloaded {i}/{len(sample)}: {'identical' if same else 'DIFFERENT'}  {p}", flush=True)
            if not same:
                bad_dl.append(p)
    await client.disconnect()
    lines = [f"Audit {time.strftime('%Y-%m-%d %H:%M')}: {len(files)} files on the drive",
             f"  in Telegram, same size:            {len(ok)}  (of which {len(via_dup)} via an identical copy)",
             f"  NOT in Telegram:                   {len(missing)}",
             f"  in Telegram but a DIFFERENT size:  {len(wrong_size)}",
             f"  not uploaded on purpose (skip.txt): {len(skipped)}  (e.g. over 2 GB: uploaded in parts from _split/)",
             f"  downloaded back and compared:      {len(sample)}, {len(sample) - len(bad_dl)} identical"]
    lines += ["", "NOT in Telegram:"] + missing + ["", "Different size (path, drive, telegram):"] + \
             [f"{p}\t{a}\t{b}" for p, a, b in wrong_size] + ["", "Skipped on purpose:"] + sorted(skipped) + \
             ["", "Downloaded and DIFFERENT:"] + bad_dl
    report = STATE / time.strftime("audit-%Y%m%d-%H%M.txt")
    report.write_text("\n".join(lines) + "\n")
    print("\n".join(lines[:6]))
    print(f"Full list: {report}")
    return 0 if not (missing or wrong_size or bad_dl) else 1


if __name__ == "__main__":
    args = [int(x) for x in sys.argv[1:3]]
    stopped = service("stop")
    if stopped:
        time.sleep(3)
    try:
        code = asyncio.run(main(*args))
    finally:
        if stopped:
            service("start")
    sys.exit(code)
