"""Applying fixes, and undoing them.

Fixes only ever move files (renames, into a new folder, or into the trash folder) or create new
ones (poster.jpg, fanart.jpg, movie.nfo). Nothing is deleted: undoing a created file moves it to
the trash too. Every apply returns a record of exactly what it did, so it can be put back.
"""
import logging
import os
import shutil
import time
import uuid
import xml.etree.ElementTree as ET

from . import config, paths
from .checks import TRASH

log = logging.getLogger("doctor")


class FixError(Exception):
    pass


class Stale(FixError):
    """The files changed since the scan."""


def can_move():
    if not paths.library_mounted():
        return False, "The movie library isn't mounted in this container (check PATH_MAP and volumes)."
    if not os.path.isdir(config.TRASH_DIR) or not os.access(config.TRASH_DIR, os.W_OK):
        return False, f"The trash folder {config.TRASH_DIR} is missing or not writable."
    return True, ""


def _chown(path):
    try:
        os.chown(path, config.PUID, config.PGID)
    except OSError:
        pass


def _makedirs(path, made):
    missing = []
    p = os.path.abspath(path)
    while not os.path.exists(p):
        missing.append(p)
        p = os.path.dirname(p)
    for p in reversed(missing):
        os.mkdir(p)
        _chown(p)
        made.append(p)


