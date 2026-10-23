"""The checks. Each looks at the library and returns issues, each with a ready-made fix.

Nothing here changes anything; it only plans. An issue is a plain dict:
  id       stable across scans, so "ignore" sticks
  kind     which check found it
  group    "Naming & folders", "Duplicates" or "Artwork & NFO"
  summary  one line for the list
  items    [{rk, title, year}] the Plex movies involved
  folders  local folders it touches (so one fix can retire overlapping issues)
  changes  [{field, before, after, image?}] for the preview
  plan     lines describing exactly what will be moved or created
  fix      what to do (see fixes.py), or None if it's information only
  copies   (duplicates only) the copies to choose between
"""
import hashlib
import logging
import os
from collections import defaultdict

from . import naming, paths
from .naming import is_video, split_ext

log = logging.getLogger("doctor")
TRASH = "{TRASH}"  # filled in with a dated trash folder when the fix runs

# kind -> (group, label, moves files, description)
KINDS = {
    "rename": ("Naming & folders", "Folder or file name doesn't match", True,
               "Renames to “Title (Year)”, the way Plex recommends (the folder too, for movies in their own "
               "folder). Subtitles and other files named after the movie are renamed with it."),
    "own_folder": ("Naming & folders", "Movie not in its own folder", True,
                   "Moves the movie and its subtitles into a new “Title (Year)” folder."),
    "unmatched": ("Naming & folders", "Not matched in Plex", True,
                  "Plex couldn't identify these. Renaming from the release name usually lets Plex match them."),
    "wrong_match": ("Naming & folders", "Name and Plex match disagree", False,
                    "The file name and what Plex matched look different, so nothing is renamed. Check the match "
                    "in Plex (… → Fix Match) and scan again."),
    "junk": ("Naming & folders", "Leftover junk files", True,
             "Samples, release-group .nfo/.txt files, website images and the like. Moved to the trash folder."),
    "duplicate": ("Duplicates", "Duplicate copies", True,
                  "Keeps the copy you pick (the best one is picked for you) and moves the others to the trash folder."),
    "same_file": ("Duplicates", "Same file in two libraries", False,
                  "Two Plex libraries point at the same files. They aren't copies; remove the overlap in Plex."),
    "artwork": ("Artwork & NFO", "Missing poster, fanart or NFO", False,
                "Saves Plex's poster and background next to the movie and writes an .nfo with its details "
                "(named after the movie, e.g. “Heat (1995)-poster.jpg”, when movies share a folder)."),
}
GROUP_ORDER = ["Naming & folders", "Duplicates", "Artwork & NFO"]


def _id(*parts):
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


# ---------------------------------------------------------------- reading Plex items

def _ids(md):
    out = {}
    for g in md.get("Guid") or []:
        gid = g.get("id", "")
        if "://" in gid:
            k, v = gid.split("://", 1)
            if k in ("imdb", "tmdb", "tvdb"):
                out[k] = v
    return out


RES_RANK = {"8k": 6, "4k": 5, "2160": 5, "1440": 4, "1080": 3, "720": 2, "576": 1, "480": 1, "sd": 0}


def res_label(r):
    r = str(r or "").lower()
    if r in ("4k", "2160"):
        return "2160p"
    if r.isdigit():
        return r + "p"
    return (r or "SD").upper() if r in ("sd", "") else r


def view(md, section):
    media = []
    for m in md.get("Media") or []:
        parts = []
        for p in m.get("Part") or []:
            if not p.get("file"):
                continue
            parts.append({"plex": p["file"], "local": paths.to_local(p["file"]), "size": p.get("size") or 0,
                          "id": p.get("id")})
        if parts:
            media.append({"id": str(m.get("id")), "res": str(m.get("videoResolution") or ""),
                          "codec": (m.get("videoCodec") or "").lower(), "bitrate": m.get("bitrate") or 0,
                          "channels": m.get("audioChannels") or 0, "container": m.get("container") or "",
                          "parts": parts, "size": sum(p["size"] for p in parts), "hdr": False})
    guid = md.get("guid") or ""
    ids = _ids(md)
    return {
        "rk": str(md["ratingKey"]),
        "section": str(section["key"]),
        "section_title": section.get("title", ""),
        "title": md.get("title") or "",
        "original": md.get("originalTitle") or "",
        "year": md.get("year"),
        "edition": md.get("editionTitle") or "",
        "ids": ids,
        "guid": guid,
        "matched": bool(ids) or (guid.startswith(("plex://", "com.plexapp.agents")) and "local://" not in guid
                                 and "none://" not in guid),
        "thumb": md.get("thumb") or "",
        "art": md.get("art") or "",
        "watched": bool(md.get("viewCount")),
        "in_progress": bool(md.get("viewOffset")),
        "media": media,
    }


