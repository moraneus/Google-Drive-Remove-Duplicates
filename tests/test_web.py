import pytest
from fastapi.testclient import TestClient

from gdrive_dedupe.core.drive import FOLDER_MIME
from gdrive_dedupe.core.models import DriveFile, DuplicateGroup, ScanResult
from gdrive_dedupe.web import app as webapp


def f(id, md5="m", modified="2024-01-01T00:00:00Z", parent="", path="/", size=10):
    return DriveFile(
        id=id,
        name=f"{id}.txt",
        size=size,
        md5=md5,
        modified_time=modified,
        parent=parent,
        path=path,
    )


def folder(id, parent="", path="/", name=None):
    return DriveFile(
        id=id,
        name=name or id,
        size=0,
        md5=None,
        modified_time="",
        mime_type=FOLDER_MIME,
        parent=parent,
        path=path,
    )


FILES = [
    f("keep", modified="2024-06-01T00:00:00Z", parent="full"),
    f("dupe", modified="2020-01-01T00:00:00Z"),
    f("lonely", md5="z", modified="2019-03-01T00:00:00Z"),
    f("p1", md5="p", modified="2023-01-01T00:00:00Z", parent="photos", path="/Photos", size=25),
    f("p2", md5="p", modified="2024-01-01T00:00:00Z", parent="y2023", path="/Photos/2023", size=25),
    f("p3", md5="p", modified="2022-01-01T00:00:00Z", parent="archive", path="/Archive", size=25),
]

RESULT = ScanResult(
    total_files=7,
    skipped_no_md5=0,
    groups=[
        DuplicateGroup(md5="m", files=[FILES[0], FILES[1]]),
        DuplicateGroup(md5="p", files=[FILES[3], FILES[4], FILES[5]]),
    ],
    files=FILES,
    folders=[
        folder("full"),
        folder("emptyA"),
        folder("emptyB", parent="emptyA"),
        folder("photos", name="Photos"),
        folder("y2023", parent="photos", path="/Photos", name="2023"),
        folder("archive", name="Archive"),
    ],
)


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GDRIVE_DEDUPE_HOME", str(tmp_path))
    application = webapp.create_app()
    application.state.scan.finish(RESULT)
    return TestClient(application, base_url="http://127.0.0.1")


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
    assert body["reclaimable_bytes"] == 60


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


# ---- folders, scope, keep rules ----


def test_folders_lists_full_paths_sorted(client):
    body = client.get("/api/folders").json()
    assert [x["path"] for x in body["folders"]] == [
        "/Archive",
        "/emptyA",
        "/emptyB",
        "/full",
        "/Photos",
        "/Photos/2023",
    ]
    assert body["folders"][-1] == {"id": "y2023", "path": "/Photos/2023"}


def test_result_scoped_to_folder(client):
    body = client.get("/api/scan/result?folder=photos").json()
    assert body["total_files"] == 2
    assert [g["md5"] for g in body["groups"]] == ["p"]
    assert {x["id"] for x in body["groups"][0]["files"]} == {"p1", "p2"}
    assert body["scope"] == {"id": "photos", "path": "/Photos"}


def test_result_unscoped_reports_root_scope(client):
    assert client.get("/api/scan/result").json()["scope"] is None


def test_result_rejects_unknown_folder(client):
    resp = client.get("/api/scan/result?folder=nope")
    assert resp.status_code == 400


def test_result_shallowest_strategy(client):
    body = client.get("/api/scan/result?strategy=shallowest").json()
    group = next(g for g in body["groups"] if g["md5"] == "p")
    flags = {x["id"]: x["suggested_trash"] for x in group["files"]}
    # p1 (/Photos) and p3 (/Archive) are equally shallow; p1 is newer
    assert flags == {"p1": False, "p2": True, "p3": True}


def test_result_prefer_folder(client):
    body = client.get("/api/scan/result?strategy=newest&prefer=archive").json()
    group = next(g for g in body["groups"] if g["md5"] == "p")
    flags = {x["id"]: x["suggested_trash"] for x in group["files"]}
    assert flags == {"p1": True, "p2": True, "p3": False}


def test_result_protected_folder_marks_files_and_never_suggests(client):
    body = client.get("/api/scan/result?protected=archive&protected=y2023").json()
    group = next(g for g in body["groups"] if g["md5"] == "p")
    by_id = {x["id"]: x for x in group["files"]}
    assert by_id["p3"]["protected"] is True and by_id["p3"]["suggested_trash"] is False
    assert by_id["p2"]["protected"] is True and by_id["p2"]["suggested_trash"] is False
    assert by_id["p1"]["protected"] is False and by_id["p1"]["suggested_trash"] is True


