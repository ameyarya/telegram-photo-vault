# Everything it does

| | Status |
|---|---|
| **Uploader** (`backup/upload.sh`) — resumable batches via [tdl](https://github.com/iyear/tdl), retries, stall watchdog, each file captioned with its path | ✅ used for the real backup; tested |
| **Large videos** (`backup/split_large.sh`) — videos over Telegram's 2 GB limit become playable ~1.8 GB parts, no re-encoding | ✅ used on real 2–17 GB videos |
| **Duplicate skipping** (`backup/find_duplicates.py`) — copies confirmed byte-for-byte (SHA-256), never by name alone | ✅ tested |
| **Timestamp manifest** (`backup/save_timestamps.sh`) — saves created/modified dates Telegram doesn't keep | ✅ tested |
| **Upload verification** (`backup/verify_uploads.py`) — downloads samples back and compares SHA-256 with the originals | ✅ |
| **Gallery** (`gallery/`) — albums from your folders, real capture dates, streams from Telegram, HTTPS at `https://photos.local`, this Mac only | ✅ tested, incl. headless-browser tests |
| ↳ **Date scrubber**, oldest/newest-first, **filters** (the page opens on All; photos, videos, RAW, screenshots, favorites, with location, not backed up, no camera date), a **Screenshots** button that hides or shows them on this device | ✅ |
| ↳ **Search** by date ("Feb 2024", "2023-08-26"), camera, lens, file or folder name | ✅ |
| ↳ **Smart search** ("wedding cake", "beach at sunset") and **People** (faces grouped, name them, merge several, review "same person?" suggestions, choose their photo; your answers survive re-analysis) — on-device OpenCLIP + OpenCV models | ✅ optional (`requirements-ml.txt`) |
| ↳ **Similar shots** — near-identical bursts grouped, best shot picked (favorite, else sharpest); "keep best, trash the rest" | ✅ optional |
| ↳ **Fix dates** — edit or shift the date of one photo or a selection; suggested dates from file names (including timestamps like `FB_IMG_1471116064495.jpg`) and from matching originals (checked visually); stored in the gallery only, files untouched, undoable | ✅ tested |
| ↳ **Live Photos** — the photo and its motion clip show as one item; a LIVE button plays the motion | ✅ |
| ↳ **Trash** — trashed items are hidden and moved to `<drive>/.photo-vault-trash`; put back any time. "Empty trash" deletes them from the drive only — the Telegram copy is never touched. Works with the drive unplugged: the file changes wait and happen when it's plugged in, and a photo trashed while still on the Mac never goes to the drive (also Delete in the photo viewer) | ✅ tested |
| ↳ Viewer with **zoom & pan**, camera details (lens, exposure, ISO, GPS), **favorites**, **your own albums**, multi-select | ✅ |
| ↳ **On this day** (this date in earlier years, each year opening as a grid of its photos; the clock button in the toolbar above the photos switches it on or off for everyone using the gallery), **slideshow**, **select all** (Ctrl/⌘ A), shift-click a range, hold a photo to select on phones | ✅ |
| ↳ **Photo categories** (optional: Food, Selfies, Travel and more as filters; the **Sorting** page (with a sphere of your photos that fly into their category cards) has "Try it", "Sort all" and Stop buttons; sorted on your Mac by the same model as smart search, so nothing is sent anywhere. Needs `./vault smart` first. Idea from [claude-photo-sorter](https://github.com/kairothq/claude-photo-sorter)) | ✅ |
| ↳ **Basic photo editor** (crop, rotate, brightness, contrast, saturation; saves a new photo, the original is never changed), **choose an album's cover** | ✅ |
| ↳ **Restore** any album, search or selection from Telegram into a folder, checked against the drive, original dates put back | ✅ |
| ↳ **Backup dashboard** — % backed up, by folder and year, problems, upload log, analysis and restore progress | ✅ |
| ↳ **Family on their phones** — share on the home Wi-Fi with per-person accounts (view-only or full access), revocable sign-ins, a phone setup page; nobody needs Telegram | ✅ tested |
| ↳ **Download** a selection — one file, or a streamed .zip (Live Photo clips included), from the drive or Telegram | ✅ tested |
| ↳ **Upload from phone** — family back up their phone from the gallery's Upload page (home Wi-Fi), into their own folder on the drive; resumable, never overwrites | ✅ tested |
| **Real-time backup** (`backup/watcher.py`): new photos from phones, iCloud or the drive reach Telegram and the gallery within minutes, even with the drive unplugged (they wait in an inbox on the Mac, then move onto the drive) | ✅ tested |
| **Bot uploader** (`backup/bot_upload.py`) — a Telegram bot uploads alongside your account for extra speed | 🧪 **experimental**: uploads verified byte-identical, but the speed gain from several bots is unproven |

Not built: encryption before upload, Windows/Linux support, access from outside your home network.
