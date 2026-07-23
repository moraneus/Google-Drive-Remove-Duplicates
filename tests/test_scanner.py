from gdrive_dedupe.core.drive import FOLDER_MIME
from gdrive_dedupe.core.models import DriveFile
from gdrive_dedupe.core.scanner import (
    find_empty_folders,
    find_old_files,
    group_duplicates,
    scan,
)


def f(id, md5, size=1, parent="", modified=""):
    return DriveFile(id=id, name=id, size=size, md5=md5, modified_time=modified, parent=parent)


def folder(id, parent=""):
    return DriveFile(
        id=id, name=id, size=0, md5=None, modified_time="", mime_type=FOLDER_MIME, parent=parent
    )


def test_groups_by_md5_and_skips_uniques():
    result = group_duplicates([f("a", "x"), f("b", "x"), f("c", "y"), f("d", None)])
    assert result.total_files == 4
    assert result.skipped_no_md5 == 1
    assert len(result.groups) == 1
    assert {x.id for x in result.groups[0].files} == {"a", "b"}


def test_groups_sorted_by_wasted_bytes_desc():
    result = group_duplicates(
        [
            f("a", "small", size=1),
            f("b", "small", size=1),
            f("c", "big", size=100),
            f("d", "big", size=100),
        ]
    )
    assert [g.md5 for g in result.groups] == ["big", "small"]


class FakeClient:
    def list_owned_items(self, progress_cb=None):
        if progress_cb:
            progress_cb(2)
        return [f("a", "x"), f("b", "x")], [folder("d1")]


def test_scan_uses_client_and_reports_progress():
    seen = []
    result = scan(FakeClient(), progress_cb=seen.append)
    assert seen == [2]
    assert len(result.groups) == 1
    assert [x.id for x in result.files] == ["a", "b"]
    assert [x.id for x in result.folders] == ["d1"]


def test_empty_folders_nested_returns_top_level_only():
    folders = [folder("outer"), folder("inner", parent="outer")]
    result = find_empty_folders([], folders)
    assert [x.id for x in result] == ["outer"]


def test_folder_with_file_anywhere_in_subtree_is_not_empty():
    folders = [folder("outer"), folder("inner", parent="outer")]
    files = [f("doc", "m", parent="inner")]
    assert find_empty_folders(files, folders) == []


def test_empty_child_of_nonempty_folder_is_returned():
    folders = [folder("outer"), folder("inner", parent="outer")]
    files = [f("doc", "m", parent="outer")]
    assert [x.id for x in find_empty_folders(files, folders)] == ["inner"]


def test_old_files_cutoff_and_ordering():
    files = [
        f("recent", "a", modified="2024-01-01T00:00:00Z"),
        f("ancient", "b", modified="2018-01-01T00:00:00Z"),
        f("older", "c", modified="2020-06-01T00:00:00Z"),
    ]
    assert [x.id for x in find_old_files(files, "2021-01-01")] == ["ancient", "older"]


def test_old_files_excludes_unparseable_dates():
    files = [f("mystery", "a", modified=""), f("dated", "b", modified="2019-01-01T00:00:00Z")]
    assert [x.id for x in find_old_files(files, "2021-01-01")] == ["dated"]
