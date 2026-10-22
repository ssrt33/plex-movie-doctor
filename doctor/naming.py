"""File-name rules: what a movie's folder and files should be called, and what counts as junk."""
import difflib
import os
import re
import unicodedata

VIDEO_EXT = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".ts", ".m2ts", ".mpg", ".mpeg", ".webm",
             ".flv", ".iso", ".vob", ".divx", ".ogm"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".tbn"}

# Plex/Kodi extras folders: always left alone.
EXTRAS_DIRS = {"behind the scenes", "deleted scenes", "featurettes", "interviews", "scenes", "shorts",
               "trailers", "other", "extras", "specials", "subs", "subtitles", "bonus", "extrafanart"}

POSTER_NAMES = {"poster", "folder", "cover", "movie", "default"}
FANART_NAMES = {"fanart", "backdrop", "background", "art"}

JUNK_EXT = {".txt", ".url", ".lnk", ".website", ".webloc", ".exe", ".bat", ".cmd", ".com", ".scr", ".html", ".htm",
            ".nzb", ".torrent", ".sfv", ".md5", ".sha1", ".par2", ".rar", ".part", ".!qb", ".!ut", ".crdownload",
            ".tmp", ".dat"}
JUNK_NAMES = {"thumbs.db", ".ds_store", "desktop.ini", "ehthumbs.db"}
SITE_WORDS = re.compile(r"www\.|\.com\b|\.org\b|\.net\b|\b(yts|yify|rarbg|eztv|1337x|torrent(galaxy)?|tgx|ettv|psa)\b", re.I)
SAMPLE = re.compile(r"(^|[\W_])sample([\W_]|$)", re.I)
RAR_PART = re.compile(r"\.r\d\d$", re.I)

_bad = re.compile(r'[<>"/\\|?*\x00-\x1f]')
ID_HINT = re.compile(r"\s*[\{\[](tmdb|imdb|tvdb)(id)?[-=][^\}\]]+[\}\]]", re.I)
EDITION_HINT = re.compile(r"\s*\{edition-[^}]+\}", re.I)


def safe_name(s, fallback=""):
    """Usable as a file or folder name on Windows, macOS and Linux. "Mission: Impossible" -> "Mission - Impossible"."""
    s = (s or "").replace(": ", " - ").replace(":", "-")
    s = _bad.sub(" ", s).strip().rstrip(".").strip()
    s = re.sub(r"\s+", " ", s)
    return s[:150] or fallback


def fold(s):
    """For comparing titles: lower case, no accents or punctuation, "Matrix, The" == "The Matrix"."""
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("&", " and ")
    m = re.match(r"^(.*),\s*(the|a|an)$", s.strip())
    if m:
        s = f"{m.group(2)} {m.group(1)}"
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def strip_hints(name):
    return EDITION_HINT.sub("", ID_HINT.sub("", name)).strip()


def base_name(title, year):
    t = safe_name(title, "Untitled")
    return f"{t} ({year})" if year else t


def folder_name(title, year, edition="", ids=None, id_style="none"):
    name = base_name(title, year)
    ids = ids or {}
    if id_style == "tmdb" and ids.get("tmdb"):
        name += f" {{tmdb-{ids['tmdb']}}}"
    elif id_style == "imdb" and ids.get("imdb"):
        name += f" {{imdb-{ids['imdb']}}}"
    if edition:
        name += f" {{edition-{safe_name(edition)}}}"
    return name


def file_stem(title, year, edition=""):
    stem = base_name(title, year)
    if edition:
        stem += f" {{edition-{safe_name(edition)}}}"
    return stem


def folder_ok(current, wanted, id_style):
    """True if a folder name is fine as it is. With no id style, existing id hints are allowed."""
    if current == wanted:
        return True
    if id_style == "none":
        return ID_HINT.sub("", current).strip() == wanted
    return False


def split_ext(name):
    stem, ext = os.path.splitext(name)
    return stem, ext.lower()


def is_video(name):
    return split_ext(name)[1] in VIDEO_EXT


# ---------------------------------------------------------------- release names

_TAGS = re.compile(r"\b(2160p|1080p|720p|576p|480p|4k|uhd|hdr10?\+?|dv|dovi|bluray|blu ray|bdrip|brrip|web ?dl|"
                   r"webrip|web|hdtv|dvdrip|dvd|remux|x264|x265|h ?264|h ?265|hevc|avc|aac|ac3|dts|truehd|atmos|"
                   r"ddp?5 ?1|10bit|proper|repack|extended|unrated|remastered|imax)\b.*$", re.I)