def item_ref(v):
    return {"rk": v["rk"], "title": v["title"], "year": v["year"]}


def _issue(kind, movies, summary, key, changes=None, plan=None, fix=None, note=None, folders=(), **extra):
    group, _label, _moves, _about = KINDS[kind]
    return {"id": _id(kind, key), "kind": kind, "group": group, "summary": summary,
            "items": [item_ref(v) for v in movies], "folders": sorted(set(folders)), "changes": changes or [],
            "plan": plan or [], "fix": fix, "note": note, **extra}


def _title(v):
    return f"{v['title']} ({v['year']})" if v["year"] else v["title"]


# ---------------------------------------------------------------- the scan

class Library:
    """Everything the checks need, worked out once per scan."""

    def __init__(self, views, roots, settings):
        self.views = views
        self.roots = [r.rstrip("/") for r in roots]
        self.settings = settings
        self.owners = defaultdict(set)    # folder -> rating keys with a video directly in it
        self.part_paths = set()
        for v in views:
            for m in v["media"]:
                for p in m["parts"]:
                    self.owners[os.path.dirname(p["local"])].add(v["rk"])
                    self.part_paths.add(p["local"])
        self._listing = {}
        layout = settings.get("layout", "auto")
        if layout == "auto":  # most movies loose at the top of the library -> that's how they're kept
            loose = sum(1 for v in views if v["media"] and self.is_root(os.path.dirname(v["media"][0]["parts"][0]["local"])))
            layout = "flat" if views and loose >= len(views) / 2 else "folders"
        self.flat = layout == "flat"

    def shared(self, folder):
        return self.is_root(folder) or len(self.owners.get(folder, ())) > 1

    def rel(self, p):
        return paths.rel_to(p, self.roots)

    def is_root(self, folder):
        return folder.rstrip("/") in self.roots

    def files(self, folder):
        if folder not in self._listing:
            try:
                self._listing[folder] = sorted(f for f in os.listdir(folder) if os.path.isfile(os.path.join(folder, f)))
            except OSError:
                self._listing[folder] = []
        return self._listing[folder]

    def videos_in(self, folder):
        """Names of every video file directly in a folder (Plex's or not)."""
        return [f for f in self.files(folder) if is_video(f)] + \
            [os.path.basename(p) for p in self.part_paths if os.path.dirname(p) == folder and not
             os.path.exists(p)]

    def own(self, folder, rk):
        return not self.is_root(folder) and self.owners.get(folder) == {rk}

    def trash_dst(self, kind, p):
        return os.path.join(TRASH, kind, self.rel(p) or os.path.basename(p))


def run(views, roots, settings, details=None):
    """views: from view(); roots: local library folders; details(rk) -> full Plex item (for HDR info)."""
    lib = Library(views, roots, settings)
    issues, stale = [], 0
    usable, missing = [], []
    for v in views:
        if not v["media"]:
            continue
        if any(not os.path.exists(p["local"]) for m in v["media"] for p in m["parts"]):
            stale += 1  # Plex hasn't caught up with a change, or PATH_MAP is off; look again next scan
            if len(missing) < 40:
                missing.append(v["media"][0]["parts"][0]["plex"])
            continue
        usable.append(v)

    issues += _duplicates(lib, usable, details)
    shared = set()
    for v in usable:
        folders = {os.path.dirname(p["local"]) for m in v["media"] for p in m["parts"]}
        if len(folders) != 1:
            continue  # copies in several folders: sort out the duplicate first
        folder = folders.pop()
        issues += _naming(lib, v, folder)
        own = lib.own(folder, v["rk"])
        if own:
            issues += _junk(lib, [v], folder)
        else:
            shared.add(folder)
        if v["matched"] and (own or lib.flat):
            issues += _artwork(lib, v, folder, own)
    if lib.flat:
        for folder in sorted(shared):
            issues += _junk(lib, [], folder)
    # Only a movie's own folder ties issues together; a folder shared by many movies doesn't.
    for i in issues:
        i["folders"] = [f for f in i["folders"] if not lib.shared(f)]
    return {"issues": issues, "movies": len(views), "stale": stale, "missing": missing,
            "layout": "flat" if lib.flat else "folders"}


