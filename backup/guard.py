"""Guardrails that run before anything is uploaded, so moving or copying photos never re-uploads them.

Telegram knows each file by the path it was uploaded from. Rename a folder in Finder, move photos to another folder,
or copy a folder, and every file looks new. Before uploading, each new file is checked against what's already in
Telegram (same file name, same size):
  * the old copy is gone from the drive and the file kept its date  -> it was MOVED/RENAMED: the backup records and the
    gallery (faces, favorites, albums ...) follow it to its new path; nothing is uploaded
  * another file already in Telegram has identical bytes, under any name -> it's a COPY: skipped as a duplicate, and
    moved to the gallery's Trash once it's indexed (trash.sync_drive), so it never shows twice or stays on the drive
And if a drive scan still finds more than HOLD new files at once (a big surprise), they're held back and you're asked
on the Backup page (watcher.py notifies you); phone/iCloud photos in the inbox keep flowing meanwhile.
"""
import collections
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

from config import DB_PATH, DRIVE, INBOX, STATE, local_path
from build_index import album_and_name

HOLD = int(os.environ.get("UPLOAD_HOLD") or 1000)
HOLD_FILE = STATE / "upload-hold.json"           # what's being held (shown on the Backup page)
APPROVED = STATE / ".upload-approved"            # you said "upload them": the next round goes ahead


def _same_bytes(a, b):
    if a.stat().st_size != b.stat().st_size:
        return False
    ha, hb = hashlib.sha256(), hashlib.sha256()
    with open(a, "rb") as fa, open(b, "rb") as fb:
        for chunk_a, chunk_b in zip(iter(lambda: fa.read(1 << 20), b""), iter(lambda: fb.read(1 << 20), b"")):
            ha.update(chunk_a)
            hb.update(chunk_b)
    return ha.digest() == hb.digest()


def _append(name, lines):
    if lines:
        with open(STATE / name, "a") as f:
            f.write("".join(l + "\n" for l in lines))


def reconcile(todo, log=print):
    """Takes the files about to be uploaded; returns the ones that really are new. Moves and copies of files already
    in Telegram are recorded as such instead (see the module doc)."""
    if not todo or not DB_PATH.exists():
        return todo
    con = sqlite3.connect(DB_PATH, timeout=60)
    known, same_size = collections.defaultdict(list), collections.defaultdict(list)
    for mid, path, name, size, mtime, msg in con.execute(
            "SELECT id, path, name, size, mtime, msg_id FROM media WHERE msg_id IS NOT NULL"):
        known[(name.lower(), size)].append((mid, path, mtime, msg))
        same_size[size].append(path)
    new, moves, copies = [], [], []
    for rel in todo:
        p = local_path(rel)
        try:
            st = p.stat()
        except OSError:
            new.append(rel)
            continue
        cands = [c for c in known.get((rel.rsplit("/", 1)[-1].lower(), st.st_size), []) if c[1] != rel]
        there = [c for c in cands if local_path(c[1]).exists()]
        gone = [c for c in cands if not local_path(c[1]).exists() and abs((c[2] or 0) - st.st_mtime) <= 2
                and DRIVE.is_dir()]                       # with the drive unplugged everything on it looks "gone"
        twin = next((c[1] for c in there if _same_bytes(p, local_path(c[1]))), None)
        if twin is None and len(gone) != 1:           # the same photo under another name
            others = [o for o in same_size.get(st.st_size, []) if o != rel and local_path(o).exists()]
            twin = next((o for o in others[:20] if _same_bytes(p, local_path(o))), None)
        if twin is not None:
            copies.append((rel, twin))
        elif len(gone) == 1:
            moves.append((rel, gone[0]))
        else:
            new.append(rel)
    if moves:
        with con:
            for rel, (mid, old, _, msg) in moves:
                for (dup,) in con.execute("SELECT id FROM media WHERE path = ? AND id != ?", (rel, mid)).fetchall():
                    for t in ("faces", "clip", "similar", "favorites", "user_album_items", "ml_done", "date_fixes", "trash"):
                        try:
                            con.execute(f"DELETE FROM {t} WHERE media_id = ?", (dup,))
                        except sqlite3.OperationalError:
                            pass
                    con.execute("DELETE FROM media WHERE id = ?", (dup,))   # the indexer saw it as new: it isn't
                album, name = album_and_name(rel)
                con.execute("UPDATE media SET path = ?, album = ?, name = ? WHERE id = ?", (rel, album, name, mid))
                con.execute("INSERT OR REPLACE INTO tg_msgs(path, msg_id) VALUES (?, ?)", (rel, msg))
                con.execute("INSERT OR REPLACE INTO path_moves(old, new) VALUES (?, ?)", (old, rel))
        _append("done.log", [rel for rel, _ in moves])
        tops = collections.Counter(f"{c[1].split('/')[0]} -> {rel.split('/')[0]}" for rel, c in moves)
        log(f"guard: {len(moves)} files were moved or renamed (e.g. a folder renamed in Finder), not uploaded again: "
            + ", ".join(f"{k} ({v})" for k, v in tops.most_common(5)))
    if copies:
        _append("duplicates.tsv", [f"{rel}\t{kept}" for rel, kept in copies])
        _append("skip.txt", [rel for rel, _ in copies])
        _append("copies-to-trash.tsv", [f"{rel}\t{kept}" for rel, kept in copies])
        log(f"guard: {len(copies)} identical copies of photos already in Telegram skipped")
    con.close()
    return new


def hold_big_surprises(todo, log=print):
    """Holds back drive files when a scan finds more than HOLD new ones at once, until you approve on the Backup page.
    Returns (to upload now, held)."""
    drive = [f for f in todo if not (INBOX / f).exists()]
    if len(drive) <= HOLD or APPROVED.exists():
        if APPROVED.exists() and drive:
            APPROVED.unlink()
            log(f"guard: uploading {len(drive)} new drive files, as you approved")
        HOLD_FILE.unlink(missing_ok=True)
        return todo, []
    folders = collections.Counter(f.split("/")[0] for f in drive)
    HOLD_FILE.write_text(json.dumps({"files": len(drive), "folders": dict(folders.most_common()),
                                     "since": time.strftime("%Y-%m-%d %H:%M")}))
    log(f"guard: holding {len(drive)} new files on the drive until you approve them on the Backup page: "
        + ", ".join(f"{k} ({v})" for k, v in folders.most_common(5)))
    held = set(drive)
    return [f for f in todo if f not in held], drive
