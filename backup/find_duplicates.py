"""Find exact duplicate files so the uploader skips the extra copies.

Run after `upload.sh --list-only` (or any upload run): it reads state/all.txt.
Candidates share a file name (case-insensitive) and size; each is then confirmed byte-for-byte with SHA-256,
so two different photos that happen to share a name and size are never skipped.
Writes in the state folder:
  duplicates.tsv  dup_path <TAB> kept_path   every confirmed extra copy and the copy that is kept
  skip.txt        dup_path                   extra copies not uploaded yet (upload.sh skips these)
The kept copy is the one already uploaded if any, else the first in upload order. Nothing on the drive changes.
    .venv/bin/python backup/find_duplicates.py
"""
import hashlib
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "gallery"))
from config import DRIVE, STATE  # noqa: E402


def sha256(rel):
    with open(DRIVE / rel, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def main():
    paths = (STATE / "all.txt").read_text().splitlines()
    order = {p: i for i, p in enumerate(paths)}
    done_log = STATE / "done.log"
    uploaded = set(done_log.read_text().splitlines()) if done_log.exists() else set()
    size = {p: (DRIVE / p).stat().st_size for p in paths}
    groups = defaultdict(list)
    for p in paths:
        groups[(os.path.basename(p).lower(), size[p])].append(p)
    cands = [g for g in groups.values() if len(g) > 1]
    total = sum(len(g) for g in cands)
    print(f"{len(cands)} name+size groups, {total} files to checksum", flush=True)

    dup_rows, done = [], 0
    for g in cands:
        by_hash = defaultdict(list)
        for p in g:
            by_hash[sha256(p)].append(p)
            done += 1
            if done % 200 == 0:
                print(f"checksummed {done}/{total}", flush=True)
        for same in by_hash.values():
            if len(same) > 1:
                kept = min(same, key=lambda p: (p not in uploaded, order[p]))
                dup_rows += [(p, kept) for p in same if p != kept]

    (STATE / "duplicates.tsv").write_text("".join(f"{d}\t{k}\n" for d, k in dup_rows))
    skip = [d for d, _ in dup_rows if d not in uploaded]
    (STATE / "skip.txt").write_text("".join(p + "\n" for p in skip))
    print(f"confirmed {len(dup_rows)} duplicate copies; {len(skip)} not uploaded yet will be skipped "
          f"({sum(size[p] for p in skip) / 1e9:.1f} GB saved)")


if __name__ == "__main__":
    main()
