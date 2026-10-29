import pytest
from typer.testing import CliRunner

from gdrive_dedupe import cli
from gdrive_dedupe.core.models import DriveFile, DuplicateGroup, ScanResult

runner = CliRunner()


def result_fixture():
    from gdrive_dedupe.core.drive import FOLDER_MIME

    files = [
        DriveFile(
            id="new",
            name="report.pdf",
            size=100,
            md5="m",
            modified_time="2024-06-01T00:00:00Z",
            path="/Docs",
            parent="docs",
        ),
        DriveFile(
            id="old",
            name="report copy.pdf",
            size=100,
            md5="m",
            modified_time="2020-06-01T00:00:00Z",
            path="/Backup",
            parent="backup",
        ),
        DriveFile(
            id="archived",
            name="report archived.pdf",
            size=100,
            md5="m",
            modified_time="2022-06-01T00:00:00Z",
            path="/Archive/2022",
            parent="y2022",
        ),
        DriveFile(
            id="video",
            name="movie.mp4",
            size=5000,
            md5="v",
            modified_time="2023-01-01T00:00:00Z",
            path="/Docs",
            parent="docs",
        ),
    ]
    folders = [
        DriveFile(
            id="empty1",
            name="Old Backups",
            size=0,
            md5=None,
            modified_time="",
            mime_type=FOLDER_MIME,
            path="/Archive",
            parent="archive",
        ),
        DriveFile(
            id="docs", name="Docs", size=0, md5=None, modified_time="", mime_type=FOLDER_MIME
        ),
        DriveFile(
            id="archive", name="Archive", size=0, md5=None, modified_time="", mime_type=FOLDER_MIME
        ),
        DriveFile(
            id="backup", name="Backup", size=0, md5=None, modified_time="", mime_type=FOLDER_MIME
        ),
        DriveFile(
            id="y2022",
            name="2022",
            size=0,
            md5=None,
            modified_time="",
            mime_type=FOLDER_MIME,
            path="/Archive",
            parent="archive",
        ),
    ]
    return ScanResult(
        total_files=10,
        skipped_no_md5=1,
        groups=[DuplicateGroup(md5="m", files=files[:3])],
        files=files,
        folders=folders,
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
    assert sorted(f.id for f in trashed) == ["archived", "old"]


def test_clean_apply_aborts_on_no(monkeypatch):
    trashed = []
    monkeypatch.setattr(
        cli.actions,
        "trash_files",
        lambda client, files, progress_cb=None: trashed.extend(files) or "b1",
    )
    runner.invoke(cli.app, ["clean", "--apply"], input="n\n")
    assert trashed == []


def test_empty_folders_dry_run(monkeypatch):
    trashed = []
    monkeypatch.setattr(
        cli.actions,
        "trash_files",
        lambda client, items, progress_cb=None: trashed.extend(items) or "b1",
    )
    result = runner.invoke(cli.app, ["empty-folders"])
    assert result.exit_code == 0
    assert "Old Backups" in result.output
    assert "dry run" in result.output.lower()
    assert trashed == []


def test_empty_folders_apply(monkeypatch):
    trashed = []
    monkeypatch.setattr(
        cli.actions,
        "trash_files",
        lambda client, items, progress_cb=None: trashed.extend(items) or "b1",
    )
    result = runner.invoke(cli.app, ["empty-folders", "--apply"], input="y\n")
    assert result.exit_code == 0
    assert [x.id for x in trashed] == ["empty1"]


def test_old_files_dry_run():
    result = runner.invoke(cli.app, ["old-files", "--before", "2021-01-01"])
    assert result.exit_code == 0
    assert "report copy.pdf" in result.output
    assert "report.pdf  (/Docs)" not in result.output
    assert "dry run" in result.output.lower()


def test_old_files_rejects_bad_date():
    result = runner.invoke(cli.app, ["old-files", "--before", "nonsense"])
    assert result.exit_code != 0


def test_undo(monkeypatch):
    monkeypatch.setattr(
        cli.actions, "undo_last", lambda client: [{"id": "old", "name": "report copy.pdf"}]
    )
    result = runner.invoke(cli.app, ["undo"])
    assert result.exit_code == 0
    assert "1 file" in result.output


# ---- keep rules ----


def trashed_by(monkeypatch, args):
    trashed = []
    monkeypatch.setattr(
        cli.actions,
        "trash_files",
        lambda client, items, progress_cb=None: trashed.extend(items) or "b1",
    )
    result = runner.invoke(cli.app, args, input="y\n")
    assert result.exit_code == 0, result.output
    return sorted(x.id for x in trashed), result.output


def test_clean_protect_makes_archive_copy_the_keeper(monkeypatch):
    ids, out = trashed_by(monkeypatch, ["clean", "--apply", "--protect", "/Archive"])
    assert ids == ["new", "old"]
    assert "never touching /Archive" in out


def test_clean_prefer_folder_changes_keeper(monkeypatch):
    ids, _ = trashed_by(monkeypatch, ["clean", "--apply", "--prefer", "/Backup"])
    assert ids == ["archived", "new"]


def test_clean_shallowest_strategy(monkeypatch):
    ids, _ = trashed_by(monkeypatch, ["clean", "--apply", "--strategy", "shallowest"])
    # /Docs and /Backup are equally shallow; newest (/Docs) wins
    assert ids == ["archived", "old"]


def test_clean_rejects_unknown_protect_folder():
    result = runner.invoke(cli.app, ["clean", "--protect", "/Nope"])
    assert result.exit_code != 0
    assert "Nope" in result.output


# ---- folder scope ----


def test_scan_folder_scope_only_reports_subtree():
    result = runner.invoke(cli.app, ["scan", "--folder", "/Archive"])
    assert result.exit_code == 0
    assert "0 duplicate groups" in result.output
    assert "1 files scanned" in result.output


def test_clean_folder_scope_with_no_dupes_inside():
    result = runner.invoke(cli.app, ["clean", "--folder", "/Docs"])
    assert result.exit_code == 0
    assert "No duplicates" in result.output


def test_scan_rejects_unknown_folder():
    result = runner.invoke(cli.app, ["scan", "--folder", "/Nope"])
    assert result.exit_code != 0


# ---- largest files ----


def test_largest_files_lists_biggest_first(monkeypatch):
    result = runner.invoke(cli.app, ["largest-files", "--limit", "2"])
    assert result.exit_code == 0
    lines = [ln for ln in result.output.splitlines() if ln.strip().startswith("- ")]
    assert "movie.mp4" in lines[0]
    assert len(lines) == 2
    assert "dry run" in result.output.lower()


def test_largest_files_apply(monkeypatch):
    ids, _ = trashed_by(monkeypatch, ["largest-files", "--limit", "1", "--apply"])
    assert ids == ["video"]


# ---- purge ----


def test_purge_lists_batches_when_no_batch_given(monkeypatch):
    monkeypatch.setattr(
        cli.actions,
        "list_batches",
        lambda: [
            {
                "batch_id": "abc123",
                "timestamp": "2026-01-01T00:00:00+00:00",
                "count": 3,
                "files": [],
                "status": "trashed",
            },
        ],
    )
    result = runner.invoke(cli.app, ["purge"])
    assert result.exit_code == 0
    assert "abc123" in result.output and "trashed" in result.output


def test_purge_dry_run_shows_files(monkeypatch):
    monkeypatch.setattr(
        cli.actions,
        "list_batches",
        lambda: [
            {
                "batch_id": "abc123",
                "timestamp": "t",
                "count": 1,
                "files": [{"id": "x", "name": "x.bin"}],
                "status": "trashed",
            }
        ],
    )
    purged = []
    monkeypatch.setattr(cli.actions, "purge_batch", lambda c, b, progress_cb=None: purged.append(b))
    result = runner.invoke(cli.app, ["purge", "--batch", "abc123"])
    assert result.exit_code == 0
    assert "x.bin" in result.output
    assert "dry run" in result.output.lower()
    assert purged == []


def test_purge_apply_requires_typing_batch_id(monkeypatch):
    monkeypatch.setattr(
        cli.actions,
        "list_batches",
        lambda: [
            {
                "batch_id": "abc123",
                "timestamp": "t",
                "count": 1,
                "files": [{"id": "x", "name": "x.bin"}],
                "status": "trashed",
            }
        ],
    )
    purged = []
    monkeypatch.setattr(cli.actions, "purge_batch", lambda c, b, progress_cb=None: purged.append(b))
    result = runner.invoke(cli.app, ["purge", "--batch", "abc123", "--apply"], input="wrong\n")
    assert result.exit_code != 0
    assert purged == []


def test_purge_apply_deletes_after_typed_confirmation(monkeypatch):
    monkeypatch.setattr(
        cli.actions,
        "list_batches",
        lambda: [
            {
                "batch_id": "abc123",
                "timestamp": "t",
                "count": 1,
                "files": [{"id": "x", "name": "x.bin"}],
                "status": "trashed",
            }
        ],
    )
    monkeypatch.setattr(
        cli.actions,
        "purge_batch",
        lambda c, b, progress_cb=None: {"batch_id": b, "deleted": [{"id": "x"}], "skipped": []},
    )
    result = runner.invoke(cli.app, ["purge", "--batch", "abc123", "--apply"], input="abc123\n")
    assert result.exit_code == 0, result.output
    assert "1" in result.output and "deleted" in result.output.lower()


def test_purge_unknown_batch_fails(monkeypatch):
    monkeypatch.setattr(cli.actions, "list_batches", lambda: [])
    result = runner.invoke(cli.app, ["purge", "--batch", "nope"])
    assert result.exit_code != 0


# ---- web host option ----


def test_web_host_option_is_passed_to_uvicorn(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: seen.update(kw))
    monkeypatch.setattr(cli.webbrowser, "open", lambda url: None)
    result = runner.invoke(cli.app, ["web", "--host", "0.0.0.0", "--port", "9000", "--no-browser"])
    assert result.exit_code == 0, result.output
    assert seen["host"] == "0.0.0.0" and seen["port"] == 9000


def test_web_host_defaults_to_loopback(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: seen.update(kw))
    result = runner.invoke(cli.app, ["web", "--no-browser"])
    assert result.exit_code == 0, result.output
    assert seen["host"] == "127.0.0.1"


def test_web_concrete_host_is_added_to_allowed_hosts(monkeypatch):
    seen = {}
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: seen.update(app=app, **kw))
    monkeypatch.setenv("GDRIVE_DEDUPE_TOKEN", "t0k")
    result = runner.invoke(cli.app, ["web", "--host", "192.168.1.20", "--no-browser"])
    assert result.exit_code == 0, result.output
    from fastapi.testclient import TestClient

    tc = TestClient(seen["app"], base_url="http://127.0.0.1")
    headers = {"Host": "192.168.1.20:8765", "X-Dedupe-Token": "t0k"}
    assert tc.get("/api/auth/status", headers=headers).status_code == 200
    headers["Host"] = "192.168.1.21:8765"
    assert tc.get("/api/auth/status", headers=headers).status_code == 400


