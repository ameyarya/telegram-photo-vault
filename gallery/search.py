"""Which photos a page shows: folder, filter chip, search text, favorites, your albums, a person.

selection(...) returns a SQL WHERE clause plus its arguments, used by every list endpoint so they all agree.
"""
import re

# A screenshot: no camera recorded and either a PNG (iPhone/Android screenshots) or phone-screen shaped (taller than
# about 2:1, or an older iPhone's exact 16:9 screen size: screenshots saved or shared as JPG/HEIC), or "screenshot" in
# the name. Anything a camera made keeps its camera make/model, so tall camera photos and panoramas stay photos.
PHONE_SCREEN = ("(width > 0 AND height > 0 AND (MAX(width, height) * 1.0 / MIN(width, height) >= 1.95"
                " OR (MIN(width, height), MAX(width, height)) IN (VALUES (640, 1136), (750, 1334), (1242, 2208))))")
SCREENSHOT = f"((make IS NULL AND (ext = 'png' OR {PHONE_SCREEN})) OR lower(name) LIKE '%screenshot%')"

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
