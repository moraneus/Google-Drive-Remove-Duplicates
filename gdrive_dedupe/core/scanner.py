from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable

from .models import DriveFile, DuplicateGroup, ScanResult


def group_duplicates(files: Iterable[DriveFile]) -> ScanResult:
    by_md5: dict[str, list[DriveFile]] = defaultdict(list)
    total = 0
    skipped = 0
    for file in files:
        total += 1
        if file.md5:
            by_md5[file.md5].append(file)
        else:
            skipped += 1
    groups = [
        DuplicateGroup(md5=md5, files=group) for md5, group in by_md5.items() if len(group) >= 2
    ]
    groups.sort(key=lambda g: g.wasted_bytes, reverse=True)
    return ScanResult(total_files=total, skipped_no_md5=skipped, groups=groups)


def scan(client, progress_cb: Callable[[int], None] | None = None) -> ScanResult:
    return group_duplicates(client.list_owned_files(progress_cb))