# ---- access token when binding beyond loopback ----


def _serve(monkeypatch, args, env=None):
    seen = {}
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: seen.update(app=app, **kw))
    for key in ("GDRIVE_DEDUPE_TOKEN", "GDRIVE_DEDUPE_HOST"):
        monkeypatch.delenv(key, raising=False)
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    result = runner.invoke(cli.app, ["web", "--no-browser", *args])
    assert result.exit_code == 0, result.output
    from fastapi.testclient import TestClient

    return result.output, TestClient(seen["app"], base_url="http://127.0.0.1")


def test_web_loopback_needs_no_token(monkeypatch):
    output, tc = _serve(monkeypatch, [])
    assert "token" not in output.lower()
    assert tc.get("/api/auth/status").status_code == 200


def test_web_wildcard_bind_generates_token_and_prints_url(monkeypatch):
    output, tc = _serve(monkeypatch, ["--host", "0.0.0.0"])
    import re

    match = re.search(r"http://127\.0\.0\.1:8765/\?token=([A-Za-z0-9_-]{20,})", output)
    assert match, output
    token = match.group(1)
    assert tc.get("/api/auth/status").status_code == 401
    assert tc.get("/api/auth/status", headers={"X-Dedupe-Token": token}).status_code == 200


def test_web_token_env_is_honored(monkeypatch):
    output, tc = _serve(
        monkeypatch, ["--host", "0.0.0.0"], env={"GDRIVE_DEDUPE_TOKEN": "fixed-token-123"}
    )
    assert "token=fixed-token-123" in output
    assert (
        tc.get("/api/auth/status", headers={"X-Dedupe-Token": "fixed-token-123"}).status_code == 200
    )


def test_web_token_env_applies_even_on_loopback(monkeypatch):
    _, tc = _serve(monkeypatch, [], env={"GDRIVE_DEDUPE_TOKEN": "fixed-token-123"})
    assert tc.get("/api/auth/status").status_code == 401