def _same(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def _move(src, dst, made):
    if not os.path.lexists(src):
        raise Stale(f"{src} isn't there any more. Scan again.")
    if os.path.lexists(dst) and not _same(src, dst):
        raise FixError(f"Won't overwrite {dst}")
    _makedirs(os.path.dirname(dst), made)
    if os.path.lexists(dst):  # only the case of the name changes, on a case-insensitive share
        tmp = f"{src}.pmd-{uuid.uuid4().hex[:6]}"
        os.rename(src, tmp)
        os.rename(tmp, dst)
    else:
        shutil.move(src, dst)


def _put_back(done, made):
    errors = []
    for src, dst in reversed(done):
        try:
            if os.path.lexists(dst) and (not os.path.lexists(src) or _same(src, dst)):
                _move(dst, src, [])
            else:
                errors.append(f"{dst} → {src}")
        except Exception as e:  # keep going; report at the end
            errors.append(f"{dst} → {src}: {e}")
    for d in sorted(made, key=len, reverse=True):
        try:
            os.rmdir(d)  # only if empty
        except OSError:
            pass
    return errors


def _trash_root():
    return os.path.join(config.TRASH_DIR, time.strftime("%Y-%m-%d %H%M%S"))


def run_moves(moves):
    """Move every [src, dst] in order. On any failure, put back what was moved and raise."""
    ok, why = can_move()
    if not ok:
        raise FixError(why)
    trash = _trash_root()
    moves = [[s, d.replace(TRASH, trash, 1) if d.startswith(TRASH) else d] for s, d in moves]
    for s, _d in moves:
        if not os.path.lexists(s):
            raise Stale(f"“{os.path.basename(s)}” isn't where it was at the last scan. Scan again.")
    done, made = [], []
    try:
        for s, d in moves:
            _move(s, d, made)
            done.append([s, d])
    except Exception as e:
        left = _put_back(done, made)
        msg = f"Stopped and put everything back: {e}"
        if left:
            msg = f"Stopped partway ({e}). Couldn't put back: {'; '.join(left)}"
        raise (e if isinstance(e, Stale) and not done else FixError(msg)) from e
    return done, made


def _refresh(plex, dirs):
    for section, plex_dir in dirs:
        try:
            plex.refresh_path(section, plex_dir)
        except Exception as e:  # Plex will notice on its next scan anyway
            log.warning("Couldn't ask Plex to rescan %s: %s", plex_dir, e)


# ---------------------------------------------------------------- moves (rename, own folder, junk, unmatched)

def apply_moves(plex, fix):
    done, made = run_moves(fix["moves"])
    _refresh(plex, fix.get("refresh", []))
    return {"type": "moves", "done": done, "made": made, "refresh": fix.get("refresh", [])}


def _now_at(path, later):
    """Where path is today, given the moves made after it (e.g. its folder was renamed later)."""
    for s, d in later:
        if path == s or path.startswith(s.rstrip("/") + "/"):
            path = d + path[len(s):]
    return path


def undo_moves(plex, rec, force=False):
    done = rec["done"]
    missing, taken = [], []
    for k, (s, d) in enumerate(done):
        later = done[k + 1:]
        if not os.path.lexists(_now_at(d, later)):
            missing.append(d)
        src_now = _now_at(s, later)
        if os.path.lexists(src_now) and not _same(src_now, _now_at(d, later)):
            taken.append(s)
    if (missing or taken) and not force:
        what = missing[0] if missing else taken[0]
        raise Stale(f"Things moved since this fix ({os.path.basename(what)}), so undoing it might not put "
                    "everything back. Undo what can be undone?")
    left = _put_back(rec["done"], rec["made"])
    _refresh(plex, rec.get("refresh", []))
    if left and not force:
        raise FixError(f"Couldn't put back: {'; '.join(left)}")


# ---------------------------------------------------------------- duplicates

def apply_dedupe(plex, fix, issue):
    copies = {c["id"]: c for c in issue["copies"]}
    keep = fix["keep"]
    if keep not in copies:
        raise FixError("That copy isn't part of this issue.")
    kept = copies[keep]
    moves, refresh = [], []
    for cid, c in copies.items():
        if cid == keep:
            continue
        for s, d in c["moves"]:
            # Never move the kept copy, even if it sits inside a folder planned for the trash.
            for km in kept["moves"]:
                if km[0] == s or km[0].startswith(s.rstrip("/") + "/"):
                    raise FixError("The copy you're keeping is inside a folder that would go to the trash. "
                                   "Move it out by hand first.")
            moves.append([s, d])
        refresh += c["refresh"]
    done, made = run_moves(moves)
    _refresh(plex, refresh)
    return {"type": "moves", "done": done, "made": made, "refresh": refresh}


# ---------------------------------------------------------------- artwork & nfo

def _ext_for(data, ctype):
    if data[:8] == b"\x89PNG\r\n\x1a\n" or "png" in ctype:
        return ".png"
    return ".jpg"


def _write_new(path, data, made_files):
    if os.path.lexists(path):
        return False
    tmp = path + ".pmd-tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    _chown(path)
    made_files.append(path)
    return True


def _tags(md, key):
    return [t.get("tag") for t in md.get(key) or [] if t.get("tag")]


def nfo_xml(md):
    """A Kodi/Jellyfin movie.nfo from a full Plex item."""
    m = ET.Element("movie")

    def add(tag, val, **attrs):
        if val not in (None, "", []):
            e = ET.SubElement(m, tag, **{k: str(v) for k, v in attrs.items()})
            e.text = str(val)
            return e

    add("title", md.get("title"))
    add("originaltitle", md.get("originalTitle") or md.get("title"))
    if md.get("titleSort") and md.get("titleSort") != md.get("title"):
        add("sorttitle", md.get("titleSort"))
    add("year", md.get("year"))
    add("plot", md.get("summary"))
    add("tagline", md.get("tagline"))
    if md.get("duration"):
        add("runtime", round(md["duration"] / 60000))
    add("mpaa", md.get("contentRating"))
    add("premiered", md.get("originallyAvailableAt"))
    add("studio", md.get("studio"))
    if md.get("editionTitle"):
        add("edition", md.get("editionTitle"))
    ids = {}
    for g in md.get("Guid") or []:
        if "://" in g.get("id", ""):
            k, v = g["id"].split("://", 1)
            ids[k] = v
    if md.get("audienceRating") or md.get("rating"):
        r = ET.SubElement(m, "ratings")
        if md.get("audienceRating"):
            e = ET.SubElement(r, "rating", name="audience", max="10", default="true")
            ET.SubElement(e, "value").text = str(md["audienceRating"])
        if md.get("rating"):
            e = ET.SubElement(r, "rating", name="critic", max="10")
            ET.SubElement(e, "value").text = str(md["rating"])
    for k in ("imdb", "tmdb", "tvdb"):
        if k in ids:
            add("uniqueid", ids[k], type=k, default="true" if k == ("imdb" if "imdb" in ids else "tmdb") else "false")
    if "imdb" in ids:
        add("id", ids["imdb"])
    for g in _tags(md, "Genre"):
        add("genre", g)
    for c in _tags(md, "Country"):
        add("country", c)
    for c in _tags(md, "Collection"):
        s = ET.SubElement(m, "set")
        ET.SubElement(s, "name").text = c
    for d in _tags(md, "Director"):
        add("director", d)
    for w in _tags(md, "Writer"):
        add("credits", w)
    for i, r in enumerate(md.get("Role") or []):
        a = ET.SubElement(m, "actor")
        ET.SubElement(a, "name").text = r.get("tag", "")
        if r.get("role"):
            ET.SubElement(a, "role").text = r["role"]
        ET.SubElement(a, "order").text = str(i)
    ET.indent(m)
    body = ET.tostring(m, encoding="unicode")
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            "<!-- written by Plex Movie Doctor from your Plex server -->\n" + body + "\n").encode("utf-8")


