from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from .models import DriveFile, DuplicateGroup

STRATEGIES: tuple[str, ...] = ("newest", "oldest", "shallowest")

_EPOCH = datetime(1, 1, 1, tzinfo=UTC)


def _parse_time(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return _EPOCH


def _norm(folder_path: str) -> str:
    stripped = folder_path.rstrip("/")
    return stripped if stripped else "/"


def is_under(item: DriveFile, folder_path: str) -> bool:
    """True when `item` lives in `folder_path` or any folder beneath it.

    `item.path` is the path of the folder containing the item, so this works
    for files and folders alike. The root "/" contains everything.
    """
    folder = _norm(folder_path)
    if folder == "/":
        return True
    return item.path == folder or item.path.startswith(folder + "/")


def _depth(file: DriveFile) -> int:
    return 0 if file.path == "/" else file.path.count("/")


def _rank(files: list[DriveFile], strategy: str) -> DriveFile:
    by_time = sorted(files, key=lambda f: _parse_time(f.modified_time))
    if strategy == "newest":
        return by_time[-1]
    if strategy == "oldest":
        return by_time[0]
    # shallowest: fewest path segments, newest among equals
    return min(reversed(by_time), key=_depth)


def select_keeper(
    group: DuplicateGroup,
    strategy: str = "newest",
    prefer_folder: str | None = None,
    protected: Sequence[str] = (),
) -> DriveFile:
    """Pick the copy to keep.

    Candidates are narrowed first to copies inside `prefer_folder` (if any),
    else to copies inside a protected folder (they can't be trashed anyway, so
    keeping one of them lets the rest go), else all copies. `strategy` then
    orders the candidates.
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}; expected one of {STRATEGIES}")
    candidates = group.files
    if prefer_folder is not None:
        inside = [f for f in group.files if is_under(f, prefer_folder)]
        if inside:
            candidates = inside
    if candidates is group.files and protected:
        guarded = [f for f in group.files if is_protected(f, protected)]
        if guarded:
            candidates = guarded
    return _rank(candidates, strategy)


def is_protected(file: DriveFile, protected: Sequence[str]) -> bool:
    return any(is_under(file, folder) for folder in protected)


def files_to_trash(
    group: DuplicateGroup,
    strategy: str = "newest",
    prefer_folder: str | None = None,
    protected: Sequence[str] = (),
) -> list[DriveFile]:
    keeper = select_keeper(group, strategy, prefer_folder, protected)
    return [f for f in group.files if f.id != keeper.id and not is_protected(f, protected)]
