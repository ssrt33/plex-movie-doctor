"""Scan a messy test library through the fake Plex, apply every kind of fix, then undo them."""
import os
import shutil
import tempfile

import pytest

from doctor import config, engine, store
from tests.fake_plex import FakePlex

MESSY = {
    "Heat (1995)/Heat (1995).mkv": 3000,
    "The.Matrix.1999.1080p.BluRay.x264-GRP/The.Matrix.1999.1080p.BluRay.x264-GRP.mkv": 3000,
    "The.Matrix.1999.1080p.BluRay.x264-GRP/The.Matrix.1999.1080p.BluRay.x264-GRP.en.srt": 10,
    "The.Matrix.1999.1080p.BluRay.x264-GRP/RARBG.txt": 10,
    "The.Matrix.1999.1080p.BluRay.x264-GRP/grp.nfo": 10,
    "The.Matrix.1999.1080p.BluRay.x264-GRP/WWW.YTS.MX.jpg": 10,
    "The.Matrix.1999.1080p.BluRay.x264-GRP/Sample/the.matrix.sample.mkv": 10,
    "Alien (1979).mp4": 3000,
    "Alien (1979).en.srt": 10,
    "Alien Resurrection (1997).mkv": 3000,
    "Alien Resurrection (1997).en.srt": 10,
    "Inception (2010)/Inception (2010).mkv": 3000,
    "Inception (2010)/poster.jpg": 10,
    "Inception.2010.2160p.UHD/Inception.2010.2160p.UHD.mkv": 5000,
    "Some.Indie.Film.2015.720p.WEB/Some.Indie.Film.2015.720p.WEB.mkv": 3000,
    "Dune (1984)/Dune (1984).mkv": 3000,
    "Mission Impossible 1996/Mission.Impossible.1996.mkv": 3000,
    "Amelie (2001)/Amelie.2001.mkv": 3000,
    "Blade Runner (1982)/Blade Runner (1982).mkv": 3000,
    "Blade Runner (1982)/folder.jpg": 10,
}


