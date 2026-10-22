"""Plex paths <-> paths inside this container."""
import os

from . import config


def maps():
    out = []
    for pair in config.PATH_MAP.split(","):
        if "=" in pair:
            a, b = pair.split("=", 1)
            out.append((a.strip().rstrip("/") or "/", b.strip().rstrip("/") or "/"))
    return out


def _swap(path, pairs):
    best = None
    for a, b in pairs:
        if path == a or path.startswith(a.rstrip("/") + "/"):
            if best is None or len(a) > len(best[0]):
                best = (a, b)
    if not best:
        return path
    a, b = best
    return (b.rstrip("/") + path[len(a.rstrip("/")):]) or "/"


def to_local(plex_path):
    return _swap(plex_path, maps())


def to_plex(local_path):
    return _swap(local_path, [(b, a) for a, b in maps()])


def library_mounted():
    roots = [b for _a, b in maps()]
    return bool(roots) and any(os.path.isdir(r) for r in roots)


def rel_to(path, roots):
    """Path shown on the page: relative to its library folder."""
    best = ""
    for r in roots:
        r = r.rstrip("/")
        if (path == r or path.startswith(r + "/")) and len(r) > len(best):
            best = r
    return path[len(best):].lstrip("/") if best else path
