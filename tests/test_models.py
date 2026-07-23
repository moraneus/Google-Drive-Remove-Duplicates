from gdrive_dedupe.core.models import DriveFile, DuplicateGroup, ScanResult, human_bytes


def make_file(id="f1", size=100, md5="abc", modified="2024-01-01T00:00:00Z"):
    return DriveFile(id=id, name=f"{id}.txt", size=size, md5=md5, modified_time=modified)


def test_group_wasted_bytes_counts_all_but_one_copy():
    g = DuplicateGroup(md5="abc", files=[make_file("a"), make_file("b"), make_file("c")])
    assert g.size == 100
    assert g.wasted_bytes == 200


def test_scan_result_aggregates():
    g1 = DuplicateGroup(md5="a", files=[make_file("a", size=10), make_file("b", size=10)])
    g2 = DuplicateGroup(
        md5="b", files=[make_file("c", size=7), make_file("d", size=7), make_file("e", size=7)]
    )
    r = ScanResult(total_files=50, skipped_no_md5=3, groups=[g1, g2])
    assert r.duplicate_file_count == 5
    assert r.reclaimable_bytes == 10 + 14


def test_human_bytes():
    assert human_bytes(0) == "0 B"
    assert human_bytes(1536) == "1.5 KB"
    assert human_bytes(3 * 1024**3) == "3.0 GB"
