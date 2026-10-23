"""App state, kept in one JSON file under CONFIG_DIR."""
import copy
import json
import os
import threading
import time

from . import config

_lock = threading.RLock()
_state = None


def _path():
    return os.path.join(config.CONFIG_DIR, "state.json")


def _defaults():
    return {
        "settings": {
            "scan_every_hours": config.SCAN_EVERY_HOURS,
            "naming_id": config.NAMING_ID if config.NAMING_ID in ("none", "tmdb", "imdb") else "none",
            "keep_versions": config.KEEP_VERSIONS,
            "layout": config.LAYOUT if config.LAYOUT in ("auto", "folders", "flat") else "auto",
        },
        "issues": [],        # open issues from the last scan
        "seen": [],          # issue ids already reported, so notifications only mention new ones
        "ignored": {},       # issue id -> {summary, kind, at}
        "history": [],       # applied fixes, newest first
        "last_scan": None,
        "scan": {"running": False, "step": "", "error": None},
        "movies": 0,
        "stale": 0,
        "missing_example": None,  # one file Plex lists that isn't found here
        "suggest_map": None,      # PATH_MAP worked out from those files
        "libraries": [],
        "layout_used": None,
        "last_bulk": None,
        "server": None,
    }


def load():
    global _state
    with _lock:
        if _state is None:
            os.makedirs(config.CONFIG_DIR, exist_ok=True)
            st = _defaults()
            try:
                with open(_path()) as f:
                    saved = json.load(f)
                for k in st:
                    if k in saved:
                        st[k] = saved[k]
                st["settings"] = {**_defaults()["settings"], **saved.get("settings", {})}
            except (OSError, ValueError):
                pass
            st["scan"] = {"running": False, "step": "", "error": None}
            _state = st
        return _state


def save():
    with _lock:
        tmp = _path() + ".tmp"
        with open(tmp, "w") as f:
            json.dump(_state, f, indent=1)
        os.replace(tmp, _path())


def lock():
    return _lock


def get():
    return load()


def snapshot():
    with _lock:
        return copy.deepcopy(load())


def add_history(entry):
    with _lock:
        st = load()
        entry.setdefault("at", time.time())
        st["history"].insert(0, entry)
        del st["history"][500:]
        save()