# ---------------------------------------------------------------- naming

def _version_stems(lib, v, folder, stem):
    """Wanted stem for every part of this movie in the folder: {local_path: stem}."""
    media = [m for m in v["media"] if any(os.path.dirname(p["local"]) == folder for p in m["parts"])]
    out, labels = {}, {}
    if len(media) > 1:
        for m in media:
            lab = res_label(m["res"])
            if lab in labels.values():
                lab = f"{lab} {m['codec']}".strip()
            n = 2
            base_lab = lab
            while lab in labels.values():
                lab, n = f"{base_lab} ({n})", n + 1
            labels[m["id"]] = lab
    taken = set()
    for m in media:
        mstem = f"{stem} - {labels[m['id']]}" if m["id"] in labels else stem
        for i, p in enumerate(m["parts"], 1):
            cur = split_ext(os.path.basename(p["local"]))[0]
            want = f"{mstem} - pt{i}" if len(m["parts"]) > 1 else mstem
            # Keep names that already follow the rules in their own way ("Heat (1995) - Remux", "… - cd1").
            if len(media) > 1 and cur.startswith(stem + " - ") and cur not in taken:
                want = cur
            elif len(m["parts"]) > 1 and cur.startswith(mstem + " - ") and \
                    naming.re.search(r"(cd|disc|disk|dvd|part|pt)\s?\d+$", cur, naming.re.I) and cur not in taken:
                want = cur
            taken.add(want)
            out[p["local"]] = want
    return out


def _layout(lib, v, folder, title, year, own, inplace=False):
    """Moves that give this movie the standard layout. Returns (moves, target_folder, first_file_after).

    inplace: only rename the files where they are (movies kept together in one folder). Raises Conflict.
    """
    st = lib.settings
    fname = naming.folder_name(title, year, v["edition"], v["ids"], st.get("naming_id", "none"))
    stem = naming.file_stem(title, year, v["edition"])
    stems = _version_stems(lib, v, folder, stem)
    files = lib.files(folder)
    moves = []
    target = folder if own or inplace else os.path.join(folder, fname)
    if own and not naming.folder_ok(os.path.basename(folder), fname, st.get("naming_id", "none")):
        target = os.path.join(os.path.dirname(folder), fname)
    renamed = {}
    here = lib.videos_in(folder)
    for src, want in stems.items():
        cur = split_ext(os.path.basename(src))[0]
        renamed[src] = os.path.join(folder, want + os.path.splitext(src)[1])
        for side in naming.sidecars(files, cur, exclude=set(here), other_stems=[split_ext(h)[0] for h in here]):
            renamed[os.path.join(folder, side)] = os.path.join(folder, want + side[len(cur):])
    first = v["media"][0]["parts"][0]["local"]
    if own or inplace:
        for src, dst in renamed.items():
            if src != dst:
                moves.append([src, dst])
        if target != folder:
            moves.append([folder, target])
        after = os.path.join(target, os.path.basename(renamed.get(first, first)))
    else:
        for src, dst in renamed.items():
            moves.append([src, os.path.join(target, os.path.basename(dst))])
        after = os.path.join(target, os.path.basename(renamed.get(first, first)))
    _check_targets(moves, folder, target)
    return moves, target, after


class Conflict(Exception):
    pass


def _check_targets(moves, folder, target):
    srcs = {s for s, _ in moves}
    for s, d in moves:
        if d in srcs or s == d:
            continue
        if os.path.exists(d) and not _same_entry(s, d):
            raise Conflict(f"“{os.path.basename(d)}” already exists")
    dsts = [d for _, d in moves]
    if len(dsts) != len(set(dsts)):
        raise Conflict("two files would end up with the same name")


def _same_entry(a, b):
    try:
        return os.path.samefile(a, b)  # same file, name differs only in case (case-insensitive share)
    except OSError:
        return False


