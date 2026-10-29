import pytest
from googleapiclient.errors import HttpError

from gdrive_dedupe.core.drive import FOLDER_MIME, DriveClient


class FakeResp:
    def __init__(self, status):
        self.status = status
        self.reason = "err"


def http_error(status):
    return HttpError(resp=FakeResp(status), content=b"err")


class FakeRequest:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)

    def execute(self):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeFiles:
    def __init__(self, pages, update_outcomes=None):
        self.pages = pages
        self.update_outcomes = update_outcomes or [{}]
        self.update_calls = []

    def list(self, **kwargs):
        page_index = 0 if kwargs.get("pageToken") is None else int(kwargs["pageToken"])
        return FakeRequest([self.pages[page_index]])

    def update(self, fileId, body):
        self.update_calls.append((fileId, body))
        return FakeRequest(list(self.update_outcomes))


class FakeService:
    def __init__(self, files):
        self._files = files

    def files(self):
        return self._files


def drive_file(id, name, parents=None, mime="text/plain", size="10"):
    item = {
        "id": id,
        "name": name,
        "mimeType": mime,
        "size": size,
        "md5Checksum": "m",
        "modifiedTime": "2024-01-01T00:00:00Z",
    }
    if parents:
        item["parents"] = parents
    return item


def test_pagination_and_progress():
    pages = [
        {"files": [drive_file("a", "a.txt")], "nextPageToken": "1"},
        {"files": [drive_file("b", "b.txt")]},
    ]
    counts = []
    files = DriveClient(FakeService(FakeFiles(pages))).list_owned_files(counts.append)
    assert [f.id for f in files] == ["a", "b"]
    assert counts == [1, 2]


def test_folder_paths_resolved_and_folders_excluded():
    pages = [
        {
            "files": [
                {"id": "root-folder", "name": "Photos", "mimeType": FOLDER_MIME},
                {"id": "sub", "name": "2024", "mimeType": FOLDER_MIME, "parents": ["root-folder"]},
                drive_file("f1", "pic.jpg", parents=["sub"]),
            ]
        }
    ]
    files = DriveClient(FakeService(FakeFiles(pages))).list_owned_files()
    assert len(files) == 1
    assert files[0].path == "/Photos/2024"
    assert files[0].parent == "sub"


def test_list_owned_items_returns_folders_with_paths():
    pages = [
        {
            "files": [
                {"id": "root-folder", "name": "Photos", "mimeType": FOLDER_MIME},
                {"id": "sub", "name": "2024", "mimeType": FOLDER_MIME, "parents": ["root-folder"]},
            ]
        }
    ]
    files, folders = DriveClient(FakeService(FakeFiles(pages))).list_owned_items()
    assert files == []
    by_id = {f.id: f for f in folders}
    assert by_id["sub"].path == "/Photos"
    assert by_id["sub"].parent == "root-folder"
    assert by_id["root-folder"].path == "/"


def test_backoff_retries_then_succeeds():
    fake_files = FakeFiles([{}])
    fake_files.list = lambda **kw: FakeRequest([http_error(429), {"files": []}])
    sleeps = []
    client = DriveClient(FakeService(fake_files), sleep=sleeps.append)
    assert client.list_owned_files() == []
    assert len(sleeps) == 1


def test_non_retryable_error_raises():
    fake_files = FakeFiles([{}])
    fake_files.list = lambda **kw: FakeRequest([http_error(404)])
    with pytest.raises(HttpError):
        DriveClient(FakeService(fake_files), sleep=lambda s: None).list_owned_files()


def test_network_timeout_retries_then_succeeds():
    fake_files = FakeFiles([{}])
    fake_files.update = lambda fileId, body: FakeRequest(
        [TimeoutError("The read operation timed out"), {}]
    )
    sleeps = []
    client = DriveClient(FakeService(fake_files), sleep=sleeps.append)
    client.set_trashed("f9", True)  # should not raise
    assert len(sleeps) == 1


def test_network_timeout_exhausts_retries_then_raises():
    fake_files = FakeFiles([{}])
    fake_files.update = lambda fileId, body: FakeRequest([TimeoutError()] * 5)
    client = DriveClient(FakeService(fake_files), max_retries=5, sleep=lambda s: None)
    with pytest.raises(TimeoutError):
        client.set_trashed("f9", True)


def test_connection_error_retries_then_succeeds():
    fake_files = FakeFiles([{}])
    fake_files.list = lambda **kw: FakeRequest(
        [ConnectionResetError("connection reset"), {"files": []}]
    )
    sleeps = []
    client = DriveClient(FakeService(fake_files), sleep=sleeps.append)
    assert client.list_owned_files() == []
    assert len(sleeps) == 1


def test_set_trashed_calls_update():
    fake_files = FakeFiles([{}])
    DriveClient(FakeService(fake_files)).set_trashed("f9", True)
    assert fake_files.update_calls == [("f9", {"trashed": True})]
