"""autobackup.py (runs daily and when a drive is mounted) with a fake uploader, indexer and analysis."""
import os
import subprocess
import time
from pathlib import Path

import pytest

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
SCRIPT = Path(__file__).resolve().parent.parent / "autobackup.py"


@pytest.fixture
def ab(tmp_path):
    state, drive = tmp_path / "state", tmp_path / "My Drive"
    (state / "gallery").mkdir(parents=True)
    drive.mkdir()
    (state / "done.log").write_text("a\nb\n")
    upload = tmp_path / "fake_upload.sh"   # pretends to upload 3 files; records its arguments
    upload.write_text('#!/bin/zsh\necho "$@" > "$STATE_DIR/upload_args"\nprintf "c\\nd\\ne\\n" >> "$STATE_DIR/done.log"\n'
                      'exit ${FAKE_UPLOAD_EXIT:-0}\n')
    index = tmp_path / "fake_index.sh"
    index.write_text('#!/bin/zsh\ntouch "$STATE_DIR/indexed"\n')
    ml = tmp_path / "fake_ml.sh"
    ml.write_text('#!/bin/zsh\ntouch "$STATE_DIR/analysed"\n')
    env = {**os.environ, "AUTOBACKUP_STATE": str(state), "AUTOBACKUP_DRIVE": str(drive),
           "VAULT_CONFIG": str(tmp_path / "no-config.env"),
           "AUTOBACKUP_UPLOAD": f"zsh {upload}", "AUTOBACKUP_INDEX": f"zsh {index}", "AUTOBACKUP_NOTIFY": "0",
           "AUTOBACKUP_DELAY": "0", "AUTOBACKUP_RUNNING_PATTERN": "no-such-process-xyz",
           "AUTOBACKUP_INDEX_PATTERN": "no-such-indexer-xyz", "STATE_DIR": str(state),
           "AUTOBACKUP_ML": f"zsh {ml}", "AUTOBACKUP_ML_PATTERN": "no-such-analysis-xyz",
           "AUTOBACKUP_SETTLE": "0", "AUTOBACKUP_USED": "echo 1000", "AUTOBACKUP_MAX_WAIT": "0"}

    def run(**extra):
        r = subprocess.run([sys.executable, str(SCRIPT)], env={**env, **extra}, capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        return (state / "autobackup.log").read_text() if (state / "autobackup.log").exists() else ""

    return {"run": run, "state": state, "drive": drive, "env": env}


def test_other_drives_are_ignored(ab, tmp_path):
    ab["run"](AUTOBACKUP_DRIVE=str(tmp_path / "not-mounted"))
    assert not (ab["state"] / "upload_args").exists() and not (ab["state"] / "indexed").exists()


def test_photo_drive_triggers_upload_then_index(ab):
    log = ab["run"]()
    assert (ab["state"] / "upload_args").exists()
    assert (ab["state"] / "indexed").exists()
    assert "Backup finished: 3 new files uploaded" in log
    assert not (ab["state"] / ".autobackup.lock").exists()          # lock released


def test_once_per_backup_day(ab):
    ab["run"]()
    (ab["state"] / "upload_args").unlink()
    log = ab["run"]()
    assert "skipped: already backed up today (since 3 AM)" in log                # plugging the drive in again: no rerun
    assert not (ab["state"] / "upload_args").exists()


def test_the_3am_run_goes_ahead_after_an_evening_run(ab):
    """Regression: a backup at 8:37 PM made the 3 AM run skip itself (it counted 20 hours instead of days)."""
    import autobackup
    t = time.mktime((2026, 10, 20, 3, 0, 24, 0, 0, -1))                       # 3:00 AM
    evening = time.mktime((2026, 10, 19, 20, 37, 0, 0, 0, -1))
    assert evening < autobackup.day_start(t)                                    # yesterday evening: a new day began
    assert autobackup.day_start(time.mktime((2026, 10, 20, 2, 59, 0, 0, 0, -1))) < evening   # 2:59 AM: same day
    (ab["state"] / ".autobackup.last").write_text(str(int(time.time()) - 86400 * 2))
    ab["run"]()
    assert (ab["state"] / "upload_args").exists()


def test_doesnt_start_while_an_upload_runs(ab):
    sleeper = subprocess.Popen(["sleep", "30"])
    try:
        log = ab["run"](AUTOBACKUP_RUNNING_PATTERN="sleep 30")
    finally:
        sleeper.kill()
    assert "skipped: an upload is already running" in log
    assert not (ab["state"] / "upload_args").exists()


def test_doesnt_run_twice_at_once(ab):
    lock = ab["state"] / ".autobackup.lock"
    lock.mkdir()
    sleeper = subprocess.Popen(["sleep", "30"])
    (lock / "pid").write_text(str(sleeper.pid))
    try:
        log = ab["run"]()
    finally:
        sleeper.kill()
    assert "skipped: auto-backup already running" in log
    assert not (ab["state"] / "upload_args").exists()


def test_stale_lock_from_a_crash_is_taken_over(ab):
    lock = ab["state"] / ".autobackup.lock"
    lock.mkdir()
    (lock / "pid").write_text("999999")            # no such process
    ab["run"]()
    assert (ab["state"] / "upload_args").exists()


def test_reports_an_interrupted_upload(ab):
    log = ab["run"](FAKE_UPLOAD_EXIT="1")
    assert "Backup stopped early (3 new files uploaded)" in log
    assert (ab["state"] / "indexed").exists()      # still indexes what did get uploaded


def test_doesnt_start_a_second_indexer(ab):
    sleeper = subprocess.Popen(["sleep", "31"])
    try:
        log = ab["run"](AUTOBACKUP_INDEX_PATTERN="sleep 31")
    finally:
        sleeper.kill()
    assert (ab["state"] / "upload_args").exists()        # still uploads
    assert not (ab["state"] / "indexed").exists()        # but leaves indexing to the one already running
    assert "gallery index already updating; skipped" in log


def test_waits_while_photos_are_still_being_copied(ab, tmp_path):
    """E.g. Image Capture importing onto the drive: the drive's used space changes, so nothing is uploaded yet."""
    counter = tmp_path / "used"
    counter.write_text("1000")
    grow = f'n=$(cat {counter}); echo $((n + 50000)) > {counter}; echo $n'   # 50 MB more each time it's asked
    log = ab["run"](AUTOBACKUP_USED=grow)
    assert "waiting: files are still being copied" in log
    assert not (ab["state"] / "upload_args").exists() and not (ab["state"] / ".autobackup.last").exists()
    ab["run"](AUTOBACKUP_USED="echo 1000")          # copying finished: next check backs it up
    assert (ab["state"] / "upload_args").exists()


def test_small_changes_dont_count_as_copying(ab, tmp_path):
    counter = tmp_path / "used"
    counter.write_text("1000")
    ab["run"](AUTOBACKUP_USED=f'n=$(cat {counter}); echo $((n + 100)) > {counter}; echo $n')  # 100 KB: indexes etc.
    assert (ab["state"] / "upload_args").exists()


def test_nothing_new_means_no_analysis_and_no_notification(ab, tmp_path):
    quiet = tmp_path / "quiet_upload.sh"
    quiet.write_text('#!/bin/zsh\necho "$@" > "$STATE_DIR/upload_args"\n')
    log = ab["run"](AUTOBACKUP_UPLOAD=f"zsh {quiet}")
    assert "nothing new" in log and "Backup finished" not in log
    assert (ab["state"] / "indexed").exists() and not (ab["state"] / "analysed").exists()


def test_doesnt_start_a_second_analysis(ab):
    sleeper = subprocess.Popen(["sleep", "32"])
    try:
        log = ab["run"](AUTOBACKUP_ML_PATTERN="sleep 32")
    finally:
        sleeper.kill()
    assert not (ab["state"] / "analysed").exists() and "photo analysis already running" in log


def test_runs_daily_and_when_plugged_in():
    plist = (SCRIPT.parent / "install_autobackup.sh").read_text()
    assert "<key>StartOnMount</key><true/>" in plist and "<key>Hour</key><integer>3</integer>" in plist
    assert "StartInterval" not in plist                                          # not every 30 minutes any more


def test_keeps_checking_while_copying_then_backs_up(ab, tmp_path):
    """Copying still going when the daily run starts: it checks again a few times instead of waiting a whole day."""
    counter = tmp_path / "used"
    counter.write_text("1000")
    # grows on the first two checks, then stays put (the copy finished)
    script = f'n=$(cat {counter}); c=$(cat {counter}.calls 2>/dev/null || echo 0); echo $((c + 1)) > {counter}.calls; ' \
             f'[ $c -lt 2 ] && echo $((n + 50000)) > {counter}; echo $n'
    log = ab["run"](AUTOBACKUP_USED=script, AUTOBACKUP_MAX_WAIT="10", AUTOBACKUP_RECHECK="0")
    assert "checking again every few minutes" in log and "looking for new photos" in log
    assert (ab["state"] / "upload_args").exists()


def test_icloud_export_runs_first_when_configured(ab, tmp_path):
    fake = tmp_path / "fake_icloud.sh"
    fake.write_text('#!/bin/zsh\ntouch "$STATE_DIR/icloud_exported"\n')
    log = ab["run"](AUTOBACKUP_ICLOUD=f"zsh {fake}")
    assert (ab["state"] / "icloud_exported").exists() and "from iCloud Photos" in log
    assert (ab["state"] / "upload_args").exists()                                # then the normal backup


def test_no_icloud_export_unless_configured(ab):
    ab["run"]()
    assert not (ab["state"] / "icloud_exported").exists()
