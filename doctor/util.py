import logging
import threading
import urllib.request

from . import config

log = logging.getLogger("doctor")


def notify(title, message):
    """Best-effort push notification (ntfy or any URL taking a plain-text POST)."""
    if not config.NOTIFY_URL:
        return

    def _send():
        try:
            req = urllib.request.Request(config.NOTIFY_URL, data=message.encode(), method="POST",
                                         headers={"Title": title.encode("ascii", "ignore").decode(), "Tags": "movie_camera"})
            urllib.request.urlopen(req, timeout=10).read()
        except Exception as e:
            log.warning("notify failed: %s", e)

    threading.Thread(target=_send, daemon=True).start()
