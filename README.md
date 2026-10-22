# Movie Doctor for Plex

Finds the messy parts of a Plex movie library and lays out a fix for each one, for you to go
through: folders and files not named `Title (Year)`, movies loose in a shared folder, release
names Plex couldn't match, leftover junk, duplicate copies, and missing posters, fanart and NFOs.

**It's review-only.** Scans never change anything. Each fix shows exactly what it will move,
rename or create, and only happens when you press **Fix**. Every fix is in the history with an
**Undo**, and nothing is ever deleted: duplicates and junk are moved to a trash folder you control.

It's a sibling of [abs-library-doctor](https://github.com/ssrt33/abs-library-doctor) (same look,
same way of working), for movies instead of audiobooks.

## What it checks

| Check | What Fix does |
|---|---|
| Folder or file name doesn't match | Renames to `Title (Year)/Title (Year).ext` using Plex's match. Subtitles and other files named after the movie (`.en.srt`, `-poster.jpg`, `.nfo`) are renamed with it, and extras folders move with the folder |
| Movie not in its own folder | Moves a loose movie (at the top of the library, or in a folder shared with other movies) and its subtitles into a new `Title (Year)` folder |
| Not matched in Plex | Renames from the release name (`Some.Film.2015.720p.WEB` → `Some Film (2015)`), which usually lets Plex match it. Without a year in the name it just tells you |
| Leftover junk files | Moves samples, `Sample/` folders, release-group `.nfo`/`.txt` files, `RARBG.txt`, `WWW.YTS.MX.jpg`, `Thumbs.db`, `.rar` leftovers and the like to the trash |
| Duplicate copies | Lists every copy with resolution, HDR/Dolby Vision, codec, audio and size; picks the best to keep, and you can pick another. The rest go to the trash |
| Missing poster, fanart or NFO | Saves Plex's poster and background as `poster.jpg` and `fanart.jpg` next to the movie and writes a Kodi/Jellyfin-style `movie.nfo` (title, plot, genres, cast, crew, IMDb/TMDB ids). Only adds files; there's a **Save all** button for this one |

**Things it won't rename:** when the name on disk and Plex's match disagree (a folder called
`Dune (1984)` that Plex matched to the 2021 film, or a title that looks nothing alike), it tells
you to check the match in Plex instead. Renaming to a wrong match would make things worse.

**Names:** colons become ` - ` (`Mission - Impossible (1996)`), characters Windows can't use are
removed, and accents are kept. Editions get Plex's tag: `Blade Runner (1982) {edition-Final Cut}`.
Several versions in one folder are named `Title (Year) - 2160p.mkv`, `Title (Year) - 1080p.mkv`;
names that already follow that pattern (`- Remux`, `- cd1`) are left alone. On the page you can
choose to add an id to folder names: `Heat (1995) {tmdb-949}` or `{imdb-tt0113277}`, which helps
Plex match reliably.

**4K + HD:** if you keep both on purpose, turn on **Keep 4K and HD copies** and different
resolutions stop counting as duplicates. Editions are never duplicates of each other.

## Safety

- Before anything moves, every file is checked to still be where the scan saw it, and nothing is
  ever overwritten. If one move in a fix fails, the ones before it are put back.
- After a fix, Plex is asked to rescan just that folder, so the change shows up in seconds.
  Plex normally keeps watch history through a rename, since the movie stays matched. If a copy going to the trash
  is part-watched, the page tells you.
- Undo puts files back, and won't run blind if something was moved since (it asks first).
  Undoing artwork moves the created files to the trash rather than deleting them.
- Movies whose files Plex lists but that aren't there (Plex hasn't caught up yet) are skipped
  until the next scan.

## Install on TrueNAS (Dockge)

### 1. Find your Plex token

Open Plex in a browser, open any movie, click **⋯ → Get Info → View XML**. The address of the
page that opens ends in `X-Plex-Token=…`; that value is your token. Use the server owner's account.

### 2. Make a trash folder

Create a folder **outside** the movie library but on the same pool, e.g. `/mnt/tank/media/movies_trash`
next to `/mnt/tank/media/movies` (moves within a pool are instant; across pools a big movie has to be
copied), and a config folder such as `/mnt/tank/apps/plex-movie-doctor`. Give the `apps` user (568)
read/write on both, and on the movie library itself (it needs write access there to rename and move).

### 3. Find the path Plex uses

In Plex go to **Settings → Manage → Libraries**, edit your movie library, and look at **Add folders**.
That's the path inside the Plex container, often `/movies` or `/data/movies`.

### 4. Add the stack

In Dockge, **+ Compose**, name it `plex-movie-doctor`, and paste
[`docker-compose.example.yaml`](docker-compose.example.yaml). Then change:

- `PLEX_URL`: how this container reaches Plex, e.g. `http://192.168.1.10:32400`.
- `PLEX_TOKEN`: the token from step 1.
- The movies volume: mount the **same** host folder Plex uses.
- `PATH_MAP`: `<path Plex sees>=<path this container sees>`. If Plex sees `/data/movies` and you
  mount the folder here at `/movies`, use `/data/movies=/movies`.

Deploy, then open `http://<nas-ip>:8688`. The first scan starts on its own; then go through the
list at your own pace. If the page says it can't see your movie files, `PATH_MAP` or the volume
is off.

**Tip:** in Plex, leave **Settings → Library → Empty trash automatically after every scan** on, so
copies moved to the trash disappear from Plex straight away instead of showing as unavailable.

## Settings

| Variable | Default | |
|---|---|---|
| `PLEX_URL` | `http://plex:32400` | Plex address |
| `PLEX_TOKEN` | | Plex token (required) |
| `LIBRARIES` | all movie libraries | Names or section ids, comma separated |
| `PATH_MAP` | `/movies=/movies` | `plex_path=local_path`, comma separated for several |
| `TRASH_DIR` | `/trash` | Where duplicates and junk are moved |
| `SCAN_EVERY_HOURS` | `12` | Starting value (change it on the page) |
| `NAMING_ID` | `none` | `none`, `tmdb` or `imdb`; starting value (change it on the page) |
| `KEEP_VERSIONS` | `false` | Starting value (change it on the page) |
| `NOTIFY_URL` | | ntfy topic (or any plain-text POST URL) told when a scan finds new things |
| `PUID` / `PGID` | `568` | Owner for moved and created files |
| `PORT` | `8688` | |

## Development

```sh
pip install -r requirements.txt pytest
python -m pytest -q        # naming rules, plus scans and fixes against a fake Plex and a messy test library
```

`tests/fake_plex.py` is a small stand-in for a Plex server. To try the page without a real Plex:

```sh
python -m tests.fake_plex /some/test/movies 32499 &
PLEX_URL=http://127.0.0.1:32499 PLEX_TOKEN=test-token PATH_MAP=/some/test/movies=/some/test/movies \
  CONFIG_DIR=./config TRASH_DIR=./trash python -m doctor
```

## License

MIT
