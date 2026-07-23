from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from gdrive_dedupe.core import actions, auth, scanner, strategies
from gdrive_dedupe.core.drive import DriveClient
from gdrive_dedupe.core.models import ScanResult, human_bytes

from .state import ScanState

STATIC_DIR = Path(__file__).parent / "static"


class ConnectBody(BaseModel):
    write_access: bool = False


class CleanBody(BaseModel):
    file_ids: list[str]


class TrashBody(BaseModel):
    file_ids: list[str]


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


def serialize_result(result: ScanResult, strategy: str) -> dict:
    groups = []
    for group in result.groups:
        keeper = strategies.select_keeper(group, strategy)
        groups.append(
            {
                "md5": group.md5,
                "size": group.size,
                "size_human": human_bytes(group.size),
                "wasted_bytes": group.wasted_bytes,
                "wasted_human": human_bytes(group.wasted_bytes),
                "files": [
                    {**_file_dict(f), "suggested_trash": f.id != keeper.id} for f in group.files
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


def _drive_client(write_access: bool) -> DriveClient:
    creds = auth.load_credentials(write_access=write_access)
    if creds is None:
        raise HTTPException(
            status_code=403,
            detail="write access not granted" if write_access else "not connected",
        )
    return DriveClient(auth.build_service(creds))


def create_app() -> FastAPI:
    app = FastAPI(title="Google Drive Duplicate Finder")
    app.state.scan = ScanState()

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

    @app.get("/api/scan/result")
    def scan_result(strategy: str = "newest"):
        state: ScanState = app.state.scan
        if state.result is None:
            raise HTTPException(status_code=404, detail="no scan result yet")
        if strategy not in strategies.STRATEGIES:
            raise HTTPException(status_code=400, detail=f"unknown strategy {strategy!r}")
        return serialize_result(state.result, strategy)

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
        batch_id = actions.trash_files(client, files)
        return {"batch_id": batch_id, "trashed": len(files)}

    @app.get("/api/empty-folders")
    def empty_folders():
        state: ScanState = app.state.scan
        if state.result is None:
            raise HTTPException(status_code=404, detail="no scan result yet")
        found = scanner.find_empty_folders(state.result.files, state.result.folders)
        return {"folders": [_file_dict(f) for f in found], "count": len(found)}

    @app.get("/api/old-files")
    def old_files(before: str):
        state: ScanState = app.state.scan
        if state.result is None:
            raise HTTPException(status_code=404, detail="no scan result yet")
        try:
            datetime.fromisoformat(before)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail="before must be a date like 2020-01-01"
            ) from exc
        found = scanner.find_old_files(state.result.files, before)
        total = sum(f.size for f in found)
        return {
            "files": [_file_dict(f) for f in found],
            "count": len(found),
            "total_bytes": total,
            "total_human": human_bytes(total),
        }

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

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
