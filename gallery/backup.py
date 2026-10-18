"""Backup dashboard: what's backed up, per folder and per year, problems, and whether uploads are running."""
import subprocess
import time

from fastapi import APIRouter

from config import DRIVE, STATE
from db import connect

router = APIRouter()


def _lines(name):
    p = STATE / name
    try:
        return [l for l in p.read_text(errors="replace").splitlines() if l.strip()]
    except OSError:
        return []


def _running(pattern):
    try:
        return subprocess.run(["pgrep", "-f", pattern], capture_output=True).returncode == 0
    except OSError:
        return False


@router.get("/api/backup")
def backup_status():
    import server  # the live Telegram/sync state
    con = connect()
    total = dict(con.execute("""SELECT count(*) files, coalesce(sum(size), 0) bytes, sum(msg_id IS NOT NULL) backed_up,
                                       coalesce(sum(CASE WHEN msg_id IS NOT NULL THEN size END), 0) backed_up_bytes,
                                       sum(kind = 'video') videos, sum(date_src = 'file' AND live_of IS NULL) undated
                                FROM media""").fetchone())
    by_folder = [dict(r) for r in con.execute("""
        SELECT CASE WHEN instr(album, '/') > 0 THEN substr(album, 1, instr(album, '/') - 1) ELSE album END folder,
               count(*) files, sum(size) bytes, sum(msg_id IS NOT NULL) backed_up
        FROM media GROUP BY folder ORDER BY bytes DESC""")]
    by_year = [dict(r) for r in con.execute("""
        SELECT substr(taken_at, 1, 4) year, count(*) files, sum(size) bytes, sum(msg_id IS NOT NULL) backed_up
        FROM media GROUP BY year ORDER BY year""")]
    missing = [dict(r) for r in con.execute("""SELECT id, path, size FROM media WHERE msg_id IS NULL
                                              ORDER BY path LIMIT 50""")]
    upload_log = _lines("upload.log")
    failed = [l for l in upload_log if "batch failed" in l]
    return {
        "total": total, "by_folder": by_folder, "by_year": by_year, "not_backed_up_sample": missing,
        "upload_list": len(_lines("all.txt")), "uploaded_files": len(_lines("done.log")),
        "skipped_duplicates": len(_lines("duplicates.tsv")), "empty_files": len(_lines("empty_files.txt")),
        "failed_batches": len(failed), "last_failures": failed[-5:],
        "upload_log_tail": upload_log[-8:], "bot_log_tail": _lines("bot.log")[-5:],
        "uploader_running": _running("upload.sh"), "bots_running": _running("bot_upload.py run"),
        "drive": DRIVE.is_dir(), "telegram": server.state["client"] is not None,
        "telegram_error": server.state["tg_error"], "last_sync": server.state["last_sync"], "now": time.time(),
    }
