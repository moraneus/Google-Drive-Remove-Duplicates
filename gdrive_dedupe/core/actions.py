from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from googleapiclient.errors import HttpError

from .auth import config_dir
from .models import DriveFile


def audit_path() -> Path:
    return config_dir() / "audit.jsonl"


def _append(record: dict) -> None:
    with audit_path().open("a") as fh:
        fh.write(json.dumps(record) + "\n")


def _records() -> list[dict]:
    if not audit_path().exists():
        return []
    return [json.loads(line) for line in audit_path().read_text().splitlines() if line.strip()]


def _now() -> str:
    return datetime.now(UTC).isoformat()


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
            "timestamp": _now(),
            "action": "trash",
            "files": [{"id": f.id, "name": f.name} for f in files],
        }
    )
    for done, file in enumerate(files, 1):
        client.set_trashed(file.id, True)
        if progress_cb:
            progress_cb(done, len(files))
    return batch_id


def list_batches() -> list[dict]:
    """Every trash batch in the audit log, newest first, with its current status.

    Status is "trashed" (restorable), "undone" (already restored) or "purged"
    (permanently deleted — nothing left to restore).
    """
    records = _records()
    undone = {r["batch_id"] for r in records if r["action"] == "undo"}
    purged = {r["batch_id"] for r in records if r["action"] == "purge"}
    batches = []
    for r in records:
        if r["action"] != "trash":
            continue
        if r["batch_id"] in purged:
            status = "purged"
        elif r["batch_id"] in undone:
            status = "undone"
        else:
            status = "trashed"
        batches.append(
            {
                "batch_id": r["batch_id"],
                "timestamp": r["timestamp"],
                "count": len(r["files"]),
                "files": r["files"],
                "status": status,
            }
        )
    batches.reverse()
    return batches


def undo_last(client) -> list[dict]:
    """Untrash the most recent restorable batch; raises LookupError if none."""
    candidates = [b for b in list_batches() if b["status"] == "trashed"]
    if not candidates:
        raise LookupError("nothing to undo")
    batch = candidates[0]
    for file in batch["files"]:
        client.set_trashed(file["id"], False)
    _append(
        {
            "batch_id": batch["batch_id"],
            "timestamp": _now(),
            "action": "undo",
            "files": batch["files"],
        }
    )
    return batch["files"]


def purge_batch(
    client,
    batch_id: str,
    progress_cb: Callable[[int, int], None] | None = None,
) -> dict:
    """Permanently delete the files of one batch this tool trashed earlier.

    This is the only path to irreversible deletion. Each file is checked
    first: a file that is no longer in the trash (restored by hand) or that
    no longer exists is skipped, never deleted. The audit record is written
    after the fact and lists exactly what was deleted.

    Raises LookupError for an unknown batch and ValueError for a batch that
    was undone or already purged.
    """
    batch = next((b for b in list_batches() if b["batch_id"] == batch_id), None)
    if batch is None:
        raise LookupError(f"unknown batch {batch_id!r}")
    if batch["status"] == "undone":
        raise ValueError(f"batch {batch_id} was undone; its files are not in the trash")
    if batch["status"] == "purged":
        raise ValueError(f"batch {batch_id} was already purged")
    deleted: list[dict] = []
    skipped: list[dict] = []
    total = len(batch["files"])
    for done, file in enumerate(batch["files"], 1):
        try:
            still_trashed = client.is_trashed(file["id"])
        except HttpError as err:
            if err.resp.status != 404:
                raise
            still_trashed = False  # already gone
        if still_trashed:
            client.delete_file(file["id"])
            deleted.append(file)
        else:
            skipped.append(file)
        if progress_cb:
            progress_cb(done, total)
    _append(
        {
            "batch_id": batch_id,
            "timestamp": _now(),
            "action": "purge",
            "files": deleted,
            "skipped": skipped,
        }
    )
    return {"batch_id": batch_id, "deleted": deleted, "skipped": skipped}
