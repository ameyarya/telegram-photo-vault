"""The first-run guide: pick your photos folder, build the gallery, start it. Run by ./setup.sh.

    ./vault setup                          the guided version (asks a few questions)
    ./vault setup --folder ~/Pictures      skip the questions you already know the answer to
    ./vault telegram                       add (or change) the Telegram backup later

Flags: --folder PATH  --telegram | --no-telegram  --no-index  --no-service  --yes (accept every default)
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = ROOT / "gallery"
CONFIG = Path(os.environ.get("VAULT_CONFIG") or ROOT / "config.env")
PY = sys.executable
MEDIA = {"jpg", "jpeg", "heic", "heif", "png", "gif", "webp", "arw", "cr2", "cr3", "nef", "dng", "tif", "tiff",
         "mov", "mp4", "m4v", "avi", "mts", "3gp", "mkv"}


# ---------------------------------------------------------------- config.env
def read_env():
    out = {}
    if CONFIG.exists():
        for line in CONFIG.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.split(" #", 1)[0].strip().strip('"').strip("'")
    return out


def set_env(key, value):
    """Set KEY="value" in config.env (keeping everything else, comments included); value None removes the line."""
    if not CONFIG.exists():
        example = ROOT / "config.example.env"
        CONFIG.write_text(example.read_text() if example.exists() else "")
    lines, found = CONFIG.read_text().splitlines(), False
    for i, line in enumerate(lines):
        if re.match(rf"\s*{re.escape(key)}\s*=", line):
            found = True
            lines[i] = None if value is None else f'{key}="{value}"'
    if not found and value is not None:
        lines.append(f'{key}="{value}"')
    CONFIG.write_text("\n".join(l for l in lines if l is not None) + "\n")


# ---------------------------------------------------------------- small helpers
def say(msg="", **kw):
    print(msg, flush=True, **kw)


def ask(question, default="", yes=False):
    if yes:
        return default
    try:
        a = input(f"{question}{f' [{default}]' if default else ''}: ").strip()
    except EOFError:
        return default
    return a or default


def confirm(question, default=True, yes=False):
    if yes:
        return default
    a = ask(f"{question} ({'Y/n' if default else 'y/N'})", "").lower()
    return default if not a else a.startswith("y")


def count_media(folder, limit=200000):
    """(photos+videos inside subfolders, photos+videos loose in the top of the folder)"""
    inside = loose = 0
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        n = sum(1 for f in filenames if f.rsplit(".", 1)[-1].lower() in MEDIA and not f.startswith("."))
        if Path(dirpath) == Path(folder):
            loose += n
        else:
            inside += n
        if inside + loose > limit:
            break
    return inside, loose


# ---------------------------------------------------------------- the steps
def choose_folder(args):
    say("1/4  Your photos")
    say("     The gallery shows the folders inside one main folder as albums.")
    default = args.folder or read_env().get("DRIVE") or str(Path.home() / "Pictures")
    while True:
        folder = Path(ask("     Where are your photos? (a folder, or a drive)", default, args.yes or bool(args.folder))).expanduser()
        if folder.is_dir():
            break
        say(f"     {folder} isn't a folder. Try again (drag the folder from Finder into this window to type its path).")
        if args.yes or args.folder:
            sys.exit(1)
    inside, loose = count_media(folder)
    say(f"     Found {inside:,} photos and videos inside {folder}.")
    if loose and not inside:
        say(f"     All {loose:,} are loose in the top of the folder. The gallery shows subfolders as albums, so put them in a")
        say(f"     subfolder first (for example {folder}/All photos), then run ./vault index.")
    elif loose:
        say(f"     {loose:,} more sit loose in the top of the folder; move them into a subfolder to see them.")
    elif not inside:
        say("     No photos found yet. That's fine: add some in a subfolder later and run ./vault index.")
    set_env("DRIVE", str(folder))
    set_env("FOLDERS", "")
    say("     Saved.\n")
    return folder


def telegram_step(args, asked=False):
    """Optional: free off-site backup to a private Telegram channel."""
    say("2/4  Telegram backup (optional)")
    say("     The gallery works fine on its own. Telegram adds a free second copy of everything, so your photos survive")
    say("     a lost drive. Takes about ten minutes. You can do it later with ./vault telegram.")
    want = args.telegram if args.telegram is not None else confirm("     Set up Telegram backup now?", False, args.yes)
    if not want:
        set_env("GALLERY_TELEGRAM", "0"); set_env("GALLERY_BACKUP", "0")
        say("     Skipped. Backup is off for now.\n")
        return False
    env = read_env()
    say()
    say("     a) In the Telegram app, create a PRIVATE channel (for example \"Photo Backup\").")
    say("     b) Go to https://my.telegram.org → API development tools and create an app (any name).")
    api_id = ask("     Your api_id", env.get("TG_API_ID", ""))
    api_hash = ask("     Your api_hash", env.get("TG_API_HASH", ""))
    if not (api_id and api_hash):
        say("     No problem; run ./vault telegram when you have them.\n")
        set_env("GALLERY_TELEGRAM", "0"); set_env("GALLERY_BACKUP", "0")
        return False
    set_env("TG_API_ID", api_id)
    set_env("TG_API_HASH", api_hash)
    if not shutil.which("tdl"):
        say("     c) The uploader needs tdl: download the macOS build from https://github.com/iyear/tdl/releases,")
        say("        unzip it and put the `tdl` file in /usr/local/bin. Then run ./vault telegram again.")
        set_env("GALLERY_TELEGRAM", "0"); set_env("GALLERY_BACKUP", "0")
        return False
    say("     c) Sign tdl in: a QR code will appear. In Telegram: Settings → Devices → Link Desktop Device, and scan it.")
    if confirm("        Sign in now?", True, args.yes):
        subprocess.run(["tdl", "login", "-T", "qr"])
    say("     d) Your channel's id:")
    subprocess.run(["tdl", "chat", "ls"])
    channel = ask("        Type the id of your backup channel (the number)", env.get("TG_CHANNEL_ID", ""))
    if channel:
        set_env("TG_CHANNEL_ID", channel)
    say("     e) Sign the gallery itself in (it reads photos back from the channel):")
    subprocess.run([PY, str(BASE / "tg.py"), "login"], cwd=BASE)
    set_env("GALLERY_TELEGRAM", None); set_env("GALLERY_BACKUP", None)
    say("     Telegram is set up. To upload what's in your photos folder, run: zsh backup/upload.sh\n")
    return True


def build_index(args):
    say("3/4  Building the gallery")
    if args.no_index:
        say("     Skipped.\n")
        return
    say("     Reading dates and making thumbnails. A few hundred photos take a minute; a big library takes a while,")
    say("     and you can stop and run ./vault index again any time: it picks up where it left off.")
    r = subprocess.run([PY, str(BASE / "build_index.py")], cwd=BASE)
    say("     Done.\n" if r.returncode == 0 else "     That didn't finish cleanly; ./vault doctor will say what's missing.\n")


def start_gallery(args):
    say("4/4  Starting the gallery")
    if args.no_service:
        say("     Not started. Run ./vault start when you want it.\n")
        return
    if not confirm("     Keep it running in the background and start it when you log in?", True, args.yes):
        say("     OK. Run ./vault start whenever you want it (Ctrl-C stops it).\n")
        return
    r = subprocess.run(["zsh", str(BASE / "install_service.sh")], capture_output=True, text=True)
    say("     " + (r.stdout.strip() or r.stderr.strip()))
    sys.path.insert(0, str(BASE))
    from doctor import address
    url = address()
    for _ in range(40):
        try:
            urllib.request.urlopen(url, timeout=2)
            break
        except Exception as e:  # a certificate error still means it's up
            if "CERTIFICATE" in str(e).upper() or "HTTP Error" in str(e):
                break
            time.sleep(0.5)
    say(f"     Running at {url}\n")
    return url


def finish(url):
    say("All set. Open your gallery:")
    say(f"     {url or 'run ./vault start, then open the address it prints'}")
    say()
    say("What's next (all optional):")
    say("     ./vault https       a padlock address, https://photos.local (asks for your Mac password once)")
    say("     ./vault telegram    free off-site backup to a private Telegram channel")
    say("     ./vault doctor      check that everything is in place")
    say("     docs/               family sharing, phone backup, smart search, sorting (see README.md)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--folder")
    ap.add_argument("--telegram", dest="telegram", action="store_true", default=None)
    ap.add_argument("--no-telegram", dest="telegram", action="store_false")
    ap.add_argument("--no-index", action="store_true")
    ap.add_argument("--no-service", action="store_true")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--only-telegram", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.only_telegram:
        args.telegram = True
        telegram_step(args)
        return 0
    say("\nPhoto Vault setup: four short steps.\n")
    choose_folder(args)
    telegram_step(args)
    build_index(args)
    url = start_gallery(args)
    finish(url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