def _plan_lines(lib, moves):
    out = []
    for s, d in moves:
        if os.path.dirname(s) == os.path.dirname(d):
            out.append(f"Rename{' folder' if os.path.isdir(s) else ''}: {os.path.basename(s)} → {os.path.basename(d)}")
        elif d.startswith(TRASH):
            out.append(f"To trash: {lib.rel(s)}")
        else:
            out.append(f"Move: {lib.rel(s)} → {lib.rel(d)}")
    return out


def _refresh_dirs(v, folder, target):
    """Plex folders to rescan after the move (the parent covers a renamed folder)."""
    dirs = {os.path.dirname(folder) if target != folder and os.path.dirname(target) == os.path.dirname(folder)
            else folder}
    if os.path.dirname(target) != os.path.dirname(folder):
        dirs.add(os.path.dirname(target))
    return [[v["section"], paths.to_plex(d)] for d in sorted(dirs)]


def _layout_issue(lib, v, folder, own, title, year, kind, summary, note=None, inplace=False):
    try:
        moves, target, after = _layout(lib, v, folder, title, year, own, inplace)
    except Conflict as e:
        return [_issue("wrong_match" if kind == "unmatched" else kind, [v],
                       f"{summary} – can't fix: {e}", (v["rk"], folder, "conflict"),
                       note=f"{e} in {lib.rel(os.path.dirname(folder) if own else folder)}. Sort that out by hand, "
                            "then scan again.", folders=[folder])]
    if not moves:
        return []
    if own:
        changes = [{"field": "Now", "before": lib.rel(folder), "after": lib.rel(target)}]
    else:
        changes = [{"field": "Now", "before": lib.rel(v["media"][0]["parts"][0]["local"]), "after": lib.rel(after)}]
    fix = {"type": "moves", "moves": moves, "refresh": _refresh_dirs(v, folder, target), "rk": v["rk"]}
    return [_issue(kind, [v], summary, (v["rk"], folder, "layout"), changes=changes, plan=_plan_lines(lib, moves),
                   fix=fix, note=note, folders=[folder, target])]


def _naming(lib, v, folder):
    own = lib.own(folder, v["rk"])
    first = os.path.basename(v["media"][0]["parts"][0]["local"])
    name_now = os.path.basename(folder) if own else first

    if not v["matched"]:
        got = naming.parse_release(name_now) or (naming.parse_release(first) if own else None)
        if not got:
            return [_issue("unmatched", [v], f"“{name_now}” isn't matched in Plex", (v["rk"], "nomatch"),
                           note="There's no year in the name to go on. Use … → Fix Match in Plex, or rename it to "
                                "“Title (Year)” yourself.", folders=[folder])]
        t, y = got
        moves_needed = _layout_issue(lib, v, folder, own, t, y, "unmatched",
                                     f"“{name_now}” isn't matched in Plex",
                                     note=f"Looks like {t} ({y}). After the rename, Plex usually matches it on its own.",
                                     inplace=lib.flat)
        if moves_needed:
            return moves_needed
        return [_issue("unmatched", [v], f"“{name_now}” isn't matched in Plex", (v["rk"], "nomatch"),
                       note="It's already named well. Use … → Fix Match in Plex to pick the right movie.",
                       folders=[folder])]

    if not v["year"]:
        return []
    ok, why = naming.name_matches_plex(name_now, [v["title"], v["original"]], v["year"])
    if not ok and own:  # a folder called "New folder" is fine if the file itself is named right
        ok = naming.name_matches_plex(first, [v["title"], v["original"]], v["year"])[0]
    if not ok:
        return [_issue("wrong_match", [v], f"{lib.rel(folder if own else os.path.join(folder, first))} → Plex says "
                       f"“{_title(v)}”", (v["rk"], folder, "mismatch"),
                       note=f"Nothing will be renamed: {why}. If Plex is wrong, use … → Fix Match there.",
                       folders=[folder])]
    if own:
        return _layout_issue(lib, v, folder, True, v["title"], v["year"], "rename", _title(v))
    if lib.flat:  # movies are kept together: just name the files right, where they are
        return _layout_issue(lib, v, folder, False, v["title"], v["year"], "rename", _title(v), inplace=True)
    where = "the top of the library" if lib.is_root(folder) else f"a folder shared with other movies ({lib.rel(folder)})"
    return _layout_issue(lib, v, folder, False, v["title"], v["year"], "own_folder", _title(v),
                         note=f"It's loose in {where}.")


