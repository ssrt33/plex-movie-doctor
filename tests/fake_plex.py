"""A tiny stand-in for a Plex server, for tests and trying the page without a real Plex.

It scans a folder the way Plex roughly does (ignoring samples and extras folders), matches each
movie against a small catalog by name, and answers the handful of API calls the doctor makes.
Run it on its own:  python -m tests.fake_plex /path/to/movies 32499
"""
import base64
import hashlib
import json
import os
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from doctor import naming  # noqa: E402

CATALOG = [
    {"title": "Heat", "year": 1995, "imdb": "tt0113277", "tmdb": "949"},
    {"title": "The Matrix", "year": 1999, "imdb": "tt0133093", "tmdb": "603"},
    {"title": "Alien", "year": 1979, "imdb": "tt0078748", "tmdb": "348"},
    {"title": "Alien Resurrection", "year": 1997, "imdb": "tt0118583", "tmdb": "8078"},
    {"title": "Inception", "year": 2010, "imdb": "tt1375666", "tmdb": "27205"},
    {"title": "Dune", "year": 2021, "imdb": "tt1160419", "tmdb": "438631"},
    {"title": "Mission: Impossible", "year": 1996, "imdb": "tt0117060", "tmdb": "954"},
    {"title": "Blade Runner", "year": 1982, "imdb": "tt0083658", "tmdb": "78"},
    {"title": "Coneheads", "year": 1993, "imdb": "tt0106598", "tmdb": "9612"},
    {"title": "Nacho Libre", "year": 2006, "imdb": "tt0457510", "tmdb": "9353"},
    {"title": "Jackass: Best and Last", "year": 2026, "imdb": "tt9999901", "tmdb": "999901"},
    {"title": "9", "year": 2009, "imdb": "tt0472033", "tmdb": "12244"},
    {"title": "Amélie", "year": 2001, "imdb": "tt0211915", "tmdb": "194", "original": "Le Fabuleux Destin d'Amélie Poulain"},
]
# A small amber PNG, standing in for a poster
IMG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAIAAAADCAIAAAA2iEnWAAAAEElEQVR4nGN4ucgLiBhQKAB3Ogr/QdKJyQAAAABJRU5ErkJggg==")


def _rk(key):
    return str(int(hashlib.sha1(key.encode()).hexdigest()[:6], 16))


