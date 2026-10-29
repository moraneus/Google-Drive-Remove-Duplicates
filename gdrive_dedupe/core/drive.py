from __future__ import annotations

import http.client
import json
import random
import ssl
import time
from collections.abc import Callable

import httplib2
from googleapiclient.errors import HttpError

from .models import DriveFile

FOLDER_MIME = "application/vnd.google-apps.folder"
FILE_FIELDS = "id, name, size, md5Checksum, modifiedTime, mimeType, webViewLink, parents"
RETRY_STATUSES = {429, 500, 502, 503, 504}
# A 403 is only transient when Drive is rate-limiting us; other 403s
# (insufficientFilePermissions, appNotAuthorizedToFile, ...) never clear on retry.
RETRYABLE_403_REASONS = {"userRateLimitExceeded", "rateLimitExceeded", "sharingRateLimitExceeded"}
OWNED_QUERY = "trashed = false and 'me' in owners"

# Transient network failures that aren't HttpErrors but are safe to retry:
# read/connect timeouts, dropped connections, partial TLS/HTTP reads, and DNS
# blips. ConnectionError covers ConnectionReset/Aborted/Refused. httplib2 (the
# transport under the Drive client) turns socket.gaierror into
# ServerNotFoundError, so that is what a DNS failure looks like at this layer.
RETRYABLE_NETWORK_ERRORS = (
    TimeoutError,
    ConnectionError,
    httplib2.ServerNotFoundError,
    ssl.SSLError,
    http.client.HTTPException,
)


def _is_retryable(err: HttpError) -> bool:
    status = err.resp.status
    if status in RETRY_STATUSES:
        return True
    if status != 403:
        return False
    try:
        errors = json.loads(err.content.decode("utf-8"))["error"]["errors"]
        reasons = {e.get("reason") for e in errors}
    except (ValueError, KeyError, TypeError, AttributeError):
        return False
    return bool(reasons & RETRYABLE_403_REASONS)


class DriveClient:
    """Thin wrapper over the Drive v3 service: pagination, backoff, trash."""

    def __init__(self, service, max_retries: int = 5, sleep: Callable[[float], None] = time.sleep):
        self.service = service
        self.max_retries = max_retries
        self._sleep = sleep

    def _backoff(self, attempt: int) -> None:
        self._sleep(min(2**attempt + random.random(), 32))

    def _execute(self, request):
        for attempt in range(self.max_retries):
            try:
                return request.execute()
            except HttpError as err:
                if not _is_retryable(err) or attempt == self.max_retries - 1:
                    raise
                self._backoff(attempt)
            except RETRYABLE_NETWORK_ERRORS:
                if attempt == self.max_retries - 1:
                    raise
                self._backoff(attempt)

    def list_owned_items(
        self, progress_cb: Callable[[int], None] | None = None
    ) -> tuple[list[DriveFile], list[DriveFile]]:
        """Return (files, folders) you own, with folder paths resolved."""
        raw: list[dict] = []
        page_token = None
        while True:
            response = self._execute(
                self.service.files().list(
                    q=OWNED_QUERY,
                    pageSize=1000,
                    fields=f"nextPageToken, files({FILE_FIELDS})",
                    pageToken=page_token,
                )
            )
            raw.extend(response.get("files", []))
            if progress_cb:
                progress_cb(len(raw))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        folder_map = {f["id"]: f for f in raw if f.get("mimeType") == FOLDER_MIME}

        def to_drive_file(item: dict) -> DriveFile:
            return DriveFile(
                id=item["id"],
                name=item.get("name", ""),
                size=int(item.get("size", 0)),
                md5=item.get("md5Checksum"),
                modified_time=item.get("modifiedTime", ""),
                mime_type=item.get("mimeType", ""),
                web_view_link=item.get("webViewLink", ""),
                path=self._resolve_path(item, folder_map),
                parent=(item.get("parents") or [""])[0],
            )

        files = [to_drive_file(f) for f in raw if f.get("mimeType") != FOLDER_MIME]
        folders = [to_drive_file(f) for f in raw if f.get("mimeType") == FOLDER_MIME]
        return files, folders

    def list_owned_files(self, progress_cb: Callable[[int], None] | None = None) -> list[DriveFile]:
        return self.list_owned_items(progress_cb)[0]

    @staticmethod
    def _resolve_path(item: dict, folders: dict[str, dict]) -> str:
        parts: list[str] = []
        parent = (item.get("parents") or [None])[0]
        depth = 0
        while parent and parent in folders and depth < 50:
            folder = folders[parent]
            parts.append(folder.get("name", ""))
            parent = (folder.get("parents") or [None])[0]
            depth += 1
        return "/" + "/".join(reversed(parts)) if parts else "/"

    def set_trashed(self, file_id: str, trashed: bool) -> None:
        self._execute(self.service.files().update(fileId=file_id, body={"trashed": trashed}))

    def is_trashed(self, file_id: str) -> bool:
        meta = self._execute(self.service.files().get(fileId=file_id, fields="trashed"))
        return bool(meta.get("trashed"))

    def delete_file(self, file_id: str) -> None:
        """Permanently delete a file. Only ever called by `actions.purge_batch`."""
        self._execute(self.service.files().delete(fileId=file_id))
