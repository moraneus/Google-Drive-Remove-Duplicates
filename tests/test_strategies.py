import pytest

from gdrive_dedupe.core.models import DriveFile, DuplicateGroup
from gdrive_dedupe.core.strategies import files_to_trash, is_under, select_keeper


def f(id, modified, path="/"):
    return DriveFile(id=id, name=id, size=1, md5="m", modified_time=modified, path=path)


GROUP = DuplicateGroup(
    md5="m",
    files=[
        f("old", "2020-05-01T10:00:00Z"),
        f("new", "2024-05-01T10:00:00Z"),
        f("mid", "2022-05-01T10:00:00Z"),
    ],
)


def test_keep_newest():
    assert select_keeper(GROUP, "newest").id == "new"
    assert {x.id for x in files_to_trash(GROUP, "newest")} == {"old", "mid"}


def test_keep_oldest():
    assert select_keeper(GROUP, "oldest").id == "old"


def test_unknown_strategy_raises():
    with pytest.raises(ValueError):
        select_keeper(GROUP, "bogus")


def test_unparseable_timestamp_sorts_last():
    g = DuplicateGroup(md5="m", files=[f("bad", ""), f("ok", "2021-01-01T00:00:00Z")])
    assert select_keeper(g, "newest").id == "ok"


# ---- is_under ----


@pytest.mark.parametrize(
    ("file_path", "folder", "expected"),
    [
        ("/Photos/2023", "/Photos", True),
        ("/Photos", "/Photos", True),
        ("/Photos", "/Photos/", True),
        ("/Photosx", "/Photos", False),
        ("/Other/Photos", "/Photos", False),
        ("/", "/Photos", False),
        ("/anything", "/", True),
        ("/", "/", True),
    ],
)
def test_is_under(file_path, folder, expected):
    assert is_under(f("x", "", path=file_path), folder) is expected


# ---- shallowest ----


def test_keep_shallowest_prefers_fewest_path_segments():
    g = DuplicateGroup(
        md5="m",
        files=[
            f("deep", "2024-01-01T00:00:00Z", path="/A/B/C"),
            f("top", "2020-01-01T00:00:00Z", path="/A"),
            f("root", "2019-01-01T00:00:00Z", path="/"),
        ],
    )
    assert select_keeper(g, "shallowest").id == "root"


def test_keep_shallowest_breaks_ties_by_newest():
    g = DuplicateGroup(
        md5="m",
        files=[
            f("older", "2020-01-01T00:00:00Z", path="/A"),
            f("newer", "2024-01-01T00:00:00Z", path="/B"),
        ],
    )
    assert select_keeper(g, "shallowest").id == "newer"


# ---- prefer_folder ----


def test_prefer_folder_wins_over_strategy():
    g = DuplicateGroup(
        md5="m",
        files=[
            f("newest-elsewhere", "2024-01-01T00:00:00Z", path="/Downloads"),
            f("in-photos", "2020-01-01T00:00:00Z", path="/Photos/2020"),
        ],
    )
    assert select_keeper(g, "newest", prefer_folder="/Photos").id == "in-photos"


def test_prefer_folder_falls_back_to_strategy_when_no_copy_inside():
    g = DuplicateGroup(
        md5="m",
        files=[
            f("a", "2024-01-01T00:00:00Z", path="/Downloads"),
            f("b", "2020-01-01T00:00:00Z", path="/Backup"),
        ],
    )
    assert select_keeper(g, "newest", prefer_folder="/Photos").id == "a"


def test_prefer_folder_applies_strategy_among_copies_inside():
    g = DuplicateGroup(
        md5="m",
        files=[
            f("p-old", "2020-01-01T00:00:00Z", path="/Photos"),
            f("p-new", "2024-01-01T00:00:00Z", path="/Photos/Sub"),
            f("x", "2025-01-01T00:00:00Z", path="/Downloads"),
        ],
    )
    assert select_keeper(g, "oldest", prefer_folder="/Photos").id == "p-old"
    assert select_keeper(g, "newest", prefer_folder="/Photos").id == "p-new"


# ---- protected folders ----


def test_protected_copies_are_never_trashed():
    g = DuplicateGroup(
        md5="m",
        files=[
            f("archived", "2020-01-01T00:00:00Z", path="/Archive/2020"),
            f("loose", "2024-01-01T00:00:00Z", path="/Downloads"),
        ],
    )
    trash = files_to_trash(g, "newest", protected=["/Archive"])
    assert [x.id for x in trash] == ["loose"]


def test_protected_copy_becomes_keeper_so_others_can_go():
    g = DuplicateGroup(
        md5="m",
        files=[
            f("archived", "2020-01-01T00:00:00Z", path="/Archive"),
            f("a", "2024-01-01T00:00:00Z", path="/Downloads"),
            f("b", "2023-01-01T00:00:00Z", path="/Desktop"),
        ],
    )
    assert select_keeper(g, "newest", protected=["/Archive"]).id == "archived"
    assert {x.id for x in files_to_trash(g, "newest", protected=["/Archive"])} == {"a", "b"}


def test_all_copies_protected_trashes_nothing():
    g = DuplicateGroup(
        md5="m",
        files=[
            f("a", "2020-01-01T00:00:00Z", path="/Archive/x"),
            f("b", "2024-01-01T00:00:00Z", path="/Archive/y"),
        ],
    )
    assert files_to_trash(g, "newest", protected=["/Archive"]) == []


def test_prefer_folder_beats_protected_for_keeper_choice():
    g = DuplicateGroup(
        md5="m",
        files=[
            f("archived", "2020-01-01T00:00:00Z", path="/Archive"),
            f("photo", "2021-01-01T00:00:00Z", path="/Photos"),
            f("loose", "2024-01-01T00:00:00Z", path="/Downloads"),
        ],
    )
    keeper = select_keeper(g, "newest", prefer_folder="/Photos", protected=["/Archive"])
    assert keeper.id == "photo"
    trash = files_to_trash(g, "newest", prefer_folder="/Photos", protected=["/Archive"])
    assert [x.id for x in trash] == ["loose"]
