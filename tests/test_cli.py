import pytest
from typer.testing import CliRunner

from gdrive_dedupe import cli
from gdrive_dedupe.core.models import DriveFile, DuplicateGroup, ScanResult

runner = CliRunner()


def result_fixture():
    files = [
        DriveFile(
            id="new",
            name="report.pdf",
            size=100,
            md5="m",
            modified_time="2024-06-01T00:00:00Z",
            path="/Docs",
        ),
        DriveFile(
            id="old",
            name="report copy.pdf",
            size=100,
            md5="m",
            modified_time="2020-06-01T00:00:00Z",
            path="/Backup",
        ),
    ]
    return ScanResult(
        total_files=10, skipped_no_md5=1, groups=[DuplicateGroup(md5="m", files=files)]
    )


@pytest.fixture(autouse=True)
def fake_core(monkeypatch, tmp_path):
    monkeypatch.setenv("GDRIVE_DEDUPE_HOME", str(tmp_path))
    monkeypatch.setattr(cli, "_connect", lambda write_access=False: object())
    monkeypatch.setattr(cli.scanner, "scan", lambda client, progress_cb=None: result_fixture())


def test_scan_prints_summary():
    result = runner.invoke(cli.app, ["scan"])
    assert result.exit_code == 0
    assert "1 duplicate group" in result.output
    assert "report.pdf" in result.output


def test_clean_dry_run_by_default(monkeypatch):
    trashed = []
    monkeypatch.setattr(
        cli.actions,
        "trash_files",
        lambda client, files, progress_cb=None: trashed.extend(files) or "b1",
    )
    result = runner.invoke(cli.app, ["clean"])
    assert result.exit_code == 0
    assert "dry run" in result.output.lower()
    assert trashed == []


def test_clean_apply_trashes_after_confirm(monkeypatch):
    trashed = []
    monkeypatch.setattr(
        cli.actions,
        "trash_files",
        lambda client, files, progress_cb=None: trashed.extend(files) or "b1",
    )
    result = runner.invoke(cli.app, ["clean", "--apply"], input="y\n")
    assert result.exit_code == 0
    assert [f.id for f in trashed] == ["old"]


def test_clean_apply_aborts_on_no(monkeypatch):
    trashed = []
    monkeypatch.setattr(
        cli.actions,
        "trash_files",
        lambda client, files, progress_cb=None: trashed.extend(files) or "b1",
    )
    runner.invoke(cli.app, ["clean", "--apply"], input="n\n")
    assert trashed == []


def test_undo(monkeypatch):
    monkeypatch.setattr(
        cli.actions, "undo_last", lambda client: [{"id": "old", "name": "report copy.pdf"}]
    )
    result = runner.invoke(cli.app, ["undo"])
    assert result.exit_code == 0
    assert "1 file" in result.output
