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
-- everything below lives only on this Mac; nothing here changes the drive or Telegram
CREATE TABLE IF NOT EXISTS favorites (media_id INTEGER PRIMARY KEY, added TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS user_albums (id INTEGER PRIMARY KEY, name TEXT NOT NULL, created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS user_album_items (album_id INTEGER NOT NULL, media_id INTEGER NOT NULL,
  added TEXT DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (album_id, media_id));
CREATE TABLE IF NOT EXISTS clip (media_id INTEGER PRIMARY KEY, emb BLOB NOT NULL);       -- image embeddings (smart search)
CREATE TABLE IF NOT EXISTS faces (id INTEGER PRIMARY KEY, media_id INTEGER NOT NULL, x REAL, y REAL, w REAL, h REAL,
  score REAL, emb BLOB NOT NULL, person INTEGER);                                         -- box as fractions of the image
CREATE INDEX IF NOT EXISTS faces_media ON faces(media_id);
CREATE INDEX IF NOT EXISTS faces_person ON faces(person);
CREATE TABLE IF NOT EXISTS people (id INTEGER PRIMARY KEY, name TEXT, cover_face INTEGER, n INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS similar (group_id INTEGER NOT NULL, media_id INTEGER NOT NULL, PRIMARY KEY (group_id, media_id));
CREATE TABLE IF NOT EXISTS ml_done (media_id INTEGER NOT NULL, task TEXT NOT NULL, PRIMARY KEY (media_id, task));
-- Similar-shots "trash": hidden in the gallery and moved to <drive>/.photo-vault-trash (Telegram copy kept)
CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'member',
  pw TEXT NOT NULL, created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS sessions (token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created TEXT DEFAULT CURRENT_TIMESTAMP,
  last_seen TEXT DEFAULT CURRENT_TIMESTAMP, ip TEXT, device TEXT);
CREATE TABLE IF NOT EXISTS date_fixes (media_id INTEGER PRIMARY KEY, orig_taken_at TEXT, orig_src TEXT,
  fixed_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS trash (media_id INTEGER PRIMARY KEY, trashed_at TEXT DEFAULT CURRENT_TIMESTAMP,
  moved INTEGER DEFAULT 0, emptied INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS similar_dismissed (group_key TEXT PRIMARY KEY);         -- "these aren't duplicates"
CREATE TABLE IF NOT EXISTS people_not_same (a INTEGER NOT NULL, b INTEGER NOT NULL, PRIMARY KEY (a, b));  -- a < b
CREATE TABLE IF NOT EXISTS jobs (id INTEGER PRIMARY KEY, kind TEXT, status TEXT, done INTEGER DEFAULT 0, total INTEGER DEFAULT 0,
  detail TEXT, started TEXT DEFAULT CURRENT_TIMESTAMP, finished TEXT);
"""

# photo details read from EXIF (camera, settings, location); exif2 = 1 once read
EXTRA_COLUMNS = {"live_id": "INTEGER", "live_of": "INTEGER",  # Live Photo: still -> its motion clip, clip -> its still
                 "orient": "INTEGER", "make": "TEXT", "model": "TEXT", "lens": "TEXT", "iso": "INTEGER",
                 "fnumber": "REAL", "exposure": "REAL", "focal": "REAL", "lat": "REAL", "lon": "REAL", "exif2": "INTEGER"}


_ready = set()


def connect(timeout=30):
    """Open the index. Schema setup (which can need a write lock) runs once per process."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH, timeout=timeout, check_same_thread=False)
    con.row_factory = sqlite3.Row
    if str(DB_PATH) not in _ready:
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
        pcols = {r["name"] for r in con.execute("PRAGMA table_info(people)")}
        if "confirmed" not in pcols:  # 1 = you named or merged them: regrouping keeps all their faces together
            con.execute("ALTER TABLE people ADD COLUMN confirmed INTEGER DEFAULT 0")
        cols = {r["name"] for r in con.execute("PRAGMA table_info(media)")}
        # orient: 1 = stored sideways (EXIF orientation 5-8 / video rotation 90/270): swap w/h for display
        for col, typ in EXTRA_COLUMNS.items():
            if col not in cols:
                con.execute(f"ALTER TABLE media ADD COLUMN {col} {typ}")
        con.commit()
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
