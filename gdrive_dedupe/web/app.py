from __future__ import annotations

import asyncio
import json
import os
import threading
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.trustedhost import TrustedHostMiddleware

from gdrive_dedupe.core import actions, auth, scanner, strategies
from gdrive_dedupe.core.drive import DriveClient
from gdrive_dedupe.core.models import ScanResult, human_bytes

from .state import ScanState

STATIC_DIR = Path(__file__).parent / "static"


class ConnectBody(BaseModel):
    write_access: bool = False


class CleanBody(BaseModel):
    file_ids: list[str]
    protected: list[str] = []


class TrashBody(BaseModel):
    file_ids: list[str]
    protected: list[str] = []


class PurgeBody(BaseModel):
    batch_id: str
    confirm: str


class CredentialsBody(BaseModel):
    content: str


def _file_dict(f) -> dict:
    return {
        "id": f.id,
        "name": f.name,
        "path": f.path,
        "size": f.size,
        "size_human": human_bytes(f.size),
        "modified_time": f.modified_time,
        "web_view_link": f.web_view_link,
    }


def serialize_result(
    result: ScanResult,
    strategy: str,
    prefer_folder: str | None = None,
    protected: list[str] | None = None,
) -> dict:
    protected = protected or []
    groups = []
    for group in result.groups:
        to_trash = {
            f.id for f in strategies.files_to_trash(group, strategy, prefer_folder, protected)
        }
        groups.append(
            {
                "md5": group.md5,
                "size": group.size,
                "size_human": human_bytes(group.size),
                "wasted_bytes": group.wasted_bytes,
                "wasted_human": human_bytes(group.wasted_bytes),
                "files": [
                    {
                        **_file_dict(f),
                        "suggested_trash": f.id in to_trash,
                        "protected": strategies.is_protected(f, protected),
                    }
                    for f in group.files
                ],
            }
        )
    return {
        "total_files": result.total_files,
        "skipped_no_md5": result.skipped_no_md5,
        "duplicate_groups": len(result.groups),
        "reclaimable_bytes": result.reclaimable_bytes,
        "reclaimable_human": human_bytes(result.reclaimable_bytes),
        "groups": groups,
    }


def _folder_path(result: ScanResult, folder_id: str, what: str = "folder") -> str:
    folder = scanner.resolve_folder(result.folders, folder_id)
    if folder is None:
        raise HTTPException(status_code=400, detail=f"unknown {what} {folder_id!r}")
    return scanner.folder_full_path(folder)


def _folder_paths(result: ScanResult, folder_ids: list[str], what: str) -> list[str]:
    return [_folder_path(result, i, what) for i in folder_ids]


def _refuse_protected(result: ScanResult, items, protected_ids: list[str]) -> None:
    protected = _folder_paths(result, protected_ids, "protected folder")
    blocked = [i.name for i in items if strategies.is_protected(i, protected)]
    if blocked:
        raise HTTPException(
            status_code=400, detail=f"refusing to trash files in protected folders: {blocked}"
        )


def _drive_client(write_access: bool) -> DriveClient:
    creds = auth.load_credentials(write_access=write_access)
    if creds is None:
        raise HTTPException(
            status_code=403,
            detail="write access not granted" if write_access else "not connected",
        )
    return DriveClient(auth.build_service(creds))


LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "[::1]", "::1")


def _allowed_hosts(extra: list[str] | None = None) -> list[str]:
    """Host names the browser may use to reach this server.

    Loopback is always allowed. `GDRIVE_DEDUPE_ALLOWED_HOSTS` (comma separated)
    and `extra` add more, e.g. a LAN address when running in a container.
    """
    env = os.environ.get("GDRIVE_DEDUPE_ALLOWED_HOSTS", "")
    more = [h.strip() for h in env.split(",") if h.strip()] + list(extra or [])
    return list(dict.fromkeys([*LOOPBACK_HOSTS, *more]))