class FakePlex:
    def __init__(self, root, plex_root=None, overrides=None, section_title="Movies"):
        self.root = root.rstrip("/")
        self.plex_root = (plex_root or root).rstrip("/")
        self.overrides = overrides or {}   # path substring -> catalog title (forces a "wrong" match)
        self.section_title = section_title
        self.refreshes = []
        self.items = {}
        self.scan()

    def _plex(self, p):
        return self.plex_root + p[len(self.root):]

    def _match(self, path):
        for sub, title in self.overrides.items():
            if sub in path:
                return next(c for c in CATALOG if c["title"] == title)
        folder = os.path.dirname(path)
        names = [os.path.basename(path)] + ([] if folder == self.root else [os.path.basename(folder)])
        for n in names:
            got = naming.parse_release(n)
            if not got:  # no year in the name: Plex still matches on the title alone
                t = naming.split_ext(n)[0] if naming.is_video(n) else n
                hit = [c for c in CATALOG if naming.fold(c["title"]) == naming.fold(t)]
                if len(hit) == 1:
                    return hit[0]
                continue
            t, y = got
            for c in CATALOG:
                if naming.fold(c["title"]) == naming.fold(t) and abs(c["year"] - y) <= 1:
                    return c
        return None

    def scan(self):
        items = {}
        for d, dirs, files in os.walk(self.root):
            dirs[:] = [x for x in dirs if x.lower() not in naming.EXTRAS_DIRS and x.lower() != "sample"]
            for f in sorted(files):
                if not naming.is_video(f) or naming.SAMPLE.search(f):
                    continue
                p = os.path.join(d, f)
                c = self._match(p)
                ed = re.search(r"\{edition-([^}]+)\}", p)
                key = f"{c['imdb']}|{ed.group(1) if ed else ''}" if c else "local|" + p
                it = items.setdefault(key, {"cat": c, "edition": ed.group(1) if ed else "", "media": []})
                res = "4k" if re.search(r"2160p|4k|uhd", f, re.I) else "720" if "720p" in f else "1080"
                it["media"].append({"id": int(_rk("m" + p)), "videoResolution": res,
                                    "videoCodec": "hevc" if res == "4k" else "h264",
                                    "bitrate": 40000 if res == "4k" else 8000 if res == "1080" else 3000,
                                    "audioChannels": 6, "container": f.rsplit(".", 1)[-1],
                                    "Part": [{"id": int(_rk("p" + p)), "file": self._plex(p), "size": os.path.getsize(p),
                                              "duration": 6000000}]})
        self.items = {}
        for key, it in items.items():
            rk = _rk(key)
            c = it["cat"]
            md = {"ratingKey": rk, "key": f"/library/metadata/{rk}", "type": "movie", "Media": it["media"]}
            if c:
                md.update({"title": c["title"], "year": c["year"], "guid": f"plex://movie/{c['tmdb']}abc",
                           "Guid": [{"id": f"imdb://{c['imdb']}"}, {"id": f"tmdb://{c['tmdb']}"}],
                           "thumb": f"/library/metadata/{rk}/thumb/1", "art": f"/library/metadata/{rk}/art/1",
                           "summary": f"About {c['title']}.", "originalTitle": c.get("original", ""),
                           "Genre": [{"tag": "Drama"}], "Director": [{"tag": "Somebody"}],
                           "Role": [{"tag": "An Actor", "role": "The Lead"}], "contentRating": "R",
                           "duration": 6000000, "audienceRating": 8.1})
                if it["edition"]:
                    md["editionTitle"] = it["edition"]
            else:
                f = os.path.basename(it["media"][0]["Part"][0]["file"])
                md.update({"title": naming.split_ext(f)[0], "guid": f"local://{rk}"})
            self.items[rk] = md

    def serve(self, port=0):
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, mc, code=200):
                body = json.dumps({"MediaContainer": mc}).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _route(self):
                if self.headers.get("X-Plex-Token") != "test-token":
                    self.send_response(401)
                    self.end_headers()
                    return
                u = urlparse(self.path)
                q = parse_qs(u.query)
                p = u.path
                if p == "/identity":
                    return self._json({"version": "1.41.0.1234-abc"})
                if p == "/library/sections":
                    return self._json({"Directory": [{"key": "1", "type": "movie", "title": fake.section_title,
                                                      "refreshing": False, "Location": [{"path": fake.plex_root}]}]})
                if p == "/library/sections/1/all":
                    md = sorted(fake.items.values(), key=lambda m: m["title"])
                    start = int(q.get("X-Plex-Container-Start", ["0"])[0])
                    size = int(q.get("X-Plex-Container-Size", ["50"])[0])
                    return self._json({"totalSize": len(md), "Metadata": md[start:start + size]})
                if p == "/library/sections/1/refresh":
                    fake.refreshes.append(q.get("path", [""])[0])
                    fake.scan()
                    return self._json({})
                m = re.match(r"^/library/metadata/(\d+)(/(thumb|art)/\d+|/refresh)?$", p)
                if m and m.group(1) in fake.items:
                    if m.group(3):
                        self.send_response(200)
                        self.send_header("Content-Type", "image/png")
                        self.end_headers()
                        self.wfile.write(IMG)
                        return
                    if m.group(2) == "/refresh":
                        return self._json({})
                    return self._json({"Metadata": [fake.items[m.group(1)]]})
                self._json({}, 404)

            do_GET = do_PUT = _route

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"


if __name__ == "__main__":
    fp = FakePlex(sys.argv[1])
    url = fp.serve(int(sys.argv[2]) if len(sys.argv) > 2 else 32499)
    print(f"Fake Plex at {url}, token test-token, {len(fp.items)} movies")
    threading.Event().wait()
