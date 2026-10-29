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


# ---- purge (permanent deletion of a tool-made batch) ----

from googleapiclient.errors import HttpError  # noqa: E402


class _Resp:
    def __init__(self, status):
        self.status = status
        self.reason = "err"


class PurgeClient(FakeClient):
    def __init__(self, trashed_state=None, missing=()):
        super().__init__()
        self.trashed_state = trashed_state or {}
        self.missing = set(missing)
        self.deleted = []

    def is_trashed(self, file_id):
        if file_id in self.missing:
            raise HttpError(resp=_Resp(404), content=b"not found")
        return self.trashed_state.get(file_id, True)

    def delete_file(self, file_id):
        self.deleted.append(file_id)


def test_list_batches_reports_status_newest_first():
    client = FakeClient()
    first = actions.trash_files(client, [f("a")])
    second = actions.trash_files(client, [f("b"), f("c")])
    actions.undo_last(client)  # undoes `second`
    batches = actions.list_batches()
    assert [b["batch_id"] for b in batches] == [second, first]
    assert batches[0]["status"] == "undone"
    assert batches[0]["count"] == 2
    assert batches[1]["status"] == "trashed"
    assert batches[1]["files"] == [{"id": "a", "name": "a.txt"}]


def test_list_batches_empty_without_log():
    assert actions.list_batches() == []


def test_purge_deletes_only_files_still_in_trash_and_records():
    client = PurgeClient(trashed_state={"a": True, "b": False}, missing={"c"})
    batch_id = actions.trash_files(client, [f("a"), f("b"), f("c")])
    progress = []
    outcome = actions.purge_batch(
        client, batch_id, progress_cb=lambda d, t: progress.append((d, t))
    )
    assert client.deleted == ["a"]
    assert [x["id"] for x in outcome["deleted"]] == ["a"]
    assert sorted(x["id"] for x in outcome["skipped"]) == ["b", "c"]
    assert progress == [(1, 3), (2, 3), (3, 3)]
    last = read_log()[-1]
    assert last["action"] == "purge"
    assert last["batch_id"] == batch_id
    assert [x["id"] for x in last["files"]] == ["a"]
    assert actions.list_batches()[0]["status"] == "purged"


def test_purge_unknown_batch_raises():
    with pytest.raises(LookupError):
        actions.purge_batch(PurgeClient(), "nope")


def test_purge_refuses_undone_batch():
    client = PurgeClient()
    batch_id = actions.trash_files(client, [f("a")])
    actions.undo_last(client)
    with pytest.raises(ValueError, match="undone"):
        actions.purge_batch(client, batch_id)
    assert client.deleted == []


def test_purge_twice_raises():
    client = PurgeClient()
    batch_id = actions.trash_files(client, [f("a")])
    actions.purge_batch(client, batch_id)
    with pytest.raises(ValueError, match="already purged"):
        actions.purge_batch(client, batch_id)


def test_undo_skips_purged_batch():
    client = PurgeClient()
    first = actions.trash_files(client, [f("a")])
    second = actions.trash_files(client, [f("b")])
    actions.purge_batch(client, second)
    restored = actions.undo_last(client)
    assert restored == [{"id": "a", "name": "a.txt"}]
    assert read_log()[-1]["batch_id"] == first


def test_undo_raises_when_only_batch_is_purged():
    client = PurgeClient()
    batch_id = actions.trash_files(client, [f("a")])
    actions.purge_batch(client, batch_id)
    with pytest.raises(LookupError):
        actions.undo_last(client)
