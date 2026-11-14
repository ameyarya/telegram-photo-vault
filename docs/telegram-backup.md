# Telegram backup (optional)

The gallery works without this. Add it when you want a free second copy of your photos in a private Telegram channel,
so you're covered if the drive dies. Photos then also stream from Telegram when the drive is unplugged.

**The easy way:** `./vault telegram` asks for what it needs and signs you in. This page is the same thing step by step,
plus the uploader, the always-on backup and the caveats worth knowing first.

Requirements: a Telegram account, and [tdl](https://github.com/iyear/tdl/releases) (download the macOS build, put the
`tdl` file somewhere on your PATH, such as `/usr/local/bin`).

## 1. Set up Telegram

1. In the Telegram app create a **private channel** (e.g. "Photo Backup").
2. Get an API id and hash at [my.telegram.org](https://my.telegram.org) → API development tools (create an app, any
   name). Put them in `config.env` as `TG_API_ID` and `TG_API_HASH`. They are yours: never share them.
3. Log tdl in, in Terminal (it's interactive): `tdl login -T qr`, then scan the QR code from Telegram →
   Settings → Devices → Link Desktop Device.
4. Find the channel's id with `tdl chat ls` and put it in `config.env` as `TG_CHANNEL_ID`.
5. Check `DRIVE` (your photos folder) and `FOLDERS` (which top-level folders, in upload order; empty = all).

## 2. Upload

```sh
zsh backup/split_large.sh                       # only if you have videos over 2 GB
zsh backup/upload.sh --list-only                # build the file list
.venv/bin/python backup/find_duplicates.py      # optional: skip exact duplicates
zsh backup/save_timestamps.sh                   # optional: save file dates (then upload state/timestamps.tsv)
caffeinate -is zsh backup/upload.sh             # upload; re-run any time, finished files are skipped
```

Progress is in `state/` (`done.log`, `upload.log`). Unplug the drive or stop it whenever you like and
run it again later. Add `--rescan` after adding new photos.

## 3. Let the gallery read it back

1. Remove the `GALLERY_TELEGRAM` and `GALLERY_BACKUP` lines from `config.env` if setup added them.
2. Sign the gallery in (Terminal): `cd gallery && ../.venv/bin/python tg.py login`
3. `./vault restart`

## Optional: real-time backup

```sh
zsh backup/install_autobackup.sh            # on  (zsh backup/install_autobackup.sh --remove  to turn off)
```

A small service (`backup/watcher.py`) runs all the time and checks every minute. New photos are uploaded to Telegram,
added to the gallery and analysed within minutes, and you get a notification. The photos can come from:
- the **inbox** on the Mac (`<state>/inbox/<folder>/`, set with `INBOX_DIR`), where phones (Syncthing), the gallery's
  upload page and the iCloud export put them;
- the **drive**, rescanned when it's plugged in and hourly after that, once copying has stopped (e.g. an Image Capture
  import).

**The drive doesn't need to be plugged in.** Inbox files are backed up and shown in the gallery straight away, and move
onto the drive under the same path once it's plugged in (only after they're in Telegram). If the Mac gets low on space
while the drive stays unplugged, you get a notification. `backup/autobackup.py` still works for a one-off run by hand.
Leave `FOLDERS` empty in `config.env` to include new top-level folders automatically.

**It uploads with your bot(s), so set up the bot uploader below first.** macOS doesn't let background jobs read an
external drive with `zsh`, `find` or `tdl` ("Operation not permitted"), so the background backup lists the drive and
uploads in Python (`backup/auto_upload.py`), which macOS asks you to allow once. Running `upload.sh` yourself in
Terminal still uses tdl as before.

The gallery service and auto-backup are started with Python directly (not shell scripts), so they also work when this
folder is inside `~/Documents`, which macOS keeps background shell scripts out of. Their logs are in
`~/Library/Logs/PhotoVault`.

## Optional: bot uploader (experimental)

Telegram limits upload speed per account; a bot is a separate account. Create one with
[@BotFather](https://t.me/BotFather), add it to your channel as an admin that can post messages, put its
token in `config.env` as `TG_BOT_TOKEN`, then run it next to `upload.sh`:

```sh
.venv/bin/python backup/bot_upload.py speedtest      # 512 MB of random data, deleted afterwards
.venv/bin/python backup/bot_upload.py run            # works backwards from the end of the list
```

In our test a bot added ~3 MB/s on top of the account. More bots (`TG_BOT_TOKEN_2`, `run 8 2`, …) can
run at once without uploading the same file twice; whether each one adds speed is untested.

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
- **Renaming or moving folders on the drive** used to look like brand-new files to Telegram. Now, before uploading, each
  new file is checked against what's already backed up (same name and size): if the old copy is gone and the date
  matches, it was renamed/moved, and the backup records and the gallery (favorites, faces, albums) follow it, with
  nothing uploaded; if the old copy is still there with identical bytes, it's a copy and is skipped. If a scan still
  finds more than `UPLOAD_HOLD` (default 1000) new files on the drive at once, they wait for an **Upload them** on
  the Backup page (you get a notification); phone and iCloud photos keep uploading meanwhile.

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