def build(root):
    for rel, size in MESSY.items():
        p = os.path.join(root, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(b"x" * size)


def tree(root):
    out = set()
    for d, _dirs, files in os.walk(root):
        for f in files:
            out.add(os.path.relpath(os.path.join(d, f), root))
    return out


@pytest.fixture
def env(monkeypatch):
    tmp = tempfile.mkdtemp()
    lib, trash, cfg = (os.path.join(tmp, x) for x in ("movies", "trash", "config"))
    for d in (lib, trash, cfg):
        os.makedirs(d)
    build(lib)
    fake = FakePlex(lib, plex_root="/data/movies", overrides={"Dune (1984)": "Dune"})
    url = fake.serve()
    monkeypatch.setattr(config, "PLEX_URL", url)
    monkeypatch.setattr(config, "PLEX_TOKEN", "test-token")
    monkeypatch.setattr(config, "PATH_MAP", f"/data/movies={lib}")
    monkeypatch.setattr(config, "TRASH_DIR", trash)
    monkeypatch.setattr(config, "CONFIG_DIR", cfg)
    monkeypatch.setattr(config, "PUID", os.getuid())
    monkeypatch.setattr(config, "PGID", os.getgid())
    monkeypatch.setattr(store, "_state", None)
    monkeypatch.setattr(engine, "wake", lambda later=0: None)
    yield lib, trash, fake
    fake.httpd.shutdown()
    shutil.rmtree(tmp)


def issues(kind=None):
    return [i for i in store.get()["issues"] if kind is None or i["kind"] == kind]


def one(kind, text):
    got = [i for i in issues(kind) if text in i["summary"]]
    assert len(got) == 1, [i["summary"] for i in issues(kind)]
    return got[0]


def test_scan_finds_everything_and_changes_nothing(env):
    lib, _trash, _fake = env
    before = tree(lib)
    assert "error" not in engine.scan()
    assert tree(lib) == before, "a scan must never change files"
    kinds = {i["kind"] for i in issues()}
    assert {"rename", "own_folder", "junk", "duplicate", "unmatched", "wrong_match", "artwork"} <= kinds
    # Heat is named right: only artwork
    assert not [i for i in issues() if "Heat" in i["summary"] and i["kind"] != "artwork"]
    # Dune: name says 1984, Plex says 2021 -> warned, never renamed
    d = one("wrong_match", "Dune")
    assert d["fix"] is None and "1984" in d["note"]
    # Amélie: matched on the original/English title, accents differ -> fine to rename
    a = one("rename", "Amélie")
    assert a["changes"][0]["after"] == "Amélie (2001)"


def test_rename_moves_sidecars_and_undo_restores(env):
    lib, _trash, fake = env
    engine.scan()
    before = tree(lib)
    i = one("rename", "The Matrix")
    assert "Rename: The.Matrix.1999.1080p.BluRay.x264-GRP.en.srt → The Matrix (1999).en.srt" in i["plan"]
    entry = engine.fix_issue(i["id"])
    assert os.path.exists(os.path.join(lib, "The Matrix (1999)", "The Matrix (1999).mkv"))
    assert os.path.exists(os.path.join(lib, "The Matrix (1999)", "The Matrix (1999).en.srt"))
    assert os.path.exists(os.path.join(lib, "The Matrix (1999)", "RARBG.txt"))  # junk is its own fix
    assert fake.refreshes == ["/data/movies"]
    engine.undo(entry["id"])
    assert tree(lib) == before


def test_loose_files_get_own_folders_without_mixing_subtitles(env):
    lib, _trash, _fake = env
    engine.scan()
    a = one("own_folder", "Alien (1979)")
    engine.fix_issue(a["id"])
    assert sorted(os.listdir(os.path.join(lib, "Alien (1979)"))) == ["Alien (1979).en.srt", "Alien (1979).mp4"]
    assert os.path.exists(os.path.join(lib, "Alien Resurrection (1997).en.srt"))


def test_colon_and_tmdb_naming(env):
    _lib, _trash, _fake = env
    store.get()["settings"]["naming_id"] = "tmdb"
    engine.scan()
    m = one("rename", "Mission")
    assert m["changes"][0]["after"] == "Mission - Impossible (1996) {tmdb-954}"
    heat = [i for i in issues("rename") if "Heat" in i["summary"]]
    assert heat and heat[0]["changes"][0]["after"] == "Heat (1995) {tmdb-949}"


def test_junk_goes_to_trash(env):
    lib, trash, _fake = env
    engine.scan()
    j = one("junk", "Matrix")
    names = sorted(os.path.basename(p.split(": ", 1)[1].rstrip("/")) for p in j["plan"])
    assert names == ["RARBG.txt", "Sample", "WWW.YTS.MX.jpg", "grp.nfo"]
    entry = engine.fix_issue(j["id"])
    folder = os.path.join(lib, "The.Matrix.1999.1080p.BluRay.x264-GRP")
    assert sorted(os.listdir(folder)) == ["The.Matrix.1999.1080p.BluRay.x264-GRP.en.srt",
                                          "The.Matrix.1999.1080p.BluRay.x264-GRP.mkv"]
    assert any(f == "RARBG.txt" for _d, _s, fs in os.walk(trash) for f in fs)
    engine.undo(entry["id"])
    assert "RARBG.txt" in os.listdir(folder)


def test_duplicate_keeps_best_and_respects_choice(env):
    lib, trash, _fake = env
    engine.scan()
    d = one("duplicate", "Inception")
    assert d["copies"][0]["rel"].startswith("Inception.2010.2160p")  # 4K picked to keep
    keep_1080 = d["copies"][1]["id"]
    entry = engine.fix_issue(d["id"], keep=keep_1080)
    assert os.path.exists(os.path.join(lib, "Inception (2010)", "Inception (2010).mkv"))
    assert not os.path.exists(os.path.join(lib, "Inception.2010.2160p.UHD"))
    assert any("Inception.2010.2160p.UHD" in d for d, _s, _f in os.walk(trash))
    engine.undo(entry["id"])
    assert os.path.exists(os.path.join(lib, "Inception.2010.2160p.UHD", "Inception.2010.2160p.UHD.mkv"))


def test_keep_versions_setting(env):
    store.get()["settings"]["keep_versions"] = True
    engine.scan()
    assert not issues("duplicate")


def test_unmatched_rename_from_release_name(env):
    lib, _trash, fake = env
    engine.scan()
    u = one("unmatched", "Some.Indie.Film")
    assert u["changes"][0]["after"] == "Some Indie Film (2015)"
    engine.fix_issue(u["id"])
    assert os.path.exists(os.path.join(lib, "Some Indie Film (2015)", "Some Indie Film (2015).mkv"))


def test_artwork_and_nfo_created_then_undone_to_trash(env):
    lib, trash, _fake = env
    engine.scan()
    h = one("artwork", "Heat")
    entry = engine.fix_issue(h["id"])
    folder = os.path.join(lib, "Heat (1995)")
    assert sorted(os.listdir(folder)) == ["Heat (1995).mkv", "fanart.png", "movie.nfo", "poster.png"]
    nfo = open(os.path.join(folder, "movie.nfo")).read()
    assert "<title>Heat</title>" in nfo and 'type="imdb"' in nfo and "<name>An Actor</name>" in nfo
    # Blade Runner already has a poster (folder.jpg): only fanart + nfo
    br = one("artwork", "Blade Runner")
    assert br["summary"].endswith("no fanart, NFO")
    # Inception has copies in two folders: artwork waits until the duplicate is sorted out
    assert not [i for i in issues("artwork") if "Inception" in i["summary"]]
    engine.undo(entry["id"])
    assert sorted(os.listdir(folder)) == ["Heat (1995).mkv"]
    assert any(f == "movie.nfo" for _d, _s, fs in os.walk(trash) for f in fs)


def test_stale_fix_is_refused_and_nothing_moves(env):
    lib, _trash, _fake = env
    engine.scan()
    i = one("rename", "The Matrix")
    os.rename(os.path.join(lib, "The.Matrix.1999.1080p.BluRay.x264-GRP"), os.path.join(lib, "moved away"))
    before = tree(lib)
    from doctor import fixes
    with pytest.raises(fixes.FixError):
        engine.fix_issue(i["id"])
    assert tree(lib) == before


def test_rescan_after_fixes_is_clean_for_those_movies(env):
    lib, _trash, fake = env
    engine.scan()
    for kind, text in (("rename", "The Matrix"), ("junk", "Matrix")):
        engine.scan()
        engine.fix_issue(one(kind, text)["id"])
    fake.scan()
    engine.scan()
    left = [i["kind"] for i in issues() if "Matrix" in i["summary"]]
    assert left == ["artwork"]


def test_wrong_path_map_is_worked_out(env, monkeypatch):
    lib, _trash, _fake = env
    monkeypatch.setattr(config, "PATH_MAP", f"/movies={lib}")  # Plex really sees /data/movies
    before = tree(lib)
    engine.scan()
    st = store.get()
    assert st["stale"] == st["movies"] and not issues()
    assert st["missing_example"].startswith("/data/movies/")
    assert st["suggest_map"]["map"] == f"/data/movies={lib}"
    assert tree(lib) == before
