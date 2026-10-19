# telegram-photo-vault

**A self-hosted Google Photos clone, with Telegram as free, unlimited storage.**

Back up a drive full of photos and videos to a **private Telegram channel** for free, then browse it in a local
gallery modelled on Google Photos: a timeline with a date scrubber, albums, search, smart search, people,
favorites, Live Photos, similar shots and a trash. Photos stream from Telegram, so it works even with the drive unplugged.
Everything runs on your Mac; nothing is sent anywhere except your own Telegram channel.

*Unofficial: not affiliated with Google or Telegram. It uses the Telegram API with your own api_id
(see [Telegram's rules](#telegrams-rules-checked-2026-10-19)).*

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
| ↳ **Download** a selection — one file, or a streamed .zip (Live Photo clips included), from the drive or Telegram | ✅ tested |
| ↳ **Upload from phone** — family back up their phone from the gallery's Upload page (home Wi-Fi), into their own folder on the drive; resumable, never overwrites | ✅ tested |
| **Auto-backup** (`backup/autobackup.py`) — once a day (3 AM, or when the drive is plugged in): backs up new photos, updates the gallery, analyses them; waits while files are still being copied | ✅ tested |
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

Once a day at 3 AM (or as soon as the Mac wakes, if it was asleep), and when `DRIVE` is plugged in if it hasn't run that
day: it rescans for new files, uploads them,
updates the gallery index, analyses new photos (if the optional models are installed) and shows a notification when
something was backed up. If files are still being copied onto the drive (say, an Image Capture import or a phone
syncing), it checks again every 5 minutes for up to 2 hours, so a half-copied file is never uploaded. It won't run twice at once and skips if an upload is running.
Leave `FOLDERS` empty in `config.env` to include new top-level folders automatically.

**It uploads with your bot(s), so set up the bot uploader below first.** macOS doesn't let background jobs read an
external drive with `zsh`, `find` or `tdl` ("Operation not permitted"), so the background backup lists the drive and
uploads in Python (`backup/auto_upload.py`), which macOS asks you to allow once. Running `upload.sh` yourself in
Terminal still uses tdl as before.

The gallery service and auto-backup are started with Python directly (not shell scripts), so they also work when this
folder is inside `~/Documents`, which macOS keeps background shell scripts out of. Their logs are in
`~/Library/Logs/PhotoVault`.

### Optional: back up an Android phone automatically (Syncthing)

The gallery's **Upload from phone** page works on any phone, but the page has to stay open. For automatic background
backup of an Android phone, use [Syncthing](https://syncthing.net) (free, open source):

1. Mac: `brew install syncthing && brew services start syncthing`, then open http://127.0.0.1:8384. In Settings,
   turn off global discovery and relaying if both devices are always on the same Wi-Fi.
2. Phone: install **Syncthing-Fork** from the Play Store, add the Mac (Actions → Show ID on the Mac shows a QR code), and
   add a folder for `DCIM/Camera` as **Send Only**, shared with the Mac.
3. Mac: accept the phone and its folder, with the path set to a folder on your drive (e.g. `<drive>/her-phone`), and
   Folder type **Receive Only**. Under Advanced, turn on **Ignore Delete**, so deleting photos on the phone never deletes
   them from the drive. Never press "Revert Local Changes" on it, because that deletes files the phone doesn't have.

Auto-backup then picks the photos up like anything else copied onto the drive.

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


## Telegram's rules (checked 2026-10-19)

From Telegram's [Terms of Service](https://telegram.org/tos), [API Terms](https://core.telegram.org/api/terms)
and [FAQ](https://telegram.org/faq):

- **Storage:** free accounts get unlimited cloud storage, with files up to 2 GB each (4 GB with Premium). Bigger files are
  uploaded in parts (`backup/split_large.sh`).
- **No guarantee:** Telegram doesn't promise to keep your data, and it won't compensate for losses. A ban for breaking
  the rules can cut off access. **Keep the drive as your main copy, and Telegram as the off-site one.**
- **Inactive accounts are deleted, by default after 18 months offline (Settings → Privacy → "If away for"), with all
  their media.** Set it to the longest option, keep using the account, and add a second person as an admin of the
  backup channel.
- **API use:** use your own api_id (this app never ships one). Say clearly that the app uses the Telegram API, and
  don't put "Telegram" in an app's name unless it starts with "Unofficial".
- **No AI training on Telegram data:** the optional smart search and face analysis run ready-made models on the photos
  on your drive. They don't train anything, and they don't read data from Telegram.
- **Content:** only back up your own, legal content. Illegal content gets accounts banned.
- **Pace:** the uploaders respect Telegram's flood-wait replies (they slow down when told to), so they don't spam.
