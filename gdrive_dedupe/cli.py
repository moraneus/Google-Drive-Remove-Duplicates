from __future__ import annotations

import json
import threading
import webbrowser
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


def _run_scan(client: DriveClient) -> ScanResult:
    return scanner.scan(client, progress_cb=lambda n: typer.echo(f"  fetched {n} files…", err=True))


@app.command()
def scan(json_out: Path | None = typer.Option(None, "--json", help="Write result as JSON")):
    """Scan Drive and report duplicate files (read-only)."""
    client = _connect(write_access=False)
    result = _run_scan(client)
    _print_result(result)
    if json_out:
        from gdrive_dedupe.web.app import serialize_result

        json_out.write_text(json.dumps(serialize_result(result, "newest"), indent=2))
        typer.echo(f"Wrote {json_out}")


@app.command()
def clean(
    strategy: str = typer.Option("newest", help="Which copy to keep: newest or oldest"),
    apply: bool = typer.Option(False, "--apply", help="Actually move files to trash"),
):
    """Trash duplicate copies, keeping one file per group. Dry run unless --apply."""
    if strategy not in strategies.STRATEGIES:
        raise typer.BadParameter(f"strategy must be one of {strategies.STRATEGIES}")
    client = _connect(write_access=apply)
    result = _run_scan(client)
    plan = [f for g in result.groups for f in strategies.files_to_trash(g, strategy)]
    if not plan:
        typer.echo("No duplicates found 🎉")
        raise typer.Exit()
    _print_result(result)
    total = sum(f.size for f in plan)
    typer.echo(
        f"\nPlan: trash {len(plan)} file(s), reclaiming {human_bytes(total)} "
        f"(keeping the {strategy} copy of each group)."
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
    port: int = typer.Option(8765, help="Port on 127.0.0.1"),
    no_browser: bool = typer.Option(False, "--no-browser", help="Don't open the browser"),
):
    """Start the local web interface."""
    from gdrive_dedupe.web.app import create_app

    url = f"http://127.0.0.1:{port}"
    if not no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    typer.echo(f"Serving on {url} (Ctrl-C to stop)")
    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    app()
