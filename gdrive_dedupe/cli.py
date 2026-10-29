from __future__ import annotations

import json
import os
import secrets
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

import typer
import uvicorn

from gdrive_dedupe.core import actions, auth, scanner, strategies
from gdrive_dedupe.core.drive import DriveClient
from gdrive_dedupe.core.models import ScanResult, human_bytes

app = typer.Typer(
    help="Find and safely remove duplicate files in Google Drive.", no_args_is_help=True
)


def _connect(write_access: bool = False) -> DriveClient:
    creds = auth.load_credentials(write_access=write_access)
    if creds is None:
        typer.echo("Opening Google consent screen in your browser…")
        creds = auth.run_oauth_flow(write_access=write_access)
    return DriveClient(auth.build_service(creds))


def _print_result(result: ScanResult) -> None:
    for group in result.groups:
        first = group.files[0]
        typer.echo(
            f"\n{first.name} — {len(group.files)} copies, "
            f"{human_bytes(group.size)} each, wastes {human_bytes(group.wasted_bytes)}"
        )
        for file in group.files:
            typer.echo(f"  - {file.name}  ({file.path})  modified {file.modified_time[:10]}")
    plural = "s" if len(result.groups) != 1 else ""
    typer.echo(
        f"\n{result.total_files} files scanned · "
        f"{len(result.groups)} duplicate group{plural} · "
        f"{human_bytes(result.reclaimable_bytes)} reclaimable · "
        f"{result.skipped_no_md5} skipped (no checksum)"
    )


def _run_scan(client: DriveClient, folder: str | None = None) -> ScanResult:
    """Scan Drive, optionally narrowing the result to one folder subtree."""
    result = scanner.scan(
        client, progress_cb=lambda n: typer.echo(f"  fetched {n} files…", err=True)
    )
    if folder is None:
        return result
    path = _folder_path(result, folder, "--folder")
    typer.echo(f"Scoped to {path}", err=True)
    return scanner.scope_result(result, path)


def _folder_path(result: ScanResult, ref: str, option: str) -> str:
    found = scanner.resolve_folder(result.folders, ref)
    if found is None:
        raise typer.BadParameter(f"{option}: no folder {ref!r} in your Drive")
    return scanner.folder_full_path(found)


FOLDER_OPT = typer.Option(
    None, "--folder", help="Only consider this folder subtree (path like /Photos/2023, or id)"
)
APPLY_OPT = typer.Option(False, "--apply", help="Actually move them to trash")


@app.command()
def scan(
    json_out: Path | None = typer.Option(None, "--json", help="Write result as JSON"),
    folder: str | None = FOLDER_OPT,
):
    """Scan Drive and report duplicate files (read-only)."""
    client = _connect(write_access=False)
    result = _run_scan(client, folder)
    _print_result(result)
    if json_out:
        from gdrive_dedupe.web.app import serialize_result

        json_out.write_text(json.dumps(serialize_result(result, "newest"), indent=2))
        typer.echo(f"Wrote {json_out}")


@app.command()
def clean(
    strategy: str = typer.Option(
        "newest", help="Which copy to keep: newest, oldest or shallowest (fewest folders deep)"
    ),
    prefer: str | None = typer.Option(
        None, "--prefer", help="Keep the copy inside this folder when a group has one"
    ),
    protect: list[str] = typer.Option(
        [], "--protect", help="Never trash anything inside this folder (repeatable)"
    ),
    folder: str | None = FOLDER_OPT,
    apply: bool = typer.Option(False, "--apply", help="Actually move files to trash"),
):
    """Trash duplicate copies, keeping one file per group. Dry run unless --apply."""
    if strategy not in strategies.STRATEGIES:
        raise typer.BadParameter(f"strategy must be one of {strategies.STRATEGIES}")
    client = _connect(write_access=apply)
    full = _run_scan(client)
    prefer_path = _folder_path(full, prefer, "--prefer") if prefer else None
    protected = [_folder_path(full, p, "--protect") for p in protect]
    result = scanner.scope_result(full, _folder_path(full, folder, "--folder")) if folder else full
    plan = [
        f
        for g in result.groups
        for f in strategies.files_to_trash(g, strategy, prefer_path, protected)
    ]
    if not plan:
        typer.echo("No duplicates to trash 🎉")
        raise typer.Exit()
    _print_result(result)
    total = sum(f.size for f in plan)
    rules = [f"keeping the {strategy} copy of each group"]
    if prefer_path:
        rules.append(f"preferring copies in {prefer_path}")
    if protected:
        rules.append(f"never touching {', '.join(protected)}")
    typer.echo(
        f"\nPlan: trash {len(plan)} file(s), reclaiming {human_bytes(total)} ({'; '.join(rules)})."
    )
    if not apply:
        typer.echo("Dry run — nothing was changed. Re-run with --apply to move files to trash.")
        raise typer.Exit()
    typer.confirm("Move these files to Drive's trash?", abort=True)
    batch_id = actions.trash_files(
        client,
        plan,
        progress_cb=lambda done, n: typer.echo(f"  trashed {done}/{n}", err=True),
    )
    typer.echo(f"Done — batch {batch_id}. Run 'gdrive-dedupe undo' to revert.")