# ---------------------------------------------------------------- junk

def _junk(lib, movies, folder):
    """Junk in one movie's own folder (movies=[v]) or in a folder many movies share (movies=[])."""
    found = naming.junk_in(folder, lib.part_paths)
    if not found:
        return []
    moves = [[p, lib.trash_dst("junk", p)] for p in found]
    names = [os.path.basename(p) + ("/" if os.path.isdir(p) else "") for p in found]
    where = _title(movies[0]) if movies else (lib.rel(folder) or "Movie folder")
    rk = movies[0]["rk"] if movies else ""
    return [_issue("junk", movies, f"{where}: {len(found)} junk file{'s' if len(found) > 1 else ''}",
                   (rk, folder, "junk", *names), plan=_plan_lines(lib, moves),
                   fix={"type": "moves", "moves": moves, "refresh": [], "rk": rk}, folders=[folder])]


# ---------------------------------------------------------------- artwork & nfo

def _artwork(lib, v, folder, own=True):
    """Own folder: poster.jpg, fanart.jpg, movie.nfo. Shared folder: named after the movie's file,
    "Heat (1995)-poster.jpg", "Heat (1995)-fanart.jpg", "Heat (1995).nfo" (what Kodi and Jellyfin read)."""
    files = lib.files(folder)
    stems = [split_ext(os.path.basename(p["local"]))[0] for m in v["media"] for p in m["parts"]]
    main = stems[0]
    names = {"poster": "poster", "fanart": "fanart", "nfo": "movie.nfo"} if own else \
        {"poster": f"{main}-poster", "fanart": f"{main}-fanart", "nfo": f"{main}.nfo"}
    want, plan, missing = {}, [], []
    if not naming.has_poster(files, stems, shared=not own):
        missing.append("poster")
        if v["thumb"]:
            want["poster"] = v["thumb"]
            plan.append(f"Save Plex's poster as {names['poster']}.jpg")
    if not naming.has_fanart(files, stems, shared=not own):
        missing.append("fanart")
        if v["art"]:
            want["fanart"] = v["art"]
            plan.append(f"Save Plex's background as {names['fanart']}.jpg")
    if not naming.has_nfo(folder, files, stems=None if own else stems):
        missing.append("NFO")
        want["nfo"] = True
        plan.append(f"Write {names['nfo']} (title, year, plot, genres, cast, crew, IMDb/TMDB ids)")
    if not want:
        return []
    changes = []
    if "poster" in want:
        changes.append({"field": "Poster", "before": "none", "after": "/api/image?path=" + want["poster"], "image": True})
    plan.append(f"In: {lib.rel(folder)}")
    return [_issue("artwork", [v], f"{_title(v)}: no {', '.join(missing)}", (v["rk"], folder, "art", *sorted(want)),
                   changes=changes, plan=plan, folders=[folder],
                   fix={"type": "artwork", "rk": v["rk"], "folder": folder, "want": want, "names": names})]


# ---------------------------------------------------------------- duplicates

def _hdr(detail, media_id):
    for m in (detail or {}).get("Media") or []:
        if str(m.get("id")) != media_id:
            continue
        for p in m.get("Part") or []:
            for s in p.get("Stream") or []:
                if s.get("streamType") != 1:
                    continue
                t = (s.get("displayTitle") or "") + " " + (s.get("extendedDisplayTitle") or "")
                if s.get("DOVIPresent") or "HDR" in t or "Dolby Vision" in t or \
                        s.get("colorTrc") in ("smpte2084", "arib-std-b67"):
                    return "DV" if s.get("DOVIPresent") or "Dolby Vision" in t else "HDR"
    return ""


def _score(c):
    res = RES_RANK.get(c["res"].lower(), 0)
    codec = 1 if c["codec"] in ("hevc", "h265", "av1") else 0
    return (res, 1 if c["hdr"] else 0, c["bitrate"], codec, c["channels"], c["size"])


def _gb(n):
    if not n:
        return ""
    return f"{n / 1e9:.1f} GB" if n >= 1e9 else f"{max(1, round(n / 1e6))} MB"


