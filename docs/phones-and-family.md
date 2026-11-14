# Phones and family

## Share with family on the home Wi-Fi

First run `./vault https` once (phones need the padlock address, `https://photos.local`). Then open the gallery on the Mac → **Sharing**: add an account for each person (view-only, or full access), then switch on
**Share on home Wi-Fi**. On the phone, open `http://photos.local/setup` once: it walks through trusting this Mac's
certificate (so passwords and photos are encrypted on the Wi-Fi), then sign in at `https://photos.local`.

- Only devices on your home network that sign in get in; everything else is refused. Switch it off any time.
- Accounts are managed only on the Mac. "Sign out" a lost phone, or change a password to sign out all their devices.
- View-only accounts can browse, favorite, add to albums and name people; trash, restore and merging are full access only.
- Passwords are stored as scrypt hashes; wrong passwords are rate-limited. Works while the Mac is awake and on the same Wi-Fi.

**From anywhere, privately (Tailscale):** install [Tailscale](https://tailscale.com) on the Mac and on each device, then run
`tailscale serve --bg 8444` on the Mac. The gallery listens on `127.0.0.1:8444` for that proxy only; everyone arriving
that way has to sign in (the Mac's own no-login access never applies to them) and failed sign-ins are counted per device.
Nothing is opened to the public internet. The address is `https://<mac-name>.<tailnet>.ts.net`.
- Trusting the certificate means the phone trusts this Mac's mkcert authority; remove it in the phone's settings to undo.

## Optional: back up an Android phone automatically (Syncthing)

The gallery's **Upload from phone** page works on any phone, but the page has to stay open. For automatic background
backup of an Android phone, use [Syncthing](https://syncthing.net) (free, open source):

1. Mac: `brew install syncthing && brew services start syncthing`, then open http://127.0.0.1:8384. In Settings,
   turn off global discovery and relaying if both devices are always on the same Wi-Fi.
2. Phone: install **Syncthing-Fork** from the Play Store, add the Mac (Actions → Show ID on the Mac shows a QR code), and
   add a folder for `DCIM/Camera` as **Send Only**, shared with the Mac.
3. Mac: accept the phone and its folder, with the path set to a folder in the inbox (e.g. `<state>/inbox/her-phone`, so
   it syncs even with the drive unplugged; the watcher moves backed-up files onto the drive), and
   Folder type **Receive Only**. Under Advanced, turn on **Ignore Delete**, so deleting photos on the phone never deletes
   them from the drive. Never press "Revert Local Changes" on it, because that deletes files the phone doesn't have.
   Syncthing needs the hidden `.stfolder` inside that folder; the watcher puts it back if it ever goes missing.

**iPhone:** [Möbius Sync](https://www.mobiussync.com) is a Syncthing app for iOS. Share its photo library folder as
Send Only, and on the Mac put it in `<state>/phone-sync/<folder>` (e.g. `phone-sync/iphone`), not in the inbox: iPhone
photo numbers wrap around, so `backup/phone_import.py` moves each new photo into `inbox/<folder>` under a name that was
never used there, and sets aside photos that are already backed up (e.g. by iCloud Photos). iOS only lets the app sync
while it's open or briefly in the background, so open it now and then on home Wi-Fi.

Auto-backup then picks the photos up like anything else copied onto the drive.

## Optional: back up an iPhone with a Shortcut (free, no app)

iOS doesn't let an app sync the photo library in the background, but a Shortcuts automation can send new photos to
the gallery over the home Wi-Fi (`gallery/shortcut.py`). Each run sends what was created since the last finished run,
so nothing is missed while the Mac is asleep or away; a photo sent twice is kept once.

1. On the Mac, make a token for the drive folder (it's shown once; making a new one replaces it):
   `curl -s -X POST https://<hostname>/api/shortcut/token -H 'Content-Type: application/json' -d '{"folder": "iphone"}'`
2. In the Shortcuts app, a shortcut that: gets the contents of `https://<hostname>/api/shortcut/since` (header
   `Authorization: Bearer <token>`), turns it into a date, finds photos whose creation date is after it, sends each
   with "Get Contents of URL" (POST, Form, a File field named `file`) to `/api/shortcut/upload`, then POSTs to
   `/api/shortcut/done`. All three with the same header.
3. Automations that run it, set to Run Immediately: when the Camera app is closed, when joining the home Wi-Fi, when
   the phone starts charging, and at a few times of day.

Photos arrive in `<state>/phone-sync/<folder>` and `backup/phone_import.py` moves them into the inbox under a name never
used in that folder (iPhone numbers wrap around). Live Photos arrive as the still photo only.

## Optional: back up an iPhone automatically through iCloud Photos

If your iPhone uses iCloud Photos, the Mac can pick up new photos from iCloud each night, with nothing to do on the phone:

1. Mac: open **Photos** → Settings → iCloud → turn on **iCloud Photos**, with **Optimize Mac Storage**. Missing originals
   are downloaded when they're exported. The library must be on the Mac's own disk, because iCloud Photos doesn't work
   on exFAT drives.
2. `.venv/bin/pip install -r requirements-icloud.txt` (osxphotos).
3. Give the Python that runs auto-backup **Full Disk Access** (System Settings → Privacy & Security), because macOS
   protects the Photos library. Allow it to access Photos if asked.
4. In `config.env`: `ICLOUD_EXPORT_FOLDER="iphone"`, plus `ICLOUD_EXPORT_SINCE="<date of the newest photo already on
   the drive>"`, so photos you already copied aren't exported again.

Whenever iCloud brings new photos to the Mac, the watcher runs `backup/icloud_export.py`: new photos (full quality, Live Photo videos included,
edited copies and photos shared by others skipped) go into that folder in the inbox, dated when they were taken, then on to
Telegram. A name already in the folder gets " (2)", because iPhone numbers wrap around. Nothing is ever overwritten,
and deleting a photo in Photos never deletes it from the drive. Try it with `.venv/bin/python backup/icloud_export.py
--dry-run`.