def _confirm_and_trash(client: DriveClient, items, noun: str) -> None:
    typer.confirm(f"Move these {noun} to Drive's trash?", abort=True)
    batch_id = actions.trash_files(
        client,
        items,
        progress_cb=lambda done, n: typer.echo(f"  trashed {done}/{n}", err=True),
    )
    typer.echo(f"Done — batch {batch_id}. Run 'gdrive-dedupe undo' to revert.")


@app.command("empty-folders")
def empty_folders(folder: str | None = FOLDER_OPT, apply: bool = APPLY_OPT):
    """Find folders with no files anywhere inside. Dry run unless --apply."""
    client = _connect(write_access=apply)
    result = _run_scan(client, folder)
    found = scanner.find_empty_folders(result.files, result.folders)
    if not found:
        typer.echo("No empty folders found 🎉")
        raise typer.Exit()
    for item in found:
        typer.echo(f"  - {item.path.rstrip('/')}/{item.name}")
    plural = "s" if len(found) != 1 else ""
    typer.echo(f"\n{len(found)} empty folder{plural} (contents counted recursively).")
    if not apply:
        typer.echo("Dry run — nothing was changed. Re-run with --apply to move them to trash.")
        raise typer.Exit()
    _confirm_and_trash(client, found, "folders")


@app.command("old-files")
def old_files(
    before: str = typer.Option(..., help="Cutoff date, e.g. 2020-01-01 (UTC)"),
    folder: str | None = FOLDER_OPT,
    apply: bool = APPLY_OPT,
):
    """Find files last modified before a date. Dry run unless --apply."""
    try:
        datetime.fromisoformat(before)
    except ValueError:
        raise typer.BadParameter("--before must be a date like 2020-01-01") from None
    client = _connect(write_access=apply)
    result = _run_scan(client, folder)
    found = scanner.find_old_files(result.files, before)
    if not found:
        typer.echo(f"No files modified before {before}.")
        raise typer.Exit()
    for file in found:
        typer.echo(
            f"  - {file.name}  ({file.path})  modified {file.modified_time[:10]}  "
            f"{human_bytes(file.size)}"
        )
    total = sum(f.size for f in found)
    typer.echo(f"\n{len(found)} file(s) older than {before}, {human_bytes(total)} total.")
    if not apply:
        typer.echo("Dry run — nothing was changed. Re-run with --apply to move them to trash.")
        raise typer.Exit()
    _confirm_and_trash(client, found, "files")


@app.command("largest-files")
def largest_files(
    limit: int = typer.Option(50, "--limit", min=1, max=1000, help="How many files to list"),
    folder: str | None = FOLDER_OPT,
    apply: bool = APPLY_OPT,
):
    """List your biggest files. Dry run unless --apply."""
    client = _connect(write_access=apply)
    result = _run_scan(client, folder)
    found = scanner.find_largest_files(result.files, limit)
    if not found:
        typer.echo("No files with a size found.")
        raise typer.Exit()
    for file in found:
        typer.echo(f"  - {human_bytes(file.size):>10}  {file.name}  ({file.path})")
    total = sum(f.size for f in found)
    typer.echo(f"\nTop {len(found)} file(s), {human_bytes(total)} total.")
    if not apply:
        typer.echo("Dry run — nothing was changed. Re-run with --apply to move them to trash.")
        raise typer.Exit()
    _confirm_and_trash(client, found, "files")


