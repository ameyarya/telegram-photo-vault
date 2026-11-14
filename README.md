# Photo Vault

**A private, self-hosted Google Photos for your Mac.** Point it at a folder or drive of photos and videos and browse
them in your browser: a timeline with a date scrubber, albums, search, favorites, Live Photos, a trash and more.
Optionally add a free off-site backup to a private Telegram channel, search by what's *in* the photos, group faces,
and share with family on your home Wi-Fi.

Everything runs on your Mac. Nothing leaves it unless you turn on Telegram backup (to your own private channel) or the
Claude sorting page (which says so before you press anything).

*Unofficial: not affiliated with Google or Telegram. Built for one person's 700 GB / 70,000-photo drive; it has a large
test suite, and it is still a personal project, so read [the caveats](docs/telegram-backup.md#caveats) before trusting it
with the only copy of anything.*

## Quick start (about 5 minutes)

You need a Mac with [Homebrew](https://brew.sh) and a folder of photos. Nothing else: no Telegram, no account, no sudo.

```sh
git clone https://github.com/ameyarya/telegram-photo-vault
cd telegram-photo-vault
./setup.sh
```

`./setup.sh` installs what's missing (Python, ffmpeg, exiftool), asks where your photos are, builds the gallery and
starts it. When it finishes it prints an address, `http://localhost:8000`. That's it: you're looking at your photos.

> **How your folder maps to the gallery:** each folder inside the one you choose becomes an album (`Pictures/Holiday`
> → "Holiday"). Photos loose in the top of that folder aren't shown: move them into a subfolder.
> Your files are never changed.

Something not working? Run **`./vault doctor`**. It checks everything and says exactly how to fix what's missing.

## Everyday commands

| | |
|---|---|
| `./vault open` | open the gallery in your browser |
| `./vault index` | pick up photos you've added to the folder |
| `./vault doctor` | check that everything is in place, with the fix for anything that isn't |
| `./vault service on` / `off` | keep the gallery running in the background and start it at login |
| `./vault start` | run it in this window instead (Ctrl-C stops it) |
| `./vault update` | get the latest version |
| `./vault help` | the full list |

## Make it better, step by step

Each of these is optional and independent. Do them in any order, whenever you like.

| Want | Run | Time |
|---|---|---|
| A padlock address, `https://photos.local`, instead of `localhost:8000` | `./vault https` (asks for your Mac password once) | 1 min |
| Search by what's in the photos ("beach at sunset"), people, similar shots, all on your Mac | `./vault smart` (downloads ~650 MB) | 10 min + about 1 h per 70k photos |
| A free off-site copy of everything in a private Telegram channel | `./vault telegram` → [docs/telegram-backup.md](docs/telegram-backup.md) | 10 min + upload time |
| Family browsing on their phones over your Wi-Fi, each with their own login | [docs/phones-and-family.md](docs/phones-and-family.md) | 5 min |
| Automatic phone backup (Android, iPhone, iCloud Photos) | [docs/phones-and-family.md](docs/phones-and-family.md) | 10 min |
| Photos sorted into Food, Selfies, Travel, … by Claude | the **Sorting** page → [docs/smart-features.md](docs/smart-features.md) | 5 min |

## What you get

Everything is listed in [docs/features.md](docs/features.md). The highlights:

- **Timeline** with a date scrubber, oldest/newest first, filters (photos, videos, screenshots, RAW, favorites, with location…)
  and a button to hide screenshots.
- **Search** by date, camera, lens or folder; with smart search, by what's in the picture.
- **Viewer** with zoom, camera details, favorites, your own albums, slideshow, and a basic editor (the original is never changed).
- **People, similar shots, On this day, Live Photos, Fix dates, Trash** (nothing is deleted until you empty it).
- **Sorting**: let Claude file photos under categories, with a sphere of your photos flying into their folders.
- **Backup dashboard, restore, family accounts, upload from phones**: when Telegram backup is on.

## If something goes wrong

| Symptom | Fix |
|---|---|
| `./setup.sh` says Homebrew is missing | install it from [brew.sh](https://brew.sh), then run `./setup.sh` again |
| The page is empty | `./vault doctor`. Usually the photos are loose in the top of the folder (put them in a subfolder), or you added photos and need `./vault index` |
| "This gallery is only available on the Mac it runs on" | you're opening it from another device. Share it on purpose: [docs/phones-and-family.md](docs/phones-and-family.md) |
| `localhost:8000` doesn't load | `./vault service on`, or `./vault start` in a Terminal window you leave open |
| The browser warns about the certificate on `https://photos.local` | run `./vault https` again and restart the browser |
| It stopped working after a macOS update | `./vault service on` (reinstalls the background service), then `./vault doctor` |
| Videos have no thumbnail | `brew install ffmpeg`, then `./vault index` |
| Want to start over | `./vault service off`, delete `config.env` and the `state/` folder, run `./setup.sh` |

Your photos are only ever read, apart from the Trash (files you trash move to `.photo-vault-trash` on the drive, and are
deleted only when you press Empty trash). Everything else the gallery writes (favorites, albums, thumbnails) is in `state/`.

## How it works

- **The gallery** (`gallery/`) is a small web server (FastAPI) with a SQLite index and a single-page front end. It reads
  your folders, takes real capture dates from EXIF, makes thumbnails, and serves it all on this Mac only. Other devices are
  refused unless you switch on sharing.
- **Telegram backup** (`backup/`, optional) uploads each file to a private channel with its path as the caption. That caption
  is the whole index: the gallery can rebuild everything from the channel, and streams photos from it when the drive is unplugged.
- **Settings** live in `config.env` (git-ignored; `config.example.env` documents every option). Logs and progress are in `state/`
  and `~/Library/Logs/PhotoVault`.

## Tests

```sh
./vault test              # first time: .venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m playwright install chromium
```

The tests build sample photos, a fake Telegram channel and stand-in ML models, and drive a headless browser. Nothing touches
your photos, your drive or Telegram.

## Docs

[Everything it does](docs/features.md) · [Telegram backup](docs/telegram-backup.md) · [Phones and family](docs/phones-and-family.md) ·
[Smart search and sorting](docs/smart-features.md)

## License

MIT
