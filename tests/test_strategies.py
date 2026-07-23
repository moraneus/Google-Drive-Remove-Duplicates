import pytest

from gdrive_dedupe.core.models import DriveFile, DuplicateGroup
from gdrive_dedupe.core.strategies import files_to_trash, select_keeper


def f(id, modified):
    return DriveFile(id=id, name=id, size=1, md5="m", modified_time=modified)


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