@app.command()
def purge(
    batch: str | None = typer.Option(None, "--batch", help="Batch id to delete permanently"),
    apply: bool = typer.Option(False, "--apply", help="Actually delete (irreversible)"),
):
    """PERMANENTLY delete a batch this tool trashed, freeing its storage.

    Without --batch, lists past batches. Trashed files still count toward your
    Drive quota until the trash is emptied; this empties just one batch. Only
    files that are still in the trash are deleted — anything you restored by
    hand is skipped.
    """
    batches = actions.list_batches()
    if batch is None:
        if not batches:
            typer.echo("No batches in the audit log.")
            raise typer.Exit()
        for b in batches:
            typer.echo(
                f"  {b['batch_id']}  {b['timestamp'][:19]}  {b['count']:>5} file(s)  {b['status']}"
            )
        typer.echo("\nRe-run with --batch <id> to see its files, add --apply to delete them.")
        raise typer.Exit()
    found = next((b for b in batches if b["batch_id"] == batch), None)
    if found is None:
        raise typer.BadParameter(f"no batch {batch!r} in the audit log")
    if found["status"] != "trashed":
        typer.echo(f"Batch {batch} is {found['status']} — nothing to purge.")
        raise typer.Exit(code=1)
    for file in found["files"]:
        typer.echo(f"  - {file['name']}")
    typer.echo(f"\n{found['count']} file(s) in batch {batch} would be deleted permanently.")
    if not apply:
        typer.echo("Dry run — nothing was changed. Re-run with --apply to delete them.")
        raise typer.Exit()
    typed = typer.prompt("This cannot be undone. Type the batch id to confirm")
    if typed.strip() != batch:
        typer.echo("Batch id did not match — aborted.")
        raise typer.Exit(code=1)
    client = _connect(write_access=True)
    outcome = actions.purge_batch(
        client,
        batch,
        progress_cb=lambda done, n: typer.echo(f"  checked {done}/{n}", err=True),
    )
    typer.echo(
        f"Deleted {len(outcome['deleted'])} file(s) permanently; "
        f"skipped {len(outcome['skipped'])} no longer in the trash."
    )


@app.command()
def undo():
    """Restore the last trashed batch from Drive's trash."""
    client = _connect(write_access=True)
    try:
        restored = actions.undo_last(client)
    except LookupError:
        typer.echo("Nothing to undo.")
        raise typer.Exit(code=1) from None
    plural = "s" if len(restored) != 1 else ""
    typer.echo(f"Restored {len(restored)} file{plural} from trash.")


@app.command()
def web(
    port: int = typer.Option(8765, help="Port to listen on"),
    host: str = typer.Option(
        os.environ.get("GDRIVE_DEDUPE_HOST", "127.0.0.1"),
        help="Address to bind. Keep 127.0.0.1 unless running in a container.",
    ),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open the browser"),
):
    """Start the local web interface."""
    from gdrive_dedupe.web.app import create_app

    wildcard = host in ("0.0.0.0", "::")
    loopback = host in ("127.0.0.1", "localhost", "::1")
    url = f"http://{'127.0.0.1' if wildcard else host}:{port}/"
    # Beyond loopback, anyone who can reach the port could trash your Drive.
    # Require a per-launch token, carried in the URL and sent by the page.
    token = os.environ.get("GDRIVE_DEDUPE_TOKEN") or (
        None if loopback else secrets.token_urlsafe(24)
    )
    if token:
        url += f"?token={token}"
        typer.echo("Access token required — use the exact URL below (it carries the token).")
    if not no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    typer.echo(f"Serving on {url} (Ctrl-C to stop)")
    application = create_app(allowed_hosts=None if wildcard else [host], token=token)
    uvicorn.run(application, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    app()