def _dup_key(v, m, keep_versions):
    ids = v["ids"]
    k = ("imdb", ids["imdb"]) if "imdb" in ids else ("tmdb", ids["tmdb"]) if "tmdb" in ids else ("guid", v["guid"])
    k = (*k, naming.fold(v["edition"]))
    if keep_versions:
        k = (*k, min(RES_RANK.get(m["res"].lower(), 0), 5))
    return k


def _duplicates(lib, views, details):
    groups = defaultdict(list)
    for v in views:
        if not v["matched"]:
            continue
        for m in v["media"]:
            groups[_dup_key(v, m, lib.settings.get("keep_versions"))].append((v, m))
    out = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        real = defaultdict(list)
        for v, m in members:
            real[tuple(sorted(os.path.realpath(p["local"]) for p in m["parts"]))].append((v, m))
        movies = list({v["rk"]: v for v, _ in members}.values())
        if len(real) < len(members):
            libs = sorted({v["section_title"] for v, _ in members})
            out.append(_issue("same_file", movies, f"{_title(members[0][0])} is in {' and '.join(libs)}",
                              ("same", *key), note="Both list the same files on disk. Remove one library's folder in "
                                                    "Plex (Settings → Manage → Libraries)."))
            members = [ms[0] for ms in real.values()]
            if len(members) < 2:
                continue
        copies = []
        cache = {}
        for v, m in members:
            if details and v["rk"] not in cache:
                try:
                    cache[v["rk"]] = details(v["rk"])
                except Exception as e:  # HDR info is a nice-to-have
                    log.debug("details for %s: %s", v["rk"], e)
                    cache[v["rk"]] = None
            m["hdr"] = _hdr(cache.get(v["rk"]), m["id"])
            copies.append((v, m))
        copies.sort(key=lambda c: _score(c[1]), reverse=True)
        clist, folders = [], []
        for v, m in copies:
            moves = _copy_removal(lib, v, m)
            first = m["parts"][0]["local"]
            folders.append(os.path.dirname(first))
            tags = [res_label(m["res"]), m["hdr"], (m["codec"] or "").upper(),
                    f"{m['channels']}.1" if m["channels"] >= 6 else (f"{m['channels']}ch" if m["channels"] else ""),
                    _gb(m["size"]), f"{len(m['parts'])} parts" if len(m["parts"]) > 1 else ""]
            clist.append({"id": m["id"], "rk": v["rk"], "rel": lib.rel(first), "tags": [t for t in tags if t],
                          "watched": v["watched"], "progress": v["in_progress"], "edition": v["edition"],
                          "moves": moves, "plan": _plan_lines(lib, moves),
                          "refresh": [[v["section"], paths.to_plex(os.path.dirname(os.path.dirname(first)))]]})
        v0 = copies[0][0]
        note = None
        if any(c["progress"] for c in clist[1:]):
            note = "A copy that would go to the trash is part-watched in Plex."
        out.append(_issue("duplicate", movies, f"{_title(v0)}: {len(clist)} copies", ("dup", *key),
                          plan=clist[1]["plan"] if len(clist) > 1 else [], note=note, folders=folders,
                          copies=clist, fix={"type": "dedupe", "keep": clist[0]["id"]}))
    return out


def _copy_removal(lib, v, m):
    """Moves that take one copy out of the library: its whole folder if nothing else lives there."""
    folder = os.path.dirname(m["parts"][0]["local"])
    same_folder = all(os.path.dirname(p["local"]) == folder for p in m["parts"])
    others_here = [p for p in lib.part_paths if os.path.dirname(p) == folder and p not in {x["local"] for x in m["parts"]}]
    nested = any(p.startswith(folder + "/") and os.path.dirname(p) != folder for p in lib.part_paths)
    if same_folder and not others_here and not nested and not lib.is_root(folder):
        return [[folder, lib.trash_dst("duplicates", folder)]]
    moves = []
    for p in m["parts"]:
        d = os.path.dirname(p["local"])
        moves.append([p["local"], lib.trash_dst("duplicates", p["local"])])
        here = lib.videos_in(d)
        stem = split_ext(os.path.basename(p["local"]))[0]
        for side in naming.sidecars(lib.files(d), stem, exclude=set(here), other_stems=[split_ext(h)[0] for h in here]):
            moves.append([os.path.join(d, side), lib.trash_dst("duplicates", os.path.join(d, side))])
    return moves
