from gdrive_dedupe.core.models import DriveFile
from gdrive_dedupe.core.scanner import group_duplicates, scan


def f(id, md5, size=1):
    return DriveFile(id=id, name=id, size=size, md5=md5, modified_time="")


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
    def list_owned_files(self, progress_cb=None):
        if progress_cb:
            progress_cb(2)
        return [f("a", "x"), f("b", "x")]


def test_scan_uses_client_and_reports_progress():
    seen = []
    result = scan(FakeClient(), progress_cb=seen.append)
    assert seen == [2]
    assert len(result.groups) == 1
