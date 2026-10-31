"""Which photos a page shows: folder, filter chip, search text, favorites, your albums, a person.

selection(...) returns a SQL WHERE clause plus its arguments, used by every list endpoint so they all agree.
"""
import re

# A screenshot is what a phone saves when you take one, not anything screen-shaped (Snapchat saves, downloads and AI
# images are tall JPGs/PNGs too):
#  * iPhone / iPad: a PNG with no camera, at the exact pixel size of an Apple screen (portrait or landscape)
#  * iPhone, drawn on before saving, which stops it being a PNG: a JPEG marked "Screenshot" in its EXIF (shot = 1), or a
#    HEIC with no camera at the size of a tall iPhone screen (it has no marker; a 16:9 HEIC is more likely from an app)
#  * Android and anything else: "screenshot" in the file name (Screenshot_2024-..., Screen Shot ...)
APPLE_SCREENS = ((640, 960), (640, 1136), (750, 1334), (768, 1024), (828, 1792), (1080, 1920), (1080, 2340), (1125, 2436),
                 (1170, 2532), (1179, 2556), (1206, 2622), (1242, 2208), (1242, 2688), (1260, 2736), (1284, 2778), (1290, 2796),
                 (1320, 2868), (1488, 2266), (1536, 2008), (1536, 2048), (1620, 2160), (1640, 2360), (1668, 2224), (1668, 2388),
                 (2048, 2732), (2064, 2752))
_SIZES = ", ".join(f"({a}, {b})" for a, b in APPLE_SCREENS)
SCREENSHOT = ("(lower(name) LIKE '%screenshot%' OR lower(name) LIKE '%screen shot%' OR "
              "shot = 1 OR "
              f"(make IS NULL AND width > 0 AND height > 0 AND (min(width, height), max(width, height)) IN (VALUES {_SIZES}) "
              "AND (ext = 'png' OR (ext IN ('heic', 'heif') AND max(width, height) > 2 * min(width, height)))))")

FILTERS = {
    "photos": f"kind = 'image' AND NOT {SCREENSHOT}",
    "videos": "kind = 'video'",
    "raw": "ext IN ('arw','cr2','cr3','nef','dng','raf','orf','rw2')",
    "screenshots": SCREENSHOT,
    "backedup": "msg_id IS NOT NULL",
    "notbackedup": "msg_id IS NULL",
    "undated": "date_src = 'file'",
    "favorites": "id IN (SELECT media_id FROM favorites)",
    "located": "lat IS NOT NULL",
    "live": "live_id IS NOT NULL",
}

# Shown everywhere unless asked otherwise: Live Photo motion clips play from their photo, trashed items are hidden.
VISIBLE = "live_of IS NULL AND id NOT IN (SELECT media_id FROM trash)"

MONTHS = {m: i + 1 for i, names in enumerate(
    [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",), ("jun", "june"),
     ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
     ("dec", "december")]) for m in names}


def parse_query(q):
    """Split search text into date parts and words. Returns (clauses, args, description)."""
    words = [w for w in re.split(r"[\s,]+", q.strip().lower()) if w]
    clauses, args, said = [], [], []
    year = next((w for w in words if re.fullmatch(r"(19|20)\d{2}", w)), None)
    month = next((w for w in words if w in MONTHS), None)
    iso = next((w for w in words if re.fullmatch(r"(19|20)\d{2}-\d{2}(-\d{2})?", w)), None)
    if iso:
        clauses.append("taken_at LIKE ?"); args.append(iso + "%"); said.append(iso)
    elif month and year:
        clauses.append("taken_at LIKE ?"); args.append(f"{year}-{MONTHS[month]:02d}%"); said.append(f"{month} {year}")
    elif year:
        clauses.append("taken_at LIKE ?"); args.append(year + "%"); said.append(year)
    elif month:
        clauses.append("substr(taken_at, 6, 2) = ?"); args.append(f"{MONTHS[month]:02d}"); said.append(month)
    for w in words:
        if w in (year, month, iso):
            continue
        like = f"%{w}%"
        clauses.append("(lower(name) LIKE ? OR lower(album) LIKE ? OR lower(coalesce(make,'')) LIKE ? "
                       "OR lower(coalesce(model,'')) LIKE ? OR lower(coalesce(lens,'')) LIKE ?)")
        args += [like] * 5
        said.append(f'"{w}"')
    return clauses, args, " · ".join(said)


def selection(album="", sub=True, filter="", q="", user_album=None, person=None, ids=None, visible=True):
    clauses, args = ([VISIBLE] if visible else []), []
    if album:
        if sub:
            clauses.append("(album = ? OR album LIKE ? ESCAPE '\\')")
            args += [album, album.replace("%", "\\%").replace("_", "\\_") + "/%"]
        else:
            clauses.append("album = ?"); args.append(album)
    for f in (filter or "").split(","):
        if f in FILTERS:
            clauses.append(FILTERS[f])
    if q:
        c, a, _ = parse_query(q)
        clauses += c; args += a
    if user_album is not None:
        clauses.append("id IN (SELECT media_id FROM user_album_items WHERE album_id = ?)"); args.append(user_album)
    if person is not None:
        clauses.append("id IN (SELECT media_id FROM faces WHERE person = ?)"); args.append(person)
    if ids is not None:
        clauses.append(f"id IN ({','.join('?' * len(ids)) or 'NULL'})"); args += list(ids)
    return (" AND ".join(clauses) or "1"), args
