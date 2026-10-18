# telegram-photo-vault

**A self-hosted Google Photos clone, with Telegram as free, unlimited storage.**

Back up a drive full of photos and videos to a **private Telegram channel** for free, then browse it in a local
gallery modelled on Google Photos: a timeline with a date scrubber, albums, search, smart search, people,
favorites, Live Photos, similar shots and a trash. Photos stream from Telegram, so it works even with the drive unplugged.
Everything runs on your Mac; nothing is sent anywhere except your own Telegram channel.

*Not affiliated with Google or Telegram.*

Built on macOS for one person's ~700 GB / 70,000-file photo drive. It works, it has tests, and it is
still a personal project: read the [caveats](#caveats) before trusting it with anything.

## What works

| | Status |
|---|---|
| **Uploader** (`backup/upload.sh`) — resumable batches via [tdl](https://github.com/iyear/tdl), retries, stall watchdog, each file captioned with its path | ✅ used for the real backup; tested |
| **Large videos** (`backup/split_large.sh`) — videos over Telegram's 2 GB limit become playable ~1.8 GB parts, no re-encoding | ✅ used on real 2–17 GB videos |
| **Duplicate skipping** (`backup/find_duplicates.py`) — copies confirmed byte-for-byte (SHA-256), never by name alone | ✅ tested |
| **Timestamp manifest** (`backup/save_timestamps.sh`) — saves created/modified dates Telegram doesn't keep | ✅ tested |
| **Upload verification** (`backup/verify_uploads.py`) — downloads samples back and compares SHA-256 with the originals | ✅ |
| **Gallery** (`gallery/`) — albums from your folders, real capture dates, streams from Telegram, HTTPS at `https://photo-vault.local`, this Mac only | ✅ tested, incl. headless-browser tests |
| ↳ **Date scrubber**, oldest/newest-first, **filter chips** (photos, videos, RAW, screenshots, favorites, with location, not backed up, no camera date) | ✅ |
| ↳ **Search** by date ("Feb 2024", "2023-08-26"), camera, lens, file or folder name | ✅ |
| ↳ **Smart search** ("wedding cake", "beach at sunset") and **People** (faces grouped, name them, merge several, review "same person?" suggestions; your answers survive re-analysis) — on-device OpenCLIP + OpenCV models | ✅ optional (`requirements-ml.txt`) |
| ↳ **Similar shots** — near-identical bursts grouped, best shot picked (favorite, else sharpest); "keep best, trash the rest" | ✅ optional |
| ↳ **Fix dates** — edit or shift the date of one photo or a selection; suggested dates from file names and from matching originals (checked visually); stored in the gallery only, files untouched, undoable | ✅ tested |
| ↳ **Live Photos** — the photo and its motion clip show as one item; a LIVE button plays the motion | ✅ |
| ↳ **Trash** — trashed items are hidden and moved to `<drive>/.photo-vault-trash`; put back any time. "Empty trash" deletes them from the drive only — the Telegram copy is never touched | ✅ tested |
| ↳ Viewer with **zoom & pan**, camera details (lens, exposure, ISO, GPS), **favorites**, **your own albums**, multi-select | ✅ |
| ↳ **Restore** any album, search or selection from Telegram into a folder, checked against the drive, original dates put back | ✅ |
| ↳ **Backup dashboard** — % backed up, by folder and year, problems, upload log, analysis and restore progress | ✅ |
| ↳ **Family on their phones** — share on the home Wi-Fi with per-person accounts (view-only or full access), revocable sign-ins, a phone setup page; nobody needs Telegram | ✅ tested |
| **Auto-backup** (`backup/autobackup.sh`) — plugging in the drive backs up new photos and updates the gallery | ✅ tested |
| **Bot uploader** (`backup/bot_upload.py`) — a Telegram bot uploads alongside your account for extra speed | 🧪 **experimental**: uploads verified byte-identical, but the speed gain from several bots is unproven |

Not built: encryption before upload, Windows/Linux support, access from outside your home network.

## Caveats

- **This uses Telegram in a way it wasn't designed for.** Telegram has no published storage limit for
  channels, but it can change its rules, slow your account, or ban accounts that abuse the service.
  Keep your drive as the primary copy; treat this as a free second copy.
- **Not end-to-end encrypted.** Channels are encrypted in transit and at rest, but Telegram holds the keys.
  Don't upload anything you wouldn't want Telegram to be able to read.
- **Protect your account:** turn on Two-Step Verification, set "Delete my account if away" to the longest
  option, never share the channel's invite link. Anyone with your login sees every file.
- **Speed varies.** Uploads ran at 10–12 MB/s at best and dropped to 1–2 MB/s for hours at a time
  (Telegram-side, not the connection). Plan on days for hundreds of GB.
- **Files over 2 GB** need splitting (4 GB with Telegram Premium). Empty files are skipped.
- Telegram keeps each file's bytes exactly (EXIF, GPS, video metadata survive) but **not** the file system's
  created/modified dates — that's what the timestamp manifest is for.
- macOS only: it uses `caffeinate`, `nettop`, `sips`, `launchd` and BSD tools.

## Setup

Requirements: macOS, [Homebrew](https://brew.sh), Python 3.11+, and a Telegram account.

```sh
brew install ffmpeg exiftool mkcert
# tdl: download the macOS build from https://github.com/iyear/tdl/releases and put it on your PATH
git clone https://github.com/ameyarya/telegram-photo-vault && cd telegram-photo-vault
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp config.example.env config.env
```

### 1. Telegram

1. In the Telegram app create a **private channel** (e.g. "Photo Backup").
2. Log tdl in — in Terminal, not through another tool (it's interactive): `tdl login -T qr`, then scan the
   QR code from Telegram → Settings → Devices → Link Desktop Device.
3. Find the channel's id with `tdl chat ls` and put it in `config.env` as `TG_CHANNEL_ID`.
4. Set `DRIVE` (where the drive is mounted) and `FOLDERS` (which top-level folders, in upload order).

### 2. Back up

```sh
zsh backup/split_large.sh                       # only if you have videos over 2 GB
zsh backup/upload.sh --list-only                # build the file list
.venv/bin/python backup/find_duplicates.py      # optional: skip exact duplicates
zsh backup/save_timestamps.sh                   # optional: save file dates (then upload state/timestamps.tsv)
caffeinate -is zsh backup/upload.sh             # upload; re-run any time, finished files are skipped
```

Progress is in `state/` (`done.log`, `upload.log`). Unplug the drive or stop it whenever you like and
run it again later. Add `--rescan` after adding new photos.

### 3. Gallery

1. Get an API id and hash at [my.telegram.org](https://my.telegram.org) → API development tools, and put
   them in `config.env` (`TG_API_ID`, `TG_API_HASH`).
2. Log the gallery in (Terminal): `cd gallery && ../.venv/bin/python tg.py login`
3. Index the drive (thumbnails are also made on demand): `../.venv/bin/python build_index.py`
4. Make `https://photo-vault.local` work and keep the gallery running in the background:
   ```sh
   sudo zsh setup_hostname.sh     # adds photo-vault.local to /etc/hosts (backs it up first)
   zsh setup_https.sh             # mkcert: a certificate trusted only on this Mac (asks for your password)
   zsh install_service.sh         # starts at login; `zsh install_service.sh --remove` to undo
   ```

Only this Mac can open the gallery unless you share it (below); every other device is refused. It never changes your Telegram channel. On the drive
it only moves files you trash into `.photo-vault-trash` (and back), and deletes them only when you press "Empty trash";
everything else it writes is local (favorites, your albums, restored copies).
Photos come from the drive when it's plugged in, otherwise from Telegram; anything not uploaded yet shows a
crossed-out cloud.

### Optional: family on their phones (home Wi-Fi)

Open the gallery on the Mac → **Sharing**: add an account for each person (view-only, or full access), then switch on
**Share on home Wi-Fi**. On the phone, open `http://photo-vault.local/setup` once: it walks through trusting this Mac's
certificate (so passwords and photos are encrypted on the Wi-Fi), then sign in at `https://photo-vault.local`.

- Only devices on your home network that sign in get in; everything else is refused. Switch it off any time.
- Accounts are managed only on the Mac. "Sign out" a lost phone, or change a password to sign out all their devices.
- View-only accounts can browse, favorite, add to albums and name people; trash, restore and merging are full access only.
- Passwords are stored as scrypt hashes; wrong passwords are rate-limited. Works while the Mac is awake and on the same Wi-Fi.
- Trusting the certificate means the phone trusts this Mac's mkcert authority; remove it in the phone's settings to undo.

### Optional: smart search, people, similar shots

Runs entirely on your Mac (uses the Apple GPU when available); nothing is sent anywhere.

```sh
.venv/bin/pip install -r requirements-ml.txt
cd gallery && ../.venv/bin/python ml.py download    # ~650 MB, once: OpenCLIP ViT-B/32 + OpenCV YuNet/SFace
../.venv/bin/python ml.py run                        # analyse every photo (resumable; ~1 h per 70k photos)
```

Later runs only process new photos; the Backup page has an "Analyse new photos" button.

### Optional: auto-backup when the drive is plugged in

```sh
zsh backup/install_autobackup.sh            # on  (zsh backup/install_autobackup.sh --remove  to turn off)
```

Whenever a drive is mounted, if it's `DRIVE`, it rescans for new files, uploads them, updates the gallery index
and shows a notification. It won't run twice at once, skips if an upload is already running, and waits an hour
between runs so other drives mounting don't trigger rescans.

### Optional: bot uploader (experimental)

Telegram limits upload speed per account; a bot is a separate account. Create one with
[@BotFather](https://t.me/BotFather), add it to your channel as an admin that can post messages, put its
token in `config.env` as `TG_BOT_TOKEN`, then run it next to `upload.sh`:

```sh
.venv/bin/python backup/bot_upload.py speedtest      # 512 MB of random data, deleted afterwards
.venv/bin/python backup/bot_upload.py run            # works backwards from the end of the list
```

In our test a bot added ~3 MB/s on top of the account. More bots (`TG_BOT_TOKEN_2`, `run 8 2`, …) can
run at once without uploading the same file twice; whether each one adds speed is untested.

## How it works

- Every upload's **caption is the file's path on the drive**. That's the whole index: the gallery reads
  the channel, matches captions to files, and knows where everything is.
- `state/done.log` lists finished files, so uploads resume after anything. `state/skip.txt` lists exact
  duplicates (their identical twin is uploaded instead).
- The gallery keeps its index, thumbnails and a cache on your Mac; full-size photos and videos stream
  from Telegram with range requests, so videos seek properly.

## Tests

```sh
.venv/bin/pip install -r requirements-dev.txt && .venv/bin/python -m playwright install chromium
.venv/bin/python -m pytest
```

The backup tests use a fake drive and a fake `tdl`; the gallery tests build sample photos and videos, a fake
Telegram channel (for streaming and restore) and small stand-in ML models, and drive a headless browser.
Nothing touches your drive or Telegram.

## License

MIT
