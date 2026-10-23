"""All movies loose in one folder, named without years (a common way to keep a library)."""
import os

import pytest

from doctor import config, engine, store
from tests.test_end_to_end import env, tree  # noqa: F401  (env is a fixture)

FLAT = {
    "Coneheads.mp4": 3000,
    "Coneheads.en.srt": 10,
    "Nacho Libre.mp4": 3000,
    "Jackass_ Best and Last.mp4": 3000,
    "9.mp4": 3000,
    "Heat (1995).mkv": 3000,
    "Heat (1995)-poster.jpg": 10,
    "Heat (1995)-fanart.jpg": 10,
    "RARBG.txt": 10,
    "Unknown Thing.mp4": 3000,
}


@pytest.fixture
def flat(env):
    lib, trash, fake = env
    for f in os.listdir(lib):  # swap the messy folder library for a flat one
        import shutil
        p = os.path.join(lib, f)
        shutil.rmtree(p) if os.path.isdir(p) else os.remove(p)
    for f, size in FLAT.items():
        with open(os.path.join(lib, f), "wb") as fh:
            fh.write(b"x" * size)
    fake.scan()
    return lib, trash, fake


def issues(kind=None):
    return [i for i in store.get()["issues"] if kind is None or i["kind"] == kind]


def test_flat_is_detected_and_nothing_moves_into_folders(flat):
    lib, _trash, _fake = flat
    before = tree(lib)
    engine.scan()
    assert tree(lib) == before
    assert store.get()["layout_used"] == "flat"
    assert not issues("own_folder")
    renames = {i["changes"][0]["before"]: i["changes"][0]["after"] for i in issues("rename")}
    assert renames == {"Coneheads.mp4": "Coneheads (1993).mp4", "Nacho Libre.mp4": "Nacho Libre (2006).mp4",
                       "Jackass_ Best and Last.mp4": "Jackass - Best and Last (2026).mp4", "9.mp4": "9 (2009).mp4"}
    cone = next(i for i in issues("rename") if "Coneheads" in i["summary"])
    assert "Rename: Coneheads.en.srt → Coneheads (1993).en.srt" in cone["plan"]
    junk = issues("junk")
    assert len(junk) == 1 and junk[0]["items"] == [] and junk[0]["plan"] == ["To trash: RARBG.txt"]
    # artwork is named after each movie in a shared folder; Heat already has its images
    heat = next(i for i in issues("artwork") if "Heat" in i["summary"])
    assert heat["summary"].endswith("no NFO") and heat["plan"][0].startswith("Write Heat (1995).nfo")
    cone_art = next(i for i in issues("artwork") if "Coneheads" in i["summary"])
    assert "Save Plex's poster as Coneheads-poster.jpg" in cone_art["plan"]


def test_one_fix_leaves_the_rest_of_the_list(flat):
    engine.scan()
    n = len(issues())
    cone = next(i for i in issues("rename") if "Coneheads" in i["summary"])
    engine.fix_issue(cone["id"])
    left = issues()
    assert not [i for i in left if "Coneheads" in i["summary"]]  # its artwork waits for the next check
    assert len(left) == n - 2


def test_fix_all_renames_with_one_undo(flat):
    lib, _trash, fake = flat
    engine.scan()
    before = tree(lib)
    res = engine.fix_many("rename")
    assert res["fixed"] == 4 and not res["failed"]
    assert {"Coneheads (1993).mp4", "Coneheads (1993).en.srt", "9 (2009).mp4",
            "Jackass - Best and Last (2026).mp4"} <= tree(lib)
    assert fake.refreshes == ["/data/movies"]  # Plex told once, not once per movie
    hist = store.get()["history"]
    assert len(hist) == 1 and hist[0]["batch"]
    assert not issues("rename")
    engine.undo(hist[0]["id"])
    assert tree(lib) == before


def test_flat_artwork_files(flat):
    lib, _trash, _fake = flat
    engine.scan()
    engine.fix_many("artwork")
    files = tree(lib)
    assert {"Coneheads-poster.png", "Coneheads-fanart.png", "Coneheads.nfo", "Heat (1995).nfo"} <= files
    assert "poster.png" not in files and "movie.nfo" not in files
    engine.scan()
    assert not issues("artwork")


def test_layout_can_be_forced(flat):
    store.get()["settings"]["layout"] = "folders"
    engine.scan()
    assert store.get()["layout_used"] == "folders"
    assert issues("own_folder")
