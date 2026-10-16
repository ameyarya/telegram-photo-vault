import sqlite3
from config import DB_PATH, DUPLICATES

SCHEMA = """
CREATE TABLE IF NOT EXISTS media (
  id        INTEGER PRIMARY KEY,
  path      TEXT UNIQUE NOT NULL,   -- path relative to the drive root, same as the Telegram caption
  album     TEXT NOT NULL,          -- folder shown in the gallery (split parts map back to the original folder)
  name      TEXT NOT NULL,
  kind      TEXT NOT NULL,          -- 'image' | 'video'
  ext       TEXT NOT NULL,
  taken_at  TEXT,                   -- ISO local time
  date_src  TEXT,                   -- 'exif' | 'file'
  size      INTEGER,
  mtime     REAL,
  width     INTEGER,
  height    INTEGER,
  duration  REAL,
  thumb     INTEGER DEFAULT 0,      -- 1 = thumbnail generated, -1 = failed
  msg_id    INTEGER                 -- message id in the Telegram backup channel (NULL = not uploaded yet)
);
CREATE INDEX IF NOT EXISTS media_album ON media(album, taken_at);
CREATE INDEX IF NOT EXISTS media_taken ON media(taken_at);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS tg_msgs (path TEXT PRIMARY KEY, msg_id INTEGER NOT NULL);  -- every upload seen in the channel
"""


_ready = set()


def connect(timeout=30):
    """Open the index. Schema setup (which can need a write lock) runs once per process."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=timeout, check_same_thread=False)
    con.row_factory = sqlite3.Row
    if str(DB_PATH) not in _ready:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
        cols = {r["name"] for r in con.execute("PRAGMA table_info(media)")}
        if "orient" not in cols:  # 1 = stored sideways (EXIF orientation 5-8 / video rotation 90/270): swap w/h for display
            con.execute("ALTER TABLE media ADD COLUMN orient INTEGER")
        _ready.add(str(DB_PATH))
    return con


def link_uploads(con):
    """Attach Telegram message ids to gallery items (also covers items indexed after their upload was seen).
    A skipped duplicate shares the message of the identical copy that was uploaded."""
    n = con.execute("""UPDATE media SET msg_id = (SELECT msg_id FROM tg_msgs t WHERE t.path = media.path)
                       WHERE msg_id IS NULL AND path IN (SELECT path FROM tg_msgs)""").rowcount
    if DUPLICATES.exists():
        con.execute("CREATE TEMP TABLE IF NOT EXISTS dups (path TEXT PRIMARY KEY, kept TEXT)")
        con.execute("DELETE FROM dups")
        con.executemany("INSERT OR REPLACE INTO dups VALUES (?, ?)",
                        [l.split("\t", 1) for l in DUPLICATES.read_text().splitlines() if "\t" in l])
        n += con.execute("""UPDATE media SET msg_id = (SELECT t.msg_id FROM dups d JOIN tg_msgs t ON t.path = d.kept
                                                       WHERE d.path = media.path)
                            WHERE msg_id IS NULL AND path IN (SELECT d.path FROM dups d JOIN tg_msgs t ON t.path = d.kept)""").rowcount
    con.commit()
    return n
