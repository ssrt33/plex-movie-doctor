"""Small client for the parts of the Plex API the doctor uses."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

from . import config


class PlexError(Exception):
    pass


def _explain(e, method, path, url):
    body = e.read().decode("utf-8", "replace")
    if e.code == 401:
        return ("Plex didn't accept the token (401). Check PLEX_TOKEN is the whole token from an "
                "account that owns the server (see the README for how to find it).")
    if e.code == 403:
        return f"Plex refused {method} {path} (403). Use the server owner's token."
    return f"{method} {path} failed: {e.code} {body[:200]}"


class Plex:
    def __init__(self, url=None, token=None):
        self.url = (url or config.PLEX_URL).rstrip("/")
        self.token = token if token is not None else config.PLEX_TOKEN

    def _open(self, method, path, timeout=60, accept="application/json"):
        r = urllib.request.Request(self.url + path, method=method)
        r.add_header("X-Plex-Token", self.token)
        r.add_header("X-Plex-Client-Identifier", "plex-movie-doctor")
        r.add_header("X-Plex-Product", "Plex Movie Doctor")
        r.add_header("Accept", accept)
        try:
            return urllib.request.urlopen(r, timeout=timeout)
        except urllib.error.HTTPError as e:
            raise PlexError(_explain(e, method, path, self.url)) from e
        except urllib.error.URLError as e:
            raise PlexError(f"Can't reach Plex at {self.url}: {e.reason}") from e

    def _get(self, path, timeout=60):
        with self._open("GET", path, timeout) as resp:
            raw = resp.read()
        if not raw.strip():
            return {}
        try:
            return json.loads(raw).get("MediaContainer", {})
        except ValueError as e:
            raise PlexError(f"Plex sent something that isn't JSON for {path}. Is PLEX_URL the Plex "
                            f"server itself (port 32400)?") from e

    # ------------------------------------------------------------ reading

    def identity(self):
        return self._get("/identity", timeout=10)

    def sections(self):
        return self._get("/library/sections").get("Directory", [])

    def movies(self, section_key, page=300):
        out, start = [], 0
        while True:
            q = urllib.parse.urlencode({"includeGuids": 1, "X-Plex-Container-Start": start,
                                        "X-Plex-Container-Size": page})
            mc = self._get(f"/library/sections/{section_key}/all?{q}", timeout=180)
            got = mc.get("Metadata", [])
            out += got
            total = int(mc.get("totalSize", len(out)))
            start += len(got)
            if not got or start >= total:
                return out

    def item(self, rating_key):
        got = self._get(f"/library/metadata/{rating_key}?includeGuids=1")
        md = got.get("Metadata") or []
        return md[0] if md else None

    def image(self, path):
        """Raw bytes of a poster/background path like /library/metadata/12/thumb/1690000000."""
        if not path.startswith("/library/metadata/"):
            raise PlexError("Not a Plex image path")
        with self._open("GET", path, timeout=60, accept="image/*") as resp:
            return resp.read(), resp.headers.get("Content-Type", "")

    # ------------------------------------------------------------ telling Plex about changes

    def refresh_path(self, section_key, plex_path):
        """Ask Plex to rescan just one folder (it picks up moves and renames in seconds)."""
        q = urllib.parse.urlencode({"path": plex_path})
        self._open("GET", f"/library/sections/{section_key}/refresh?{q}", timeout=30).close()

    def refresh_item(self, rating_key):
        self._open("PUT", f"/library/metadata/{rating_key}/refresh", timeout=30).close()

    def busy_sections(self):
        return [s["title"] for s in self.sections() if s.get("refreshing")]

    def wait_idle(self, timeout=300, step=None):
        """Wait (up to timeout seconds) while Plex is scanning a library."""
        end = time.time() + timeout
        while time.time() < end:
            busy = self.busy_sections()
            if not busy:
                return True
            if step:
                step(f"Waiting for Plex to finish scanning {', '.join(busy)}")
            time.sleep(5)
        return False