def parse_release(name):
    """Best guess at (title, year) from a name like "The.Matrix.1999.1080p.BluRay.x264-GRP".

    Returns None when there's no year to anchor on.
    """
    stem = split_ext(name)[0] if is_video(name) else name
    s = strip_hints(stem)
    s = re.sub(r"^\s*[\[\(][^\]\)]*[\]\)]\s*", "", s)  # "[YTS.MX] Title..." prefixes
    if " " not in s.strip() or s.count(".") >= 2 or "_" in s:
        s = re.sub(r"[._]+", " ", s)
    years = [m for m in re.finditer(r"[\(\[]?\b((?:19|20)\d{2})\b[\)\]]?", s) if m.start() > 0]
    if not years:
        return None
    m = years[-1]
    bracketed = [c for c in years if c.group(0)[:1] in "([" and c.group(0)[-1:] in ")]"]
    if bracketed:  # "Blade Runner 2049 (2017)"
        m = bracketed[-1]
    else:  # "Blade.Runner.2049.2017.1080p": the year is the one followed by tags or nothing
        for c in years:
            rest = s[c.end():].strip(" -)]")
            if not rest or _TAGS.match(rest) or rest[:1] in "([{":
                m = c
                break
    title = s[:m.start()]
    title = _TAGS.sub("", title)
    title = re.sub(r"[\s\-\[\(]+$", "", title).strip(" -")
    if not title:
        return None
    if title == title.lower() or title == title.upper():
        title = title.title()
    return title, int(m.group(1))


def similar(a, b):
    fa, fb = fold(a), fold(b)
    if not fa or not fb:
        return False
    if fa == fb or fa in fb or fb in fa:
        return True
    return difflib.SequenceMatcher(None, fa, fb).ratio() >= 0.6


def name_matches_plex(name, titles, year):
    """Does an existing file/folder name agree with what Plex matched? Returns (ok, reason)."""
    got = parse_release(name)
    if got:
        t, y = got
        if year and abs(y - int(year)) > 1:
            return False, f"the name says {y} but Plex matched {year}"
        if not any(similar(t, x) for x in titles if x):
            return False, f"the name says “{t}” but Plex matched “{titles[0]}”"
        return True, ""
    plain = strip_hints(split_ext(name)[0] if is_video(name) else name)
    if not any(similar(plain, x) for x in titles if x):
        return False, f"the name “{plain}” doesn't look like “{titles[0]}”"
    return True, ""


# ---------------------------------------------------------------- what's in a movie folder

def _belongs(f, stem):
    return f.startswith(stem + ".") or f.startswith(stem + "-")


def sidecars(folder_files, stem, exclude=(), other_stems=()):
    """Files belonging to a video by name: "Stem.en.srt", "Stem-poster.jpg", "Stem.nfo"…

    other_stems are the other videos in the folder: "Alien.Resurrection.en.srt" belongs to
    "Alien.Resurrection", not to "Alien".
    """
    longer = [s for s in other_stems if len(s) > len(stem) and s != stem]
    out = []
    for f in folder_files:
        if f in exclude or not _belongs(f, stem):
            continue
        if any(_belongs(f, s) or f == s for s in longer):
            continue
        out.append(f)
    return out


def is_kodi_nfo(path):
    try:
        with open(path, "rb") as fh:
            head = fh.read(512).lstrip(b"\xef\xbb\xbf").lstrip()
    except OSError:
        return False
    return head.startswith(b"<?xml") or head.lower().startswith(b"<movie")


def has_poster(files, stems):
    for f in files:
        st, ext = split_ext(f)
        if ext not in IMAGE_EXT:
            continue
        low = st.lower()
        if low in POSTER_NAMES or any(st == s or st == s + "-poster" for s in stems):
            return True
    return False


def has_fanart(files, stems):
    for f in files:
        st, ext = split_ext(f)
        if ext not in IMAGE_EXT:
            continue
        if st.lower() in FANART_NAMES or any(st == s + "-fanart" for s in stems):
            return True
    return False


def has_nfo(folder, files):
    return any(split_ext(f)[1] == ".nfo" and is_kodi_nfo(os.path.join(folder, f)) for f in files)


def junk_in(folder, part_paths, size_of=os.path.getsize):
    """Leftover junk in a movie's own folder (and a Sample/ subfolder). Returns local paths."""
    out = []
    try:
        entries = sorted(os.listdir(folder))
    except OSError:
        return out
    for f in entries:
        p = os.path.join(folder, f)
        if p in part_paths:
            continue
        st, ext = split_ext(f)
        if os.path.isdir(p):
            if f.lower() in ("sample", "samples", "proof", "screens", "screenshots"):
                out.append(p)
            continue
        low = f.lower()
        if low in JUNK_NAMES or low.startswith("._"):
            out.append(p)
        elif ext in JUNK_EXT or RAR_PART.search(low):
            out.append(p)
        elif ext == ".nfo" and not is_kodi_nfo(p):
            out.append(p)  # a release-group .nfo, not one Kodi/Jellyfin can read
        elif ext in VIDEO_EXT and SAMPLE.search(st):
            try:
                if size_of(p) < 800 * 1024 * 1024:
                    out.append(p)
            except OSError:
                pass
        elif ext in IMAGE_EXT and SITE_WORDS.search(st):
            out.append(p)  # "WWW.YTS.MX.jpg"
    return out
