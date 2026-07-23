from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from datetime import UTC, datetime

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
    files, folders = client.list_owned_items(progress_cb)
    result = group_duplicates(files)
    result.files = files
    result.folders = folders
    return result


def find_empty_folders(files: list[DriveFile], folders: list[DriveFile]) -> list[DriveFile]:
    """Top-level folders whose subtree contains no files at all.

    A folder holding only empty folders counts as empty. Only the topmost
    empty folders are returned — trashing them covers their subtree.
    """
    has_file = {f.parent for f in files if f.parent}
    children: dict[str, list[DriveFile]] = defaultdict(list)
    for folder in folders:
        children[folder.parent].append(folder)
    empty: dict[str, bool] = {}

    def is_empty(folder: DriveFile) -> bool:
        if folder.id in empty:
            return empty[folder.id]
        empty[folder.id] = False  # guards against parent cycles in Drive metadata
        result = folder.id not in has_file and all(
            is_empty(child) for child in children.get(folder.id, [])
        )
        empty[folder.id] = result
        return result

    empty_ids = {folder.id for folder in folders if is_empty(folder)}
    top_level = [f for f in folders if f.id in empty_ids and f.parent not in empty_ids]
    return sorted(top_level, key=lambda f: (f.path, f.name))


def _parse_rfc3339(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def find_old_files(files: list[DriveFile], before: str) -> list[DriveFile]:
    """Files last modified strictly before `before` (YYYY-MM-DD), oldest first.

    Files whose modification time cannot be parsed are excluded — age cannot
    be established, so they are never candidates.
    """
    cutoff = datetime.fromisoformat(before)
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=UTC)
    dated = [(parsed, f) for f in files if (parsed := _parse_rfc3339(f.modified_time)) is not None]
    return [f for parsed, f in sorted(dated, key=lambda p: p[0]) if parsed < cutoff]
