import json

import pytest

from gdrive_dedupe.core import actions
from gdrive_dedupe.core.models import DriveFile


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GDRIVE_DEDUPE_HOME", str(tmp_path))
    return tmp_path


class FakeClient:
    def __init__(self):
        self.calls = []

    def set_trashed(self, file_id, trashed):
        self.calls.append((file_id, trashed))


def f(id):
    return DriveFile(id=id, name=f"{id}.txt", size=1, md5="m", modified_time="")


def read_log():
    return [json.loads(line) for line in actions.audit_path().read_text().splitlines()]


def test_trash_writes_audit_before_trashing_and_reports_progress():
    client = FakeClient()
    progress = []
    batch_id = actions.trash_files(
        client, [f("a"), f("b")], progress_cb=lambda done, total: progress.append((done, total))
    )
    assert client.calls == [("a", True), ("b", True)]
    assert progress == [(1, 2), (2, 2)]
    log = read_log()
    assert log[0]["action"] == "trash"
    assert log[0]["batch_id"] == batch_id
    assert [x["id"] for x in log[0]["files"]] == ["a", "b"]


def test_undo_last_restores_and_records():
    client = FakeClient()
    actions.trash_files(client, [f("a")])
    restored = actions.undo_last(client)
    assert restored == [{"id": "a", "name": "a.txt"}]
    assert ("a", False) in client.calls
    assert read_log()[-1]["action"] == "undo"


def test_undo_twice_raises():
    client = FakeClient()
    actions.trash_files(client, [f("a")])
    actions.undo_last(client)
    with pytest.raises(LookupError):
        actions.undo_last(client)


def test_undo_with_no_log_raises():
    with pytest.raises(LookupError):
        actions.undo_last(FakeClient())
