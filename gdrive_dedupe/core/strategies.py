from __future__ import annotations

from datetime import UTC, datetime

from .models import DriveFile, DuplicateGroup

STRATEGIES: tuple[str, ...] = ("newest", "oldest")

_EPOCH = datetime(1, 1, 1, tzinfo=UTC)


def _parse_time(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return _EPOCH


def select_keeper(group: DuplicateGroup, strategy: str = "newest") -> DriveFile:
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}; expected one of {STRATEGIES}")
    ordered = sorted(group.files, key=lambda f: _parse_time(f.modified_time))
    return ordered[-1] if strategy == "newest" else ordered[0]


def files_to_trash(group: DuplicateGroup, strategy: str = "newest") -> list[DriveFile]:
    keeper = select_keeper(group, strategy)
    return [f for f in group.files if f.id != keeper.id]
