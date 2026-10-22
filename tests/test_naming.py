import pytest

from doctor import naming


@pytest.mark.parametrize("name,want", [
    ("The.Matrix.1999.1080p.BluRay.x264-GRP.mkv", ("The Matrix", 1999)),
    ("Blade Runner 2049 (2017) 2160p", ("Blade Runner 2049", 2017)),
    ("Blade.Runner.2049.2017.1080p.WEB-DL", ("Blade Runner 2049", 2017)),
    ("2001.A.Space.Odyssey.1968.REMASTERED.720p", ("2001 A Space Odyssey", 1968)),
    ("[YTS.MX] Parasite (2019) [1080p]", ("Parasite", 2019)),
    ("Heat (1995) {tmdb-949}", ("Heat", 1995)),
    ("some_indie_film_2015_720p", ("Some Indie Film", 2015)),
    ("Movie With No Year", None),
])
def test_parse_release(name, want):
    assert naming.parse_release(name) == want


def test_names():
    assert naming.safe_name("Mission: Impossible") == "Mission - Impossible"
    assert naming.safe_name("AC/DC: Live?") == "AC DC - Live"
    assert naming.folder_name("Heat", 1995, ids={"tmdb": "949"}, id_style="tmdb") == "Heat (1995) {tmdb-949}"
    assert naming.folder_name("Blade Runner", 1982, edition="Final Cut") == "Blade Runner (1982) {edition-Final Cut}"
    assert naming.folder_ok("Heat (1995) {imdb-tt0113277}", "Heat (1995)", "none")
    assert not naming.folder_ok("Heat (1995)", "Heat (1995) {tmdb-949}", "tmdb")


def test_fold_and_match():
    assert naming.fold("Matrix, The") == naming.fold("The Matrix")
    assert naming.name_matches_plex("Amelie (2001)", ["Amélie", ""], 2001)[0]
    assert not naming.name_matches_plex("Dune (1984)", ["Dune", ""], 2021)[0]
    assert not naming.name_matches_plex("Totally Different (1999)", ["The Matrix", ""], 1999)[0]


def test_sidecars_dont_cross_movies():
    files = ["Alien.mkv", "Alien.en.srt", "Alien.Resurrection.mkv", "Alien.Resurrection.en.srt", "Alien-poster.jpg"]
    got = naming.sidecars(files, "Alien", exclude={"Alien.mkv", "Alien.Resurrection.mkv"},
                          other_stems=["Alien", "Alien.Resurrection"])
    assert got == ["Alien.en.srt", "Alien-poster.jpg"]


def test_junk(tmp_path):
    for f in ["Movie (2000).mkv", "Movie (2000).en.srt", "RARBG.txt", "release.nfo", "movie.nfo", "poster.jpg",
              "WWW.YTS.MX.jpg", "movie-sample.mkv", "Thumbs.db", "._Movie (2000).mkv"]:
        (tmp_path / f).write_bytes(b"x")
    (tmp_path / "movie.nfo").write_text("<?xml version='1.0'?><movie/>")
    (tmp_path / "Sample").mkdir()
    (tmp_path / "Featurettes").mkdir()
    got = sorted(p.rsplit("/", 1)[1] for p in naming.junk_in(str(tmp_path), {str(tmp_path / "Movie (2000).mkv")}))
    assert got == ["._Movie (2000).mkv", "RARBG.txt", "Sample", "Thumbs.db", "WWW.YTS.MX.jpg", "movie-sample.mkv",
                   "release.nfo"]
