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


def suggest_map(plex_paths, mounts):
    """Work out PATH_MAP from files Plex lists that can't be found here.

    For "/data/Movies/Heat (1995)/Heat (1995).mkv" with the library mounted at /movies, finds
    /movies/Heat (1995)/Heat (1995).mkv and returns "/data/Movies=/movies". None if nothing fits.
    """
    from collections import Counter
    votes = Counter()
    for p in plex_paths:
        parts = [x for x in p.split("/") if x]
        for i in range(1, len(parts)):
            tail = parts[i:]
            hit = next((m for m in mounts if os.path.exists(os.path.join(m, *tail))), None)
            if hit:
                votes[("/" + "/".join(parts[:i]), hit.rstrip("/") or "/")] += 1
                break
    if not votes:
        return None
    (plex_root, local), n = votes.most_common(1)[0]
    return {"map": f"{plex_root}={local}", "matched": n, "checked": len(plex_paths)}


def mounts():
    """Folders this container could hold the library in: PATH_MAP's local side and common mounts."""
    seen = []
    for m in [b for _a, b in maps()] + ["/movies", "/media", "/data", "/mnt"]:
        if os.path.isdir(m) and m not in seen:
            seen.append(m)
    return seen
