"""upload.sh, find_duplicates.py and save_timestamps.sh against a fake drive and a fake tdl (no Telegram needed)."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKUP = Path(__file__).resolve().parent.parent
ROOT = BACKUP.parent

FAKE_TDL = r'''#!/bin/zsh
# Records what would be uploaded. Fails once if $FAIL_ONCE exists, to test retries.
[[ "$1" == "up" ]] || exit 0
if [[ -n "$FAIL_ONCE" && -f "$FAIL_ONCE" ]]; then rm "$FAIL_ONCE"; exit 1; fi
if [[ -n "$HANG_ONCE" && -f "$HANG_ONCE" ]]; then rm "$HANG_ONCE"; sleep 60; fi   # a stalled upload
caption=""; files=()
while (( $# )); do
  case "$1" in
    -p) files+=("$2"); shift 2 ;;
    --caption) caption="$2"; shift 2 ;;
    -c) echo "$2" > "$TDL_LOG.chat"; shift 2 ;;
    *) shift ;;
  esac
done
prefix="${caption#*\"}"; prefix="${prefix%%\"*}"          # the drive path inside replace(FilePath, "...", "")
for f in "${files[@]}"; do echo "${f:${#prefix}}" >> "$TDL_LOG"; done
'''


def write(p: Path, data=b"x" * 1000):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


@pytest.fixture
def vault(tmp_path):
    drive, state = tmp_path / "My Drive", tmp_path / "state"
    write(drive / "Wedding/day 1/a.jpg", b"A" * 5000)
    write(drive / "Wedding/day 1/clip.mp4", b"V" * 9000)
    write(drive / "Wedding/day 1/a.AAE")                             # sidecar: skipped
    write(drive / "Wedding/day 1/.DS_Store")                         # hidden: skipped
    write(drive / "Wedding/day 1/empty.jpg", b"")                    # empty: skipped
    write(drive / "Trips/beach.heic", b"B" * 3000)
    write(drive / "Trips/copy of beach/beach.heic", b"B" * 3000)     # exact duplicate of Trips/beach.heic
    write(drive / "Trips/other/beach.heic", b"C" * 3000)             # same name+size, different photo
    write(drive / "Phone/Photos Library.photoslibrary/originals/1/IMG_1.heic", b"P" * 2000)  # kept
    write(drive / "Phone/Photos Library.photoslibrary/database/Photos.sqlite")              # skipped
    big = drive / "Trips/huge.mov"
    big.parent.mkdir(parents=True, exist_ok=True)
    with open(big, "wb") as f:                                       # sparse 3 GB: too big, split instead
        f.truncate(3 * 1024**3)
    write(drive / "_split/Trips/huge_part00.mov", b"S" * 4000)
    write(drive / "_split/Trips/huge_part01.mov", b"S" * 4001)
    (drive / "_split/.complete").touch()
    write(drive / "NotListed/x.jpg")                                 # not in FOLDERS: skipped

    tdl = tmp_path / "tdl"
    tdl.write_text(FAKE_TDL)
    tdl.chmod(0o755)
    config = tmp_path / "config.env"
    config.write_text(f'DRIVE="{drive}"\nFOLDERS="Wedding|Trips|Phone"\nTG_CHANNEL_ID=12345\nSTATE_DIR="{state}"\n')
    env = {**os.environ, "VAULT_CONFIG": str(config), "TDL": str(tdl), "TDL_LOG": str(tmp_path / "uploaded.txt"),
           "RETRY_SLEEP": "0", "BATCH": "3"}

    def run(*args, script="upload.sh"):
        # run it the way the README says: from the repo folder, with a relative path
        r = subprocess.run(["zsh", f"backup/{script}", *args], cwd=ROOT, env=env, capture_output=True, text=True,
                           timeout=120)
        assert r.returncode == 0, r.stdout + r.stderr
        return r.stdout

    def uploaded():
        f = tmp_path / "uploaded.txt"
        return f.read_text().splitlines() if f.exists() else []

    return {"drive": drive, "state": state, "env": env, "run": run, "uploaded": uploaded, "tmp": tmp_path}


EXPECTED = ["Wedding/day 1/a.jpg", "Wedding/day 1/clip.mp4",
            "Trips/beach.heic", "Trips/copy of beach/beach.heic", "Trips/other/beach.heic",
            "Phone/Photos Library.photoslibrary/originals/1/IMG_1.heic",
            "_split/Trips/huge_part00.mov", "_split/Trips/huge_part01.mov"]


def test_watchdog_kills_a_stalled_upload_and_it_is_retried(vault):
    flag = vault["tmp"] / "hang-once"
    flag.touch()
    vault["env"].update(HANG_ONCE=str(flag), WATCHDOG_WINDOW="2")
    vault["run"]()
    log = (vault["state"] / "upload.log").read_text()
    assert "watchdog: upload stalled" in log
    assert sorted(vault["uploaded"]()) == sorted(EXPECTED)          # the stalled batch went up on retry


def test_list_follows_rules_and_folder_order(vault):
    vault["run"]("--list-only")
    assert (vault["state"] / "all.txt").read_text().splitlines() == EXPECTED
    assert vault["uploaded"]() == []


def test_uploads_everything_once_with_path_captions(vault):
    vault["run"]()
    assert sorted(vault["uploaded"]()) == sorted(EXPECTED)          # captions = paths relative to the drive
    assert (vault["tmp"] / "uploaded.txt.chat").read_text().strip() == "12345"
    assert sorted((vault["state"] / "done.log").read_text().splitlines()) == sorted(EXPECTED)


def test_rerun_resumes_and_uploads_nothing_new(vault):
    vault["run"]()
    vault["run"]()
    assert len(vault["uploaded"]()) == len(EXPECTED)


def test_failed_batch_is_retried(vault):
    flag = vault["tmp"] / "fail-once"
    flag.touch()
    vault["env"]["FAIL_ONCE"] = str(flag)
    out = vault["run"]()
    assert "batch failed" in out
    assert sorted(vault["uploaded"]()) == sorted(EXPECTED)          # the failed batch went up in pass 2


def test_files_done_elsewhere_are_not_reuploaded(vault):
    """The bot uploader records its files in done.log too; upload.sh must skip them even mid-pass."""
    vault["run"]("--list-only")
    (vault["state"] / "done.log").write_text("Trips/beach.heic\n_split/Trips/huge_part01.mov\n")
    vault["run"]()
    assert "Trips/beach.heic" not in vault["uploaded"]()
    assert "_split/Trips/huge_part01.mov" not in vault["uploaded"]()
    assert len(vault["uploaded"]()) == len(EXPECTED) - 2


def test_split_parts_wait_until_complete(vault):
    (vault["drive"] / "_split/.complete").unlink()
    vault["run"]("--list-only")
    assert not any(p.startswith("_split/") for p in (vault["state"] / "all.txt").read_text().splitlines())


def test_duplicates_are_confirmed_by_content_then_skipped(vault):
    vault["run"]("--list-only")
    r = subprocess.run([sys.executable, str(BACKUP / "find_duplicates.py")], env=vault["env"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (vault["state"] / "duplicates.tsv").read_text() == "Trips/copy of beach/beach.heic\tTrips/beach.heic\n"
    assert (vault["state"] / "skip.txt").read_text() == "Trips/copy of beach/beach.heic\n"  # not "other": differs
    vault["run"]()
    assert "Trips/copy of beach/beach.heic" not in vault["uploaded"]()
    assert "Trips/other/beach.heic" in vault["uploaded"]()


def test_duplicate_keeps_the_copy_already_uploaded(vault):
    vault["run"]("--list-only")
    (vault["state"] / "done.log").write_text("Trips/copy of beach/beach.heic\n")
    subprocess.run([sys.executable, str(BACKUP / "find_duplicates.py")], env=vault["env"], check=True,
                   capture_output=True)
    assert (vault["state"] / "duplicates.tsv").read_text() == "Trips/beach.heic\tTrips/copy of beach/beach.heic\n"


def test_timestamps_manifest(vault):
    vault["run"]("--list-only")
    out = vault["run"](script="save_timestamps.sh")
    rows = (vault["state"] / "timestamps.tsv").read_text().splitlines()
    assert rows[0] == "path\tsize\tcreated\tmodified"
    assert len(rows) == 1 + len(EXPECTED)
    path, size, created, modified = rows[1].split("\t")
    assert path == EXPECTED[0] and size == "5000" and len(created) == len(modified) == 19
    assert "Saved 8 files" in out


def test_empty_folders_setting_means_every_top_folder(vault):
    cfg = Path(vault["env"]["VAULT_CONFIG"])
    cfg.write_text(cfg.read_text().replace('FOLDERS="Wedding|Trips|Phone"', 'FOLDERS=""'))
    vault["run"]("--list-only")
    listed = (vault["state"] / "all.txt").read_text().splitlines()
    assert "NotListed/x.jpg" in listed and "Wedding/day 1/a.jpg" in listed
