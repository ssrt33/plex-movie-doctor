"""Web page + JSON API."""
import logging
import os
import threading

from flask import Flask, Response, jsonify, request, send_from_directory

from . import config, engine, fixes, store
from .checks import GROUP_ORDER, KINDS
from .plex import Plex, PlexError

app = Flask(__name__, static_folder=None)
app.json.sort_keys = False  # keep the checks in their natural order on the page
STATIC = os.path.join(os.path.dirname(__file__), "static")
log = logging.getLogger("doctor")


def err(msg, code=400):
    return jsonify({"error": msg}), code


@app.errorhandler(LookupError)
def _missing(_):
    return err("Not found (it may have been fixed already – refresh)", 404)


@app.errorhandler(fixes.FixError)
def _fix_error(e):
    return err(str(e), 409)


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/api/state")
def state():
    s = store.snapshot()
    ok, why = fixes.can_move()
    s["can_move"], s["can_move_reason"] = ok, why
    s["trash_mounted"] = engine.trash_mounted()
    s["configured"] = bool(config.PLEX_TOKEN)
    s["plex_url"] = config.PLEX_URL
    s["groups"] = GROUP_ORDER
    s["kinds"] = {k: {"group": g, "label": l, "moves_files": mv, "about": about}
                  for k, (g, l, mv, about) in KINDS.items()}
    s.pop("seen", None)
    for h in s["history"]:
        h.pop("record", None)  # internal undo data
    for i in s["issues"]:
        for c in i.get("copies") or []:
            c.pop("moves", None)
    return jsonify(s)


@app.get("/api/image")
def image():
    """Plex posters for the preview, fetched here so the token never reaches the browser."""
    path = request.args.get("path", "")
    if not path.startswith("/library/metadata/") or ".." in path:
        return err("Bad image path")
    try:
        data, ctype = Plex().image(path)
    except PlexError as e:
        return err(str(e), 502)
    return Response(data, mimetype=ctype or "image/jpeg", headers={"Cache-Control": "max-age=86400"})


@app.post("/api/scan")
def scan_now():
    if store.get()["scan"]["running"]:
        return err("Already busy", 409)
    threading.Thread(target=engine.scan, daemon=True).start()
    return jsonify({"started": True})


@app.post("/api/issues/<issue_id>/fix")
def fix(issue_id):
    body = request.get_json(silent=True) or {}
    engine.fix_issue(issue_id, keep=body.get("keep"))
    return jsonify({"ok": True})


@app.post("/api/issues/fix-kind/artwork")
def fix_artwork():
    """Save artwork/NFO for every open artwork issue. Only creates files, never moves any."""
    ids = [i["id"] for i in store.get()["issues"] if i["kind"] == "artwork" and i["fix"] and not i.get("ignored")]
    done, failed = 0, []
    for i in ids:
        try:
            engine.fix_issue(i)
            done += 1
        except (fixes.FixError, LookupError, PlexError) as e:
            failed.append(str(e))
    return jsonify({"fixed": done, "failed": failed})


@app.post("/api/issues/<issue_id>/ignore")
def ignore(issue_id):
    body = request.get_json(silent=True) or {}
    engine.ignore(issue_id, on=bool(body.get("on", True)))
    return jsonify({"ok": True})


@app.post("/api/history/<entry_id>/undo")
def undo(entry_id):
    force = bool((request.get_json(silent=True) or {}).get("force"))
    try:
        engine.undo(entry_id, force=force)
    except fixes.Stale as e:
        return jsonify({"error": str(e), "can_force": True}), 409
    return jsonify({"ok": True})


@app.post("/api/settings")
def settings():
    body = request.get_json(force=True) or {}
    rescan = False
    with store.lock():
        s = store.get()["settings"]
        if "scan_every_hours" in body:
            try:
                s["scan_every_hours"] = min(168.0, max(0.25, float(body["scan_every_hours"])))
            except (TypeError, ValueError):
                return err("Scan interval must be a number of hours")
        if "naming_id" in body:
            if body["naming_id"] not in ("none", "tmdb", "imdb"):
                return err("Pick none, tmdb or imdb")
            rescan |= s["naming_id"] != body["naming_id"]
            s["naming_id"] = body["naming_id"]
        if "keep_versions" in body:
            rescan |= s["keep_versions"] != bool(body["keep_versions"])
            s["keep_versions"] = bool(body["keep_versions"])
        store.save()
    if rescan or "scan_every_hours" in body:
        engine.wake()
    return jsonify(store.get()["settings"])


def main():
    logging.basicConfig(level=config.LOG_LEVEL, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("waitress").setLevel(logging.WARNING)
    store.load()
    if not config.PLEX_TOKEN:
        log.warning("PLEX_TOKEN isn't set. See the README for how to find your Plex token.")
    ok, why = fixes.can_move()
    if not ok:
        log.warning("Fixes that move files are off: %s", why)
    elif not engine.trash_mounted():
        log.warning("%s isn't a mounted folder; anything moved there is lost when the container is rebuilt.",
                    config.TRASH_DIR)
    threading.Thread(target=engine.scheduler, daemon=True).start()
    from waitress import serve
    log.info("Movie Doctor on port %s, checking %s", config.PORT, config.PLEX_URL)
    serve(app, host="0.0.0.0", port=config.PORT, threads=8)
