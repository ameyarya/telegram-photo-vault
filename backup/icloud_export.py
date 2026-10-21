"""Bring iPhone photos from iCloud Photos onto the drive, so they're backed up like everything else.

The Mac's Photos app syncs your iPhone's photos from iCloud. This exports the new ones (full quality, downloaded from
iCloud if the Mac keeps only previews) into the Mac's inbox under that folder name; the watcher then sends them to
Telegram and moves them onto the drive whenever it's plugged in. The watcher runs it when iCloud brings new photos, if
ICLOUD_EXPORT_FOLDER is set in config.env:

    ICLOUD_EXPORT_FOLDER="iphone"          # folder on the drive
    ICLOUD_EXPORT_SINCE="2026-01-01"       # optional: skip photos taken before this (e.g. already on the drive)
    PHOTOS_LIBRARY="~/Pictures/Photos Library.photoslibrary"   # optional, this is the default

Needs:   pip install osxphotos   (in requirements-icloud.txt)
         Full Disk Access for the Python that runs auto-backup (System Settings -> Privacy & Security), because macOS
         protects the Photos library; and "Optimize Mac Storage" is fine: missing originals are downloaded.
By hand: .venv/bin/python backup/icloud_export.py [--dry-run] [--uuids FILE]   (FILE: Photos ids to backfill, one per
         line, e.g. photos taken before ICLOUD_EXPORT_SINCE that never reached the drive; see find_missing_icloud.py)

It remembers what it exported (icloud-export.db in the state folder), so each photo goes out once. It sets the file
date to when the photo was taken, includes Live Photo videos, and skips edited copies and photos shared with you by
others. Deleting a photo in Photos never deletes it from the drive.

Never overwrites: iPhone numbers wrap around (a new IMG_8132 next to an old, different IMG_8132), so osxphotos exports
into a hidden staging folder and this moves each photo in: a taken name gets " (2)" (" (3)" ...), with a photo and its
Live video always given the same number.
"""
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "gallery"))
from config import DB_PATH, DRIVE, INBOX, STATE  # noqa: E402  (reads config.env)

FOLDER = os.environ.get("ICLOUD_EXPORT_FOLDER", "").strip().strip("/")
SINCE = os.environ.get("ICLOUD_EXPORT_SINCE", "").strip()
LIBRARY = Path(os.environ.get("PHOTOS_LIBRARY") or "~/Pictures/Photos Library.photoslibrary").expanduser()
STAGE = INBOX / ".icloud-export-staging"   # on the Mac, hidden: the gallery and uploader skip it


def command(dry_run=False, uuids=None):
    cmd = [sys.executable, "-m", "osxphotos", "export", str(STAGE),
           "--library", str(LIBRARY),
           "--update", "--only-new", "--exportdb", str(STATE / "icloud-export.db"), "--ramdb",
           "--download-missing", "--use-photokit", "--retry", "3",   # PhotoKit: no need to drive the Photos app
           "--touch-file", "--skip-edited", "--not-shared",
           "--no-progress", "--verbose", "--timestamp"]
    if uuids:                                              # a backfill of exactly these photos (--uuids FILE)
        cmd += ["--uuid-from-file", str(uuids)]
    elif SINCE:
        cmd += ["--from-date", SINCE]
    if dry_run:
        cmd.append("--dry-run")
    return cmd


def stem_of(name):
    return (name.rsplit(".", 1)[0] if "." in name else name).lower()


def names_in_use(folder):
    """Every name in the folder, wherever it is now: on the drive (even if unplugged: the gallery knows them), in the
    inbox, or already in Telegram."""
    names = set()
    for d in (DRIVE / folder, INBOX / folder):
        if d.is_dir():
            names.update(os.listdir(d))
    if DB_PATH.exists():
        import sqlite3
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        names.update(p.rsplit("/", 1)[1] for (p,) in con.execute("SELECT path FROM media WHERE path LIKE ?", (folder + "/%",)))
        con.close()
    done = STATE / "done.log"
    if done.exists():
        names.update(p.rsplit("/", 1)[1] for p in done.read_text().splitlines() if p.startswith(folder + "/"))
    return names


def move_in(stage, dest, in_use=()):
    """Move exported files into dest, never reusing a name; files sharing a name (photo + Live video) move together."""
    taken = {stem_of(n) for n in [*os.listdir(dest), *in_use]}   # exFAT ignores upper/lower case
    groups = {}
    for n in sorted(os.listdir(stage)):
        if not n.startswith(".") and (stage / n).is_file():
            groups.setdefault(stem_of(n), []).append(n)
    moved = []
    for names in groups.values():
        k = 1
        while True:
            new = [n if k == 1 else (f"{n.rsplit('.', 1)[0]} ({k}).{n.rsplit('.', 1)[1]}" if "." in n else f"{n} ({k})")
                   for n in names]
            if stem_of(new[0]) not in taken and not any((dest / x).exists() for x in new):
                break
            k += 1
        for old, n in zip(names, new):
            os.rename(stage / old, dest / n)                  # same drive: instant, keeps the file's date
            moved.append(n)
        taken.add(stem_of(new[0]))
    return moved


def main():
    if not FOLDER:
        print("ICLOUD_EXPORT_FOLDER isn't set in config.env: nothing to do")
        return 0
    if not LIBRARY.exists():
        print(f"no Photos library at {LIBRARY}")
        return 1
    dry = "--dry-run" in sys.argv
    (INBOX / FOLDER).mkdir(parents=True, exist_ok=True)
    STAGE.mkdir(parents=True, exist_ok=True)
    uuids = sys.argv[sys.argv.index("--uuids") + 1] if "--uuids" in sys.argv else None
    code = subprocess.run(command(dry, uuids)).returncode
    if not dry:
        moved = move_in(STAGE, INBOX / FOLDER, names_in_use(FOLDER))   # also what an interrupted run left behind
        print(f"moved {len(moved)} files into {FOLDER}/" + (f", e.g. {moved[:3]}" if moved else ""), flush=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
