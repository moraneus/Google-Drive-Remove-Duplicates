import pytest
from fastapi.testclient import TestClient

from gdrive_dedupe.core.drive import FOLDER_MIME
from gdrive_dedupe.core.models import DriveFile, DuplicateGroup, ScanResult
from gdrive_dedupe.web import app as webapp


def f(id, md5="m", modified="2024-01-01T00:00:00Z", parent=""):
    return DriveFile(
        id=id, name=f"{id}.txt", size=10, md5=md5, modified_time=modified, parent=parent
    )


def folder(id, parent=""):
    return DriveFile(
        id=id, name=id, size=0, md5=None, modified_time="", mime_type=FOLDER_MIME, parent=parent
    )


FILES = [
    f("keep", modified="2024-06-01T00:00:00Z", parent="full"),
    f("dupe", modified="2020-01-01T00:00:00Z"),
    f("lonely", md5="z", modified="2019-03-01T00:00:00Z"),
]

RESULT = ScanResult(
    total_files=4,
    skipped_no_md5=0,
    groups=[DuplicateGroup(md5="m", files=[FILES[0], FILES[1]])],
    files=FILES,
    folders=[folder("full"), folder("emptyA"), folder("emptyB", parent="emptyA")],
)


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GDRIVE_DEDUPE_HOME", str(tmp_path))
    application = webapp.create_app()
    application.state.scan.finish(RESULT)
    return TestClient(application)


def connected(monkeypatch, write_access):
    monkeypatch.setattr(
        webapp.auth,
        "auth_status",
        lambda: {"connected": True, "write_access": write_access, "has_client_secrets": True},
    )
    monkeypatch.setattr(webapp.auth, "load_credentials", lambda write_access=False: object())
    monkeypatch.setattr(webapp.auth, "build_service", lambda creds: object())


def test_auth_status(client):
    body = client.get("/api/auth/status").json()
    assert body["connected"] is False


def test_upload_credentials_happy_path(client):
    content = '{"installed": {"client_id": "abc", "client_secret": "xyz"}}'
    body = client.post("/api/auth/credentials", json={"content": content}).json()
    assert body["saved"] is True
    assert client.get("/api/auth/status").json()["has_client_secrets"] is True


def test_upload_credentials_rejects_invalid(client):
    resp = client.post("/api/auth/credentials", json={"content": "garbage"})
    assert resp.status_code == 400
    assert "valid JSON" in resp.json()["detail"]


def test_scan_result_marks_suggested_trash(client):
    body = client.get("/api/scan/result?strategy=newest").json()
    group = body["groups"][0]
    flags = {x["id"]: x["suggested_trash"] for x in group["files"]}
    assert flags == {"keep": False, "dupe": True}
    assert body["reclaimable_bytes"] == 10


def test_clean_requires_write_access(client, monkeypatch):
    monkeypatch.setattr(webapp.auth, "load_credentials", lambda write_access=False: None)
    resp = client.post("/api/clean", json={"file_ids": ["dupe"]})
    assert resp.status_code == 403


def test_clean_rejects_unknown_ids(client, monkeypatch):
    connected(monkeypatch, write_access=True)
    resp = client.post("/api/clean", json={"file_ids": ["not-in-result"]})
    assert resp.status_code == 400


def test_clean_rejects_trashing_whole_group(client, monkeypatch):
    connected(monkeypatch, write_access=True)
    resp = client.post("/api/clean", json={"file_ids": ["keep", "dupe"]})
    assert resp.status_code == 400


def test_clean_happy_path(client, monkeypatch):
    connected(monkeypatch, write_access=True)
    monkeypatch.setattr(
        webapp.actions, "trash_files", lambda client_, files, progress_cb=None: "batch42"
    )
    body = client.post("/api/clean", json={"file_ids": ["dupe"]}).json()
    assert body == {"batch_id": "batch42", "trashed": 1}


def test_undo_when_empty_is_404(client, monkeypatch):
    connected(monkeypatch, write_access=True)

    def boom(client_):
        raise LookupError("nothing to undo")

    monkeypatch.setattr(webapp.actions, "undo_last", boom)
    assert client.post("/api/undo").status_code == 404


def test_empty_folders_returns_top_level_only(client):
    body = client.get("/api/empty-folders").json()
    assert body["count"] == 1
    assert body["folders"][0]["id"] == "emptyA"


def test_old_files_filters_and_totals(client):
    body = client.get("/api/old-files?before=2021-01-01").json()
    assert [x["id"] for x in body["files"]] == ["lonely", "dupe"]
    assert body["total_bytes"] == 20


def test_old_files_rejects_bad_date(client):
    assert client.get("/api/old-files?before=nonsense").status_code == 400


def test_trash_rejects_unknown_ids(client, monkeypatch):
    connected(monkeypatch, write_access=True)
    resp = client.post("/api/trash", json={"file_ids": ["emptyB"]})
    assert resp.status_code == 400


def test_trash_accepts_empty_folder_and_old_file(client, monkeypatch):
    connected(monkeypatch, write_access=True)
    seen = []
    monkeypatch.setattr(
        webapp.actions,
        "trash_files",
        lambda client_, items, progress_cb=None: seen.extend(items) or "batch7",
    )
    body = client.post("/api/trash", json={"file_ids": ["emptyA", "lonely"]}).json()
    assert body == {"batch_id": "batch7", "trashed": 2}
    assert [x.id for x in seen] == ["emptyA", "lonely"]


def test_trash_requires_write_access(client, monkeypatch):
    monkeypatch.setattr(webapp.auth, "load_credentials", lambda write_access=False: None)
    assert client.post("/api/trash", json={"file_ids": ["lonely"]}).status_code == 403


def test_scan_conflict_while_busy(client):
    client.app.state.scan.begin("scanning")
    assert client.post("/api/scan").status_code == 409


def test_serves_index(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