def create_app(allowed_hosts: list[str] | None = None) -> FastAPI:
    app = FastAPI(title="Google Drive Duplicate Finder")
    app.state.scan = ScanState()
    hosts = _allowed_hosts(allowed_hosts)

    # Defense against DNS rebinding: a page on attacker.example that resolves
    # to 127.0.0.1 would arrive with its own Host header. Refuse it.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    # Defense against cross-site requests: browsers attach an Origin header to
    # every cross-origin request (and to all POSTs). Only our own origin may
    # drive this API.
    @app.middleware("http")
    async def refuse_foreign_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin is not None and urlsplit(origin).hostname not in hosts:
            return JSONResponse(status_code=403, content={"detail": "cross-origin request refused"})
        return await call_next(request)

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/auth/status")
    def auth_status():
        return auth.auth_status()

    @app.post("/api/auth/connect")
    def connect(body: ConnectBody):
        state: ScanState = app.state.scan
        if not state.begin("connecting"):
            raise HTTPException(status_code=409, detail="busy")

        def work():
            try:
                auth.run_oauth_flow(write_access=body.write_access)
                state.status = "idle"
            except Exception as exc:  # surfaced via /api/scan/events and status polling
                state.fail(str(exc))

        threading.Thread(target=work, daemon=True).start()
        return {"started": True}

    @app.post("/api/auth/credentials")
    def upload_credentials(body: CredentialsBody):
        try:
            path = auth.save_client_secrets(body.content)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"saved": True, "path": str(path)}

    @app.post("/api/auth/disconnect")
    def disconnect():
        auth.disconnect()
        return {"disconnected": True}

    @app.post("/api/scan")
    def start_scan():
        state: ScanState = app.state.scan
        if not state.begin("scanning"):
            raise HTTPException(status_code=409, detail="scan already running")
        try:
            client = _drive_client(write_access=False)
        except HTTPException:
            state.status = "idle"
            raise

        def work():
            try:
                result = scanner.scan(
                    client, progress_cb=lambda n: state.set_progress(files_fetched=n)
                )
                state.finish(result)
            except Exception as exc:
                state.fail(str(exc))

        threading.Thread(target=work, daemon=True).start()
        return {"started": True}

    @app.get("/api/scan/events")
    async def scan_events(request: Request):
        state: ScanState = app.state.scan

        async def stream():
            while True:
                snap = state.snapshot()
                yield f"data: {json.dumps(snap)}\n\n"
                if snap["status"] in ("done", "error", "idle") or await request.is_disconnected():
                    break
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="text/event-stream")

    def _full_result() -> ScanResult:
        state: ScanState = app.state.scan
        if state.result is None:
            raise HTTPException(status_code=404, detail="no scan result yet")
        return state.result

    def _scoped(folder: str | None) -> tuple[ScanResult, dict | None]:
        """The current scan, optionally narrowed to one folder subtree."""
        result = _full_result()
        if not folder:
            return result, None
        path = _folder_path(result, folder)
        return scanner.scope_result(result, path), {"id": folder, "path": path}

    @app.get("/api/folders")
    def folders():
        result = _full_result()
        items = sorted(
            ({"id": f.id, "path": scanner.folder_full_path(f)} for f in result.folders),
            key=lambda x: x["path"].lower(),
        )
        return {"folders": items}

    @app.get("/api/scan/result")
    def scan_result(
        strategy: str = "newest",
        folder: str | None = None,
        prefer: str | None = None,
        protected: list[str] = Query(default=[]),
    ):
        if strategy not in strategies.STRATEGIES:
            raise HTTPException(status_code=400, detail=f"unknown strategy {strategy!r}")
        full = _full_result()
        result, scope = _scoped(folder)
        prefer_path = _folder_path(full, prefer, "prefer folder") if prefer else None
        protected_paths = _folder_paths(full, protected, "protected folder")
        body = serialize_result(result, strategy, prefer_path, protected_paths)
        body["scope"] = scope
        return body

    @app.post("/api/clean")
    def clean(body: CleanBody):
        state: ScanState = app.state.scan
        if state.result is None:
            raise HTTPException(status_code=400, detail="run a scan first")
        client = _drive_client(write_access=True)
        selected = set(body.file_ids)
        known = {f.id: f for g in state.result.groups for f in g.files}
        unknown = selected - known.keys()
        if unknown:
            raise HTTPException(status_code=400, detail=f"unknown file ids: {sorted(unknown)}")
        for group in state.result.groups:
            ids = {f.id for f in group.files}
            if ids and ids <= selected:
                raise HTTPException(
                    status_code=400,
                    detail=f"refusing to trash every copy in group {group.md5}",
                )
        files = [known[i] for i in body.file_ids]
        _refuse_protected(state.result, files, body.protected)
        batch_id = actions.trash_files(client, files)
        return {"batch_id": batch_id, "trashed": len(files)}

    def _file_list(found) -> dict:
        total = sum(f.size for f in found)
        return {
            "files": [_file_dict(f) for f in found],
            "count": len(found),
            "total_bytes": total,
            "total_human": human_bytes(total),
        }

    @app.get("/api/empty-folders")
    def empty_folders(folder: str | None = None):
        result, _ = _scoped(folder)
        found = scanner.find_empty_folders(result.files, result.folders)
        return {"folders": [_file_dict(f) for f in found], "count": len(found)}

    @app.get("/api/old-files")
    def old_files(before: str, folder: str | None = None):
        result, _ = _scoped(folder)
        try:
            datetime.fromisoformat(before)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="before must be a date like 2020-01-01"
            ) from exc
        return _file_list(scanner.find_old_files(result.files, before))

    @app.get("/api/largest-files")
    def largest_files(limit: int = Query(default=50, ge=1, le=1000), folder: str | None = None):
        result, _ = _scoped(folder)
        return _file_list(scanner.find_largest_files(result.files, limit))

    @app.post("/api/trash")
    def trash(body: TrashBody):
        """Trash scanned files or empty folders selected in the extra tools."""
        state: ScanState = app.state.scan
        if state.result is None:
            raise HTTPException(status_code=400, detail="run a scan first")
        client = _drive_client(write_access=True)
        known = {f.id: f for f in state.result.files}
        for folder in scanner.find_empty_folders(state.result.files, state.result.folders):
            known[folder.id] = folder
        unknown = set(body.file_ids) - known.keys()
        if unknown:
            raise HTTPException(status_code=400, detail=f"unknown ids: {sorted(unknown)}")
        items = [known[i] for i in body.file_ids]
        _refuse_protected(state.result, items, body.protected)
        batch_id = actions.trash_files(client, items)
        return {"batch_id": batch_id, "trashed": len(items)}

    @app.post("/api/undo")
    def undo():
        client = _drive_client(write_access=True)
        try:
            restored = actions.undo_last(client)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return {"restored": len(restored)}

    @app.get("/api/batches")
    def batches():
        return {"batches": actions.list_batches()}

    @app.post("/api/purge")
    def purge(body: PurgeBody):
        """Permanently delete one batch this tool trashed. Irreversible."""
        client = _drive_client(write_access=True)
        if body.confirm != body.batch_id:
            raise HTTPException(
                status_code=400, detail="type the batch id to confirm permanent deletion"
            )
        try:
            outcome = actions.purge_batch(client, body.batch_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {
            "batch_id": outcome["batch_id"],
            "deleted": len(outcome["deleted"]),
            "skipped": len(outcome["skipped"]),
        }

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
