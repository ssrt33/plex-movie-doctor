"""Scanning, applying and undoing, and the background schedule.

Scans only look. Nothing is changed unless you press Fix on the page.
"""
import logging
import os
import threading
import time
import uuid

from . import checks, config, fixes, paths, store
from .plex import Plex

log = logging.getLogger("doctor")
_run_lock = threading.Lock()   # one scan or fix at a time
_wake = threading.Event()


def _step(text):
    with store.lock():
        store.get()["scan"]["step"] = text


def _sections(plex):
    secs = [s for s in plex.sections() if s.get("type") == "movie"]
    if config.LIBRARIES:
        want = {x.lower() for x in config.LIBRARIES}
        secs = [s for s in secs if str(s["key"]).lower() in want or s.get("title", "").lower() in want]
    return secs


def scan():
    """Read Plex and the files, run the checks, and list what needs your OK."""
    if not _run_lock.acquire(blocking=False):
        return {"busy": True}
    plex = Plex()
    try:
        with store.lock():
            st = store.get()
            st["scan"] = {"running": True, "step": "Connecting to Plex", "error": None}
            settings = dict(st["settings"])
        ident = plex.identity()
        with store.lock():
            store.get()["server"] = {"version": (ident.get("version") or "").split("-")[0], "url": plex.url}
        plex.wait_idle(timeout=300, step=_step)
        views, roots, libs = [], [], []
        for sec in _sections(plex):
            _step(f"Reading {sec.get('title')}")
            locs = [l["path"] for l in sec.get("Location") or []]
            roots += [paths.to_local(p) for p in locs]
            libs.append({"key": str(sec["key"]), "name": sec.get("title"), "folders": locs,
                         "local": [paths.to_local(p) for p in locs]})
            views += [checks.view(md, sec) for md in plex.movies(sec["key"])]
        _step(f"Checking {len(views)} movies")
        res = checks.run(views, roots, settings, details=plex.item)
        _publish(res, libs)
        return {"issues": len(res["issues"])}
    except Exception as e:
        log.exception("Scan failed")
        with store.lock():
            store.get()["scan"]["error"] = str(e)
        return {"error": str(e)}
    finally:
        with store.lock():
            st = store.get()
            st["scan"]["running"] = False
            st["scan"]["step"] = ""
            store.save()
        _run_lock.release()


def _publish(res, libs):
    with store.lock():
        st = store.get()
        ignored = st["ignored"]
        for i in res["issues"]:
            i["ignored"] = i["id"] in ignored
        seen = set(st.get("seen") or [])
        new = [i for i in res["issues"] if i["id"] not in seen and not i["ignored"]]
        st["issues"] = res["issues"]
        st["seen"] = [i["id"] for i in res["issues"]]
        st["movies"] = res["movies"]
        st["stale"] = res["stale"]
        st["missing_example"] = res["missing"][0] if res["missing"] else None
        st["suggest_map"] = paths.suggest_map(res["missing"], paths.mounts()) \
            if res["missing"] and res["stale"] >= res["movies"] / 2 else None
        st["libraries"] = libs
        st["last_scan"] = time.time()
        store.save()
    if new and config.NOTIFY_URL:
        from .util import notify
        notify("Movie Doctor", f"{len(new)} new thing(s) to look at in your movie library.")


def _overlaps(a, b):
    if {x["rk"] for x in a["items"]} & {x["rk"] for x in b["items"]}:
        return True
    for f in a.get("folders", []):
        for g in b.get("folders", []):
            if f == g or f.startswith(g.rstrip("/") + "/") or g.startswith(f.rstrip("/") + "/"):
                return True
    return False


def fix_issue(issue_id, keep=None):
    """Apply one issue's fix (only ever from a click on the page)."""
    with store.lock():
        issue = next((i for i in store.get()["issues"] if i["id"] == issue_id), None)
    if not issue or not issue["fix"]:
        raise LookupError
    if not _run_lock.acquire(timeout=600):
        raise fixes.FixError("A scan or another fix is still running.")
    plex = Plex()
    try:
        with store.lock():
            store.get()["scan"] = {"running": True, "step": f"Fixing: {issue['summary']}", "error": None}
        plan = issue["plan"]
        if issue["fix"]["type"] == "dedupe":
            pick = keep or issue["fix"]["keep"]
            rels = {c["id"]: c for c in issue["copies"]}
            plan = [f"Kept: {rels[pick]['rel']}"] if pick in rels else []
            plan += [line for cid, c in rels.items() if cid != pick for line in c["plan"]]
        rec = fixes.apply(plex, issue, keep=keep)
        entry = {"id": uuid.uuid4().hex[:10], "issue": issue["id"], "kind": issue["kind"],
                 "summary": issue["summary"], "plan": plan, "record": rec, "undone": False, "at": time.time()}
        store.add_history(entry)
        with store.lock():
            st = store.get()
            # Anything touching the same movie or folder is out of date now; the next scan re-checks it.
            st["issues"] = [i for i in st["issues"] if i["id"] != issue_id and
                            (issue["kind"] == "artwork" or not _overlaps(i, issue))]
            store.save()
        return entry
    finally:
        with store.lock():
            store.get()["scan"]["running"] = False
        _run_lock.release()
        if issue["kind"] != "artwork":
            wake(later=45)  # give Plex a moment to pick up the move, then look again


def undo(entry_id, force=False):
    with store.lock():
        entry = next((h for h in store.get()["history"] if h["id"] == entry_id), None)
    if not entry or entry["undone"]:
        raise LookupError
    if not _run_lock.acquire(timeout=600):
        raise fixes.FixError("A scan or another fix is still running.")
    try:
        with store.lock():
            store.get()["scan"] = {"running": True, "step": f"Undoing: {entry['summary']}", "error": None}
        fixes.undo(Plex(), entry["record"], force=force)
        with store.lock():
            for h in store.get()["history"]:
                if h["id"] == entry_id:
                    h["undone"] = True
                    h["undone_at"] = time.time()
            # It'll show up again on the next scan; ignore it so it doesn't nag straight away.
            store.get()["ignored"][entry["issue"]] = {"summary": entry["summary"], "kind": entry["kind"],
                                                      "at": time.time(), "reason": "You undid this fix."}
            store.save()
    finally:
        with store.lock():
            store.get()["scan"]["running"] = False
        _run_lock.release()
        wake(later=45)


def ignore(issue_id, on=True):
    with store.lock():
        st = store.get()
        if on:
            issue = next((i for i in st["issues"] if i["id"] == issue_id), None)
            if not issue:
                raise LookupError
            st["ignored"][issue_id] = {"summary": issue["summary"], "kind": issue["kind"], "at": time.time()}
            issue["ignored"] = True
        else:
            st["ignored"].pop(issue_id, None)
            for i in st["issues"]:
                if i["id"] == issue_id:
                    i["ignored"] = False
        store.save()


def wake(later=0):
    if later:
        t = threading.Timer(later, _wake.set)
        t.daemon = True
        t.start()
    else:
        _wake.set()


def scheduler():
    """Scan at start-up and then every N hours (from settings)."""
    time.sleep(3)
    while True:
        if config.PLEX_TOKEN:
            scan()
        hours = max(0.25, float(store.get()["settings"].get("scan_every_hours") or 12))
        _wake.wait(timeout=hours * 3600)
        _wake.clear()


def trash_mounted():
    return os.path.ismount(config.TRASH_DIR)
