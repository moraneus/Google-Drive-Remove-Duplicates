from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DriveFile:
    id: str
    name: str
    size: int
    md5: str | None
    modified_time: str  # RFC3339, e.g. "2024-01-01T00:00:00Z"
    mime_type: str = ""
    web_view_link: str = ""
    path: str = "/"
    parent: str = ""  # id of the first parent folder, "" if unknown/root


@dataclass
class DuplicateGroup:
    md5: str
    files: list[DriveFile]

    @property
    def size(self) -> int:
        return self.files[0].size if self.files else 0

    @property
    def wasted_bytes(self) -> int:
        return self.size * max(len(self.files) - 1, 0)


@dataclass
class ScanResult:
    total_files: int
    skipped_no_md5: int
    groups: list[DuplicateGroup] = field(default_factory=list)
    files: list[DriveFile] = field(default_factory=list)
    folders: list[DriveFile] = field(default_factory=list)

    @property
    def duplicate_file_count(self) -> int:
        return sum(len(g.files) for g in self.groups)

    @property
    def reclaimable_bytes(self) -> int:
        return sum(g.wasted_bytes for g in self.groups)


def human_bytes(n: int) -> str:
    if n == 0:
        return "0 B"
    value = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"