def test_clean_refuses_protected_file(client, monkeypatch):
    connected(monkeypatch, write_access=True)
    resp = client.post("/api/clean", json={"file_ids": ["p3"], "protected": ["archive"]})
    assert resp.status_code == 400
    assert "protected" in resp.json()["detail"]


def test_trash_refuses_protected_file(client, monkeypatch):
    connected(monkeypatch, write_access=True)
    resp = client.post("/api/trash", json={"file_ids": ["p2"], "protected": ["photos"]})
    assert resp.status_code == 400


# ---- largest files ----


def test_largest_files_sorted_and_limited(client):
    body = client.get("/api/largest-files?limit=2").json()
    assert [x["id"] for x in body["files"]] == ["p1", "p2"]
    assert body["count"] == 2
    assert body["total_bytes"] == 50


def test_largest_files_honors_scope(client):
    body = client.get("/api/largest-files?limit=10&folder=archive").json()
    assert [x["id"] for x in body["files"]] == ["p3"]


def test_largest_files_rejects_bad_limit(client):
    assert client.get("/api/largest-files?limit=0").status_code == 422


def test_trash_accepts_largest_file(client, monkeypatch):
    connected(monkeypatch, write_access=True)
    monkeypatch.setattr(
        webapp.actions, "trash_files", lambda client_, items, progress_cb=None: "batch9"
    )
    assert client.post("/api/trash", json={"file_ids": ["p1"]}).json()["trashed"] == 1


# ---- batches & purge ----


def test_batches_lists_audit_log(client, monkeypatch):
    monkeypatch.setattr(
        webapp.actions,
        "list_batches",
        lambda: [
            {"batch_id": "b1", "timestamp": "t", "count": 2, "files": [], "status": "trashed"}
        ],
    )
    body = client.get("/api/batches").json()
    assert body["batches"][0]["batch_id"] == "b1"


def test_purge_requires_write_access(client, monkeypatch):
    monkeypatch.setattr(webapp.auth, "load_credentials", lambda write_access=False: None)
    resp = client.post("/api/purge", json={"batch_id": "b1", "confirm": "b1"})
    assert resp.status_code == 403


def test_purge_requires_typed_confirmation(client, monkeypatch):
    connected(monkeypatch, write_access=True)
    called = []
    monkeypatch.setattr(
        webapp.actions, "purge_batch", lambda c, b, progress_cb=None: called.append(b)
    )
    resp = client.post("/api/purge", json={"batch_id": "b1", "confirm": "wrong"})
    assert resp.status_code == 400
    assert called == []


def test_purge_unknown_batch_is_404(client, monkeypatch):
    connected(monkeypatch, write_access=True)

    def boom(c, b, progress_cb=None):
        raise LookupError("unknown batch")

    monkeypatch.setattr(webapp.actions, "purge_batch", boom)
    assert client.post("/api/purge", json={"batch_id": "zz", "confirm": "zz"}).status_code == 404


def test_purge_undone_batch_is_409(client, monkeypatch):
    connected(monkeypatch, write_access=True)

    def boom(c, b, progress_cb=None):
        raise ValueError("batch was undone")

    monkeypatch.setattr(webapp.actions, "purge_batch", boom)
    assert client.post("/api/purge", json={"batch_id": "b1", "confirm": "b1"}).status_code == 409


def test_purge_happy_path(client, monkeypatch):
    connected(monkeypatch, write_access=True)
    monkeypatch.setattr(
        webapp.actions,
        "purge_batch",
        lambda c, b, progress_cb=None: {
            "batch_id": b,
            "deleted": [{"id": "a", "name": "a"}],
            "skipped": [{"id": "b", "name": "b"}],
        },
    )
    body = client.post("/api/purge", json={"batch_id": "b1", "confirm": "b1"}).json()
    assert body == {"batch_id": "b1", "deleted": 1, "skipped": 1}


# ---- request-origin hardening (CSRF / DNS rebinding) ----


def test_rejects_untrusted_host_header(client):
    resp = client.get("/api/auth/status", headers={"Host": "evil.example.com"})
    assert resp.status_code == 400


def test_allows_localhost_hosts(client):
    for host in ("127.0.0.1:8765", "localhost:8765"):
        assert client.get("/api/auth/status", headers={"Host": host}).status_code == 200


def test_extra_allowed_host_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GDRIVE_DEDUPE_HOME", str(tmp_path))
    monkeypatch.setenv("GDRIVE_DEDUPE_ALLOWED_HOSTS", "dedupe.lan, nas.local")
    tc = TestClient(webapp.create_app(), base_url="http://127.0.0.1")
    assert tc.get("/api/auth/status", headers={"Host": "nas.local:8765"}).status_code == 200


