"""Settings, read from ../config.env (and overridable by environment variables)."""
import os
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOT = BASE.parent


def _read_env(path):
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.split(" #", 1)[0].strip().strip('"').strip("'")
            out[k.strip()] = v
    return out


for _k, _v in _read_env(Path(os.environ.get("VAULT_CONFIG", ROOT / "config.env"))).items():
    os.environ.setdefault(_k, _v)

# launchd starts services with a bare PATH; ffmpeg/exiftool usually live in Homebrew
for _d in ("/opt/homebrew/bin", "/usr/local/bin"):
    if _d not in os.environ.get("PATH", "").split(":"):
        os.environ["PATH"] = _d + ":" + os.environ.get("PATH", "/usr/bin:/bin")

DRIVE = Path(os.environ.get("GALLERY_DRIVE") or os.environ.get("DRIVE") or "/nonexistent")
STATE = Path(os.environ.get("STATE_DIR") or ROOT / "state").expanduser()
DATA = Path(os.environ.get("GALLERY_DATA") or STATE / "gallery")  # tests point this at a temp dir
DB_PATH = DATA / "index.db"
THUMBS = DATA / "thumbs"
CACHE = DATA / "cache"          # originals downloaded from Telegram + converted previews
TIMESTAMPS = Path(os.environ.get("GALLERY_TIMESTAMPS") or STATE / "timestamps.tsv")   # from backup/save_timestamps.sh
RESTORE_DIR = Path(os.environ.get("GALLERY_RESTORE_DIR") or Path.home() / "Downloads" / "Photo Vault Restore")
MODELS = Path(os.environ.get("GALLERY_MODELS") or STATE / "models")                    # ml.py download puts them here
CACHE_MAX_BYTES = 10 * 1024**3  # trimmed oldest-first when exceeded
# New photos (phones, iCloud) wait here on the Mac, laid out like the drive (inbox/<folder>/...), so they're backed up
# and shown right away even with the drive unplugged; they move onto the drive once it's plugged in.
INBOX = Path(os.environ.get("INBOX_DIR") or STATE / "inbox").expanduser()
# Folders kept only in Telegram: never copied to the drive, and removed from the Mac once safely uploaded (the gallery
# shows them from Telegram). E.g. an old account's photos you don't want on the drive. Separate with "|".
CLOUD_ONLY = {f.strip().strip("/") for f in os.environ.get("CLOUD_ONLY_FOLDERS", "").split("|") if f.strip()}
# The gallery's address on this Mac and the home network; older addresses keep working by forwarding to it.
HOSTNAME = os.environ.get("GALLERY_HOSTNAME", "photos.local")
OLD_HOSTNAMES = [h.strip() for h in os.environ.get("GALLERY_OLD_HOSTNAMES", "photo-vault.local").split(",") if h.strip()]
SESSION = str(STATE / "gallery")  # Telethon session file (gallery.session)
TELEGRAM = os.environ.get("GALLERY_TELEGRAM", "1") != "0"
# exact duplicates the uploader skips: "dup_path<TAB>kept_path" (written by backup/find_duplicates.py)
DUPLICATES = Path(os.environ.get("GALLERY_DUPLICATES") or STATE / "duplicates.tsv")

SYSTEM_DIRS = {"$RECYCLE.BIN", "System Volume Information", "lost+found"}
IMAGE_EXT = {"jpg", "jpeg", "heic", "heif", "png", "gif", "webp", "arw", "cr2", "cr3", "nef", "dng", "tif", "tiff"}
VIDEO_EXT = {"mov", "mp4", "m4v", "avi", "mts", "3gp", "mkv"}
BROWSER_IMAGE_EXT = {"jpg", "jpeg", "png", "gif", "webp"}  # everything else is converted to JPEG for viewing


def roots():
    """Where photos are right now: the drive (if plugged in) and the Mac's inbox."""
    return [r for r in (DRIVE, INBOX) if r.is_dir()]


def local_path(rel):
    """The file for a drive path: on the drive, or still in the inbox (the drive path if neither has it)."""
    for r in (DRIVE, INBOX):
        p = r / rel
        if p.exists():
            return p
    return DRIVE / rel


def folders():
    """Top-level folders to show: FOLDERS from config.env, else every folder on the drive and in the inbox.
    Plus _split (big videos)."""
    names = [f for f in os.environ.get("FOLDERS", "").split("|") if f.strip()]
    if not names:
        names = sorted({p.name for r in roots() for p in r.iterdir()
                        if p.is_dir() and not p.name.startswith((".", "_")) and p.name not in SYSTEM_DIRS})
    return names + ["_split"]


def load_env():
    return {
        "api_id": int(os.environ.get("TG_API_ID", "0") or 0),
        "api_hash": os.environ.get("TG_API_HASH", ""),
        "channel_id": int(os.environ.get("TG_CHANNEL_ID", "0") or 0),
    }
