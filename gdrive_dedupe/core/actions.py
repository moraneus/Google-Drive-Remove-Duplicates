from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .auth import config_dir
from .models import DriveFile


def audit_path() -> Path:
    return config_dir() / "audit.jsonl"


def _append(record: dict) -> None:
    with audit_path().open("a") as fh:
        fh.write(json.dumps(record) + "\n")


def trash_files(
    client,
    files: list[DriveFile],
    progress_cb: Callable[[int, int], None] | None = None,
) -> str:
    """Move files to Drive's trash, recording the batch in the audit log first.

    The audit record is written before any trash call so a mid-batch crash
    still leaves enough information to recover.
    """
    batch_id = uuid.uuid4().hex[:12]
    _append(
        {
            "batch_id": batch_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "action": "trash",
            "files": [{"id": f.id, "name": f.name} for f in files],
        }
    )
    for done, file in enumerate(files, 1):
        client.set_trashed(file.id, True)
        if progress_cb:
            progress_cb(done, len(files))
    return batch_id


def undo_last(client) -> list[dict]:
    """Untrash the most recent not-yet-undone batch; raises LookupError if none."""
    if not audit_path().exists():
        raise LookupError("nothing to undo")
    records = [json.loads(line) for line in audit_path().read_text().splitlines()]
    undone = {r["batch_id"] for r in records if r["action"] == "undo"}
    candidates = [r for r in records if r["action"] == "trash" and r["batch_id"] not in undone]
    if not candidates:
        raise LookupError("nothing to undo")
    batch = candidates[-1]
    for file in batch["files"]:
        client.set_trashed(file["id"], False)
    _append(
        {
            "batch_id": batch["batch_id"],
            "timestamp": datetime.now(UTC).isoformat(),
            "action": "undo",
            "files": batch["files"],
        }
    )
    return batch["files"]