def apply_artwork(plex, fix):
    folder = fix["folder"]
    if not os.path.isdir(folder):
        raise Stale("The movie's folder has moved since the last scan. Scan again.")
    if not os.access(folder, os.W_OK):
        raise FixError(f"Can't write to {folder}. Give the app's user (PUID {config.PUID}) write access.")
    md = plex.item(fix["rk"])
    if not md:
        raise Stale("That movie isn't in Plex any more.")
    made, skipped = [], []
    try:
        for kind, name in (("poster", "poster"), ("fanart", "fanart")):
            src = fix["want"].get(kind)
            if not src:
                continue
            src = md.get("thumb" if kind == "poster" else "art") or src  # newest image path
            data, ctype = plex.image(src)
            if not data.startswith((b"\x89PNG", b"\xff\xd8")):  # not an image (an error page, say)
                skipped.append(kind)
                continue
            if not _write_new(os.path.join(folder, name + _ext_for(data, ctype)), data, made):
                skipped.append(kind)
        if fix["want"].get("nfo"):
            if not _write_new(os.path.join(folder, "movie.nfo"), nfo_xml(md), made):
                skipped.append("nfo")
    except Exception as e:
        for p in made:  # we made these moments ago; nothing of yours
            try:
                os.remove(p)
            except OSError:
                pass
        raise FixError(f"Nothing was saved: {e}") from e
    if not made:
        raise Stale("Those files exist now. Scan again.")
    return {"type": "created", "files": made}


def undo_created(plex, rec, force=False):
    moves = [[p, os.path.join(TRASH, "undone", os.path.basename(os.path.dirname(p)), os.path.basename(p))]
             for p in rec["files"] if os.path.exists(p)]
    if moves:
        run_moves(moves)


# ---------------------------------------------------------------- dispatch

def apply(plex, issue, keep=None):
    fix = dict(issue["fix"])
    if fix["type"] == "dedupe":
        if keep:
            fix["keep"] = keep
        return apply_dedupe(plex, fix, issue)
    if fix["type"] == "moves":
        return apply_moves(plex, fix)
    if fix["type"] == "artwork":
        return apply_artwork(plex, fix)
    raise FixError(f"Unknown fix {fix['type']}")


UNDO = {"moves": undo_moves, "created": undo_created}


def undo(plex, rec, force=False):
    return UNDO[rec["type"]](plex, rec, force=force)