def test_post_with_foreign_origin_is_refused(client, monkeypatch):
    connected(monkeypatch, write_access=True)
    called = []

    def fake_purge(c, b, progress_cb=None):
        called.append(b)
        return {"batch_id": b, "deleted": [], "skipped": []}

    monkeypatch.setattr(webapp.actions, "purge_batch", fake_purge)
    resp = client.post(
        "/api/purge",
        json={"batch_id": "b1", "confirm": "b1"},
        headers={"Origin": "http://evil.example.com"},
    )
    assert resp.status_code == 403
    assert called == []


def test_post_with_own_origin_is_allowed(client, monkeypatch):
    connected(monkeypatch, write_access=True)
    monkeypatch.setattr(webapp.actions, "undo_last", lambda c: [{"id": "a", "name": "a"}])
    headers = {"Origin": "http://127.0.0.1:8765", "Host": "127.0.0.1:8765"}
    assert client.post("/api/undo", headers=headers).status_code == 200


def test_post_from_other_local_port_is_refused(client, monkeypatch):
    connected(monkeypatch, write_access=True)
    monkeypatch.setattr(webapp.actions, "undo_last", lambda c: [{"id": "a", "name": "a"}])
    headers = {"Origin": "http://127.0.0.1:9999", "Host": "127.0.0.1:8765"}
    assert client.post("/api/undo", headers=headers).status_code == 403


def test_post_with_wrong_scheme_origin_is_refused(client, monkeypatch):
    connected(monkeypatch, write_access=True)
    monkeypatch.setattr(webapp.actions, "undo_last", lambda c: [{"id": "a", "name": "a"}])
    headers = {"Origin": "https://127.0.0.1:8765", "Host": "127.0.0.1:8765"}
    assert client.post("/api/undo", headers=headers).status_code == 403


def test_null_origin_is_refused(client):
    assert client.get("/api/batches", headers={"Origin": "null"}).status_code == 403


def test_get_with_foreign_origin_is_still_refused(client):
    resp = client.get("/api/batches", headers={"Origin": "http://evil.example.com"})
    assert resp.status_code == 403


def test_create_app_accepts_extra_allowed_hosts(monkeypatch, tmp_path):
    monkeypatch.setenv("GDRIVE_DEDUPE_HOME", str(tmp_path))
    tc = TestClient(webapp.create_app(allowed_hosts=["192.168.1.20"]), base_url="http://127.0.0.1")
    assert tc.get("/api/auth/status", headers={"Host": "192.168.1.20:8765"}).status_code == 200
    assert tc.get("/api/auth/status", headers={"Host": "192.168.1.21:8765"}).status_code == 400


# ---- access token (required when not bound to loopback) ----


@pytest.fixture()
def token_client(monkeypatch, tmp_path):
    monkeypatch.setenv("GDRIVE_DEDUPE_HOME", str(tmp_path))
    application = webapp.create_app(token="s3cret")
    application.state.scan.finish(RESULT)
    return TestClient(application, base_url="http://127.0.0.1")


def test_token_required_for_api(token_client):
    assert token_client.get("/api/auth/status").status_code == 401


def test_token_accepted_in_header(token_client):
    resp = token_client.get("/api/auth/status", headers={"X-Dedupe-Token": "s3cret"})
    assert resp.status_code == 200


def test_token_rejected_when_wrong(token_client):
    resp = token_client.get("/api/auth/status", headers={"X-Dedupe-Token": "nope"})
    assert resp.status_code == 401


def test_token_not_needed_for_page_and_static(token_client):
    assert token_client.get("/").status_code == 200
    assert token_client.get("/static/app.js").status_code == 200


def test_no_token_configured_means_open_loopback_api(client):
    assert client.get("/api/auth/status").status_code == 200


def test_token_accepted_in_query_for_event_stream(token_client):
    # EventSource cannot set headers, so the stream must accept ?token=
    with token_client.stream("GET", "/api/scan/events?token=s3cret") as resp:
        assert resp.status_code == 200
        first = next(resp.iter_lines())
        assert first.startswith("data:")


def test_query_token_not_accepted_for_other_endpoints(token_client):
    assert token_client.get("/api/auth/status?token=s3cret").status_code == 401
    assert token_client.post("/api/scan?token=s3cret").status_code == 401


def test_files_expose_checksum_and_parent_folder(client):
    body = client.get("/api/scan/result").json()
    group = next(g for g in body["groups"] if g["md5"] == "p")
    first = group["files"][0]
    assert first["md5"] == "p"
    assert first["parent"] == "photos"
    tool = client.get("/api/largest-files?limit=1").json()["files"][0]
    assert tool["md5"] == "p" and tool["parent"] == "photos"
