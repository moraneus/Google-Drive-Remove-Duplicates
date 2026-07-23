from __future__ import annotations

import random
import time
from collections.abc import Callable

from googleapiclient.errors import HttpError

from .models import DriveFile

FOLDER_MIME = "application/vnd.google-apps.folder"
FILE_FIELDS = "id, name, size, md5Checksum, modifiedTime, mimeType, webViewLink, parents"
RETRY_STATUSES = {403, 429, 500, 502, 503, 504}
OWNED_QUERY = "trashed = false and 'me' in owners"


class DriveClient:
    """Thin wrapper over the Drive v3 service: pagination, backoff, trash."""

    def __init__(self, service, max_retries: int = 5, sleep: Callable[[float], None] = time.sleep):
        self.service = service
        self.max_retries = max_retries
        self._sleep = sleep

    def _execute(self, request):
        for attempt in range(self.max_retries):
            try:
                return request.execute()
            except HttpError as err:
                status = err.resp.status
                if status not in RETRY_STATUSES or attempt == self.max_retries - 1:
                    raise
                self._sleep(min(2**attempt + random.random(), 32))

    def list_owned_files(self, progress_cb: Callable[[int], None] | None = None) -> list[DriveFile]:
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
        folders = {f["id"]: f for f in raw if f.get("mimeType") == FOLDER_MIME}
        return [
            DriveFile(
                id=f["id"],
                name=f.get("name", ""),
                size=int(f.get("size", 0)),
                md5=f.get("md5Checksum"),
                modified_time=f.get("modifiedTime", ""),
                mime_type=f.get("mimeType", ""),
                web_view_link=f.get("webViewLink", ""),
                path=self._resolve_path(f, folders),
            )
            for f in raw
            if f.get("mimeType") != FOLDER_MIME
        ]

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
