"""Settings, all overridable with environment variables."""
import os

# Your Plex server, as this container can reach it, e.g. http://192.168.1.10:32400
PLEX_URL = os.environ.get("PLEX_URL", "http://plex:32400").strip().strip("'\"").rstrip("/")
if PLEX_URL and "://" not in PLEX_URL:
    PLEX_URL = "http://" + PLEX_URL
# Your Plex token (see the README for how to find it). Spaces and quotes are removed.
PLEX_TOKEN = "".join(os.environ.get("PLEX_TOKEN", "").split()).strip("'\"")
# Only look at these movie libraries (names or ids, comma separated). Empty = every movie library.
LIBRARIES = [x.strip() for x in os.environ.get("LIBRARIES", "").split(",") if x.strip()]

# How Plex's paths map to paths inside this container, as "plex_path=local_path" (comma separated
# for several). If both containers mount the movies at /movies, the default works as is.
PATH_MAP = os.environ.get("PATH_MAP", "/movies=/movies")
# Where duplicates and junk files are moved instead of being deleted. Mount a folder here that is
# OUTSIDE the movie library so Plex doesn't scan it.
TRASH_DIR = os.environ.get("TRASH_DIR", "/trash")
# Where the app keeps its own state (issues, history, settings).
CONFIG_DIR = os.environ.get("CONFIG_DIR", "/config")

PORT = int(os.environ.get("PORT", "8688"))

# Starting values for settings you can also change on the page.
SCAN_EVERY_HOURS = float(os.environ.get("SCAN_EVERY_HOURS", "12"))
# Add an id to folder names: "none", "tmdb" -> "Heat (1995) {tmdb-949}", "imdb" -> "{imdb-tt0113277}"
NAMING_ID = os.environ.get("NAMING_ID", "none").strip().lower()
# How the movies are kept: "folders" (one folder per movie), "flat" (all in one folder, renamed in place)
# or "auto" (flat when most movies sit loose at the top of the library).
LAYOUT = os.environ.get("LAYOUT", "auto").strip().lower()
# Treat a 4K and a 1080p copy of the same movie as versions you want to keep, not duplicates.
KEEP_VERSIONS = os.environ.get("KEEP_VERSIONS", "false").strip().lower() in ("1", "true", "yes", "on")

# Optional push notification when a scan finds new things (plain-text POST, e.g. an ntfy topic URL).
NOTIFY_URL = os.environ.get("NOTIFY_URL", "").strip()

# Ownership for files and folders the app moves or creates (TrueNAS "apps" user is 568).
PUID = int(os.environ.get("PUID", "568"))
PGID = int(os.environ.get("PGID", "568"))

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
