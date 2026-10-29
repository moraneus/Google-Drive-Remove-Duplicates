from gdrive_dedupe.core.drive import FOLDER_MIME
from gdrive_dedupe.core.models import DriveFile
from gdrive_dedupe.core.scanner import (
    find_empty_folders,
    find_largest_files,
    find_old_files,
    folder_full_path,
    group_duplicates,
    resolve_folder,
    scan,
    scope_result,
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


# ---- folder scoping ----


def pf(id, md5, path, size=1):
    return DriveFile(id=id, name=id, size=size, md5=md5, modified_time="", path=path)


def pfolder(id, path, name=None):
    return DriveFile(
        id=id, name=name or id, size=0, md5=None, modified_time="", mime_type=FOLDER_MIME, path=path
    )


SCOPED_FILES = [
    pf("in1", "x", "/Photos"),
    pf("in2", "x", "/Photos/2023"),
    pf("out", "x", "/Downloads"),
    pf("lonely-in", "y", "/Photos"),
    pf("pair-out-a", "z", "/Downloads"),
    pf("pair-out-b", "z", "/Desktop"),
]
SCOPED_FOLDERS = [
    pfolder("photos", "/", name="Photos"),
    pfolder("y2023", "/Photos", name="2023"),
    pfolder("downloads", "/", name="Downloads"),
]


def test_folder_full_path_joins_parent_path_and_name():
    assert folder_full_path(pfolder("photos", "/", name="Photos")) == "/Photos"
    assert folder_full_path(pfolder("y2023", "/Photos", name="2023")) == "/Photos/2023"


def test_scope_result_keeps_only_subtree_and_regroups():
    full = group_duplicates(SCOPED_FILES)
    full.files = SCOPED_FILES
    full.folders = SCOPED_FOLDERS
    scoped = scope_result(full, "/Photos")
    assert [x.id for x in scoped.files] == ["in1", "in2", "lonely-in"]
    assert [x.id for x in scoped.folders] == ["y2023"]
    assert scoped.total_files == 3
    assert [g.md5 for g in scoped.groups] == ["x"]
    assert {x.id for x in scoped.groups[0].files} == {"in1", "in2"}


def test_scope_result_root_is_identity():
    full = group_duplicates(SCOPED_FILES)
    full.files = SCOPED_FILES
    full.folders = SCOPED_FOLDERS
    scoped = scope_result(full, "/")
    assert len(scoped.files) == len(SCOPED_FILES)
    assert len(scoped.groups) == 2


def test_resolve_folder_by_path_or_id():
    assert resolve_folder(SCOPED_FOLDERS, "/Photos/2023").id == "y2023"
    assert resolve_folder(SCOPED_FOLDERS, "Photos/2023/").id == "y2023"
    assert resolve_folder(SCOPED_FOLDERS, "y2023").id == "y2023"
    assert resolve_folder(SCOPED_FOLDERS, "/Nope") is None


# ---- largest files ----


def test_largest_files_sorted_desc_and_limited():
    files = [
        pf("small", "a", "/", size=1),
        pf("huge", "b", "/", size=1000),
        pf("big", "c", "/", size=500),
        pf("mid", "d", "/", size=100),
    ]
    assert [x.id for x in find_largest_files(files, limit=3)] == ["huge", "big", "mid"]


def test_largest_files_excludes_zero_size():
    files = [pf("doc", None, "/", size=0), pf("bin", "a", "/", size=5)]
    assert [x.id for x in find_largest_files(files, limit=10)] == ["bin"]
