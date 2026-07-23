# Google Drive Duplicate Finder v2.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite the single-script duplicate finder as a `gdrive-dedupe` Python package with a shared core engine, a local FastAPI web UI, and a Typer CLI, with a safety-first deletion model.

**Architecture:** One core engine (`gdrive_dedupe/core/`) handles auth, Drive API access, duplicate grouping, and trash/undo actions with an audit log. Two thin frontends consume it: a FastAPI app serving a no-build-step vanilla JS page, and a Typer CLI. All Drive mutations go through the audit-logged actions module.

**Tech Stack:** Python ≥3.11, uv, hatchling, FastAPI + uvicorn, Typer, google-api-python-client, ruff, pytest, GitHub Actions.

## Global Constraints

- Package name `gdrive-dedupe`, import name `gdrive_dedupe`, version `2.0.0`, Python `>=3.11`.
- Scopes: readonly = `https://www.googleapis.com/auth/drive.metadata.readonly`; full = `https://www.googleapis.com/auth/drive`. Never request full scope unless write access is explicitly requested.
- Config dir: `~/.gdrive-dedupe/` overridable via env var `GDRIVE_DEDUPE_HOME` (tests rely on this). Token saved with mode `0600`. Never pickle.
- All Drive mutations are trash-only (`trashed=True/False`), never `files().delete()`. Every trash batch writes an audit record to `<config>/audit.jsonl` **before** trashing.
- Web server binds `127.0.0.1` only.
- Run everything through uv: `uv run pytest`, `uv run ruff check .`.
- Commit after every task with the trailer `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- No test may touch the network or a real Google account.

## File Structure

```
pyproject.toml                      # packaging, ruff, pytest config
gdrive_dedupe/__init__.py
gdrive_dedupe/core/__init__.py
gdrive_dedupe/core/models.py        # DriveFile, DuplicateGroup, ScanResult, human_bytes
gdrive_dedupe/core/strategies.py    # keep-newest / keep-oldest selection
gdrive_dedupe/core/scanner.py       # group_duplicates, scan
gdrive_dedupe/core/auth.py          # OAuth, token storage, scope tiers
gdrive_dedupe/core/drive.py         # DriveClient: pagination, backoff, trash
gdrive_dedupe/core/actions.py       # trash_files, undo_last, audit log
gdrive_dedupe/web/__init__.py
gdrive_dedupe/web/state.py          # ScanState: background scan job state
gdrive_dedupe/web/app.py            # FastAPI app factory + routes + SSE
gdrive_dedupe/web/static/index.html
gdrive_dedupe/web/static/app.js
gdrive_dedupe/web/static/style.css
gdrive_dedupe/cli.py                # Typer CLI: scan, clean, undo, web
tests/test_models.py
tests/test_strategies.py
tests/test_scanner.py
tests/test_auth.py
tests/test_drive.py
tests/test_actions.py
tests/test_web.py
tests/test_cli.py
.github/workflows/ci.yml
README.md  SECURITY.md  CHANGELOG.md  docs/architecture.md
```

---

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`, `gdrive_dedupe/__init__.py`, `gdrive_dedupe/core/__init__.py`, `gdrive_dedupe/web/__init__.py`, `tests/__init__.py`, `tests/test_smoke.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: installable package `gdrive_dedupe` with `__version__ = "2.0.0"`; working `uv run pytest` and `uv run ruff check .`.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "gdrive-dedupe"
version = "2.0.0"
description = "Find and safely remove duplicate files in Google Drive — web UI and CLI"
readme = "README.md"
requires-python = ">=3.11"
license = { text = "MIT" }
authors = [{ name = "moraneus" }]
dependencies = [
    "google-api-python-client>=2.100",
    "google-auth>=2.23",
    "google-auth-oauthlib>=1.1",
    "fastapi>=0.110",
    "uvicorn>=0.29",
    "typer>=0.12",
]

[project.scripts]
gdrive-dedupe = "gdrive_dedupe.cli:app"

[dependency-groups]
dev = ["pytest>=8", "httpx>=0.27", "ruff>=0.4"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["gdrive_dedupe"]

[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Create package skeleton**

`gdrive_dedupe/__init__.py`:

```python
__version__ = "2.0.0"
```

`gdrive_dedupe/core/__init__.py`, `gdrive_dedupe/web/__init__.py`, `tests/__init__.py`: empty files.

`tests/test_smoke.py`:

```python
import gdrive_dedupe


def test_version():
    assert gdrive_dedupe.__version__ == "2.0.0"
```

- [ ] **Step 3: Ensure `.gitignore` covers secrets and artifacts**

Append these lines if not already present: `credentials.json`, `token.json`, `.venv/`, `__pycache__/`, `*.log`, `uv.lock` is **kept** (do NOT ignore it).

- [ ] **Step 4: Sync and verify**

Run: `uv sync && uv run pytest -q && uv run ruff check .`
Expected: 1 test passes, no lint errors.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock gdrive_dedupe tests .gitignore
git commit -m "feat: scaffold gdrive-dedupe package with uv, ruff, pytest"
```

---

### Task 2: Core models

**Files:**
- Create: `gdrive_dedupe/core/models.py`, `tests/test_models.py`

**Interfaces:**
- Produces:
  - `DriveFile(id: str, name: str, size: int, md5: str | None, modified_time: str, mime_type: str = "", web_view_link: str = "", path: str = "/")` — frozen dataclass
  - `DuplicateGroup(md5: str, files: list[DriveFile])` with `.size -> int`, `.wasted_bytes -> int`
  - `ScanResult(total_files: int, skipped_no_md5: int, groups: list[DuplicateGroup])` with `.duplicate_file_count -> int`, `.reclaimable_bytes -> int`
  - `human_bytes(n: int) -> str` (e.g. `1536` → `"1.5 KB"`)

- [ ] **Step 1: Write failing tests** (`tests/test_models.py`)

```python
from gdrive_dedupe.core.models import DriveFile, DuplicateGroup, ScanResult, human_bytes


def make_file(id="f1", size=100, md5="abc", modified="2024-01-01T00:00:00Z"):
    return DriveFile(id=id, name=f"{id}.txt", size=size, md5=md5, modified_time=modified)


def test_group_wasted_bytes_counts_all_but_one_copy():
    g = DuplicateGroup(md5="abc", files=[make_file("a"), make_file("b"), make_file("c")])
    assert g.size == 100
    assert g.wasted_bytes == 200


def test_scan_result_aggregates():
    g1 = DuplicateGroup(md5="a", files=[make_file("a", size=10), make_file("b", size=10)])
    g2 = DuplicateGroup(md5="b", files=[make_file("c", size=7), make_file("d", size=7),
                                        make_file("e", size=7)])
    r = ScanResult(total_files=50, skipped_no_md5=3, groups=[g1, g2])
    assert r.duplicate_file_count == 5
    assert r.reclaimable_bytes == 10 + 14


def test_human_bytes():
    assert human_bytes(0) == "0 B"
    assert human_bytes(1536) == "1.5 KB"
    assert human_bytes(3 * 1024**3) == "3.0 GB"
```

- [ ] **Step 2: Run to verify failure**: `uv run pytest tests/test_models.py -q` → import error.

- [ ] **Step 3: Implement** (`gdrive_dedupe/core/models.py`)

```python
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DriveFile:
    id: str
    name: str
    size: int
    md5: str | None
    modified_time: str  # RFC3339, e.g. "2024-01-01T00:00:00Z"
    mime_type: str = ""
    web_view_link: str = ""
    path: str = "/"


@dataclass
class DuplicateGroup:
    md5: str
    files: list[DriveFile]

    @property
    def size(self) -> int:
        return self.files[0].size if self.files else 0

    @property
    def wasted_bytes(self) -> int:
        return self.size * max(len(self.files) - 1, 0)


@dataclass
class ScanResult:
    total_files: int
    skipped_no_md5: int
    groups: list[DuplicateGroup] = field(default_factory=list)

    @property
    def duplicate_file_count(self) -> int:
        return sum(len(g.files) for g in self.groups)

    @property
    def reclaimable_bytes(self) -> int:
        return sum(g.wasted_bytes for g in self.groups)


def human_bytes(n: int) -> str:
    if n == 0:
        return "0 B"
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}".replace(".0 B", " B")
        value /= 1024
    return f"{value:.1f} TB"
```

Note: `human_bytes(0)` returns `"0 B"`; whole-byte values render without a decimal (`"512 B"`), larger units keep one decimal.

- [ ] **Step 4: Run**: `uv run pytest tests/test_models.py -q` → 3 passed. Adjust `human_bytes` until the exact expectations pass.

- [ ] **Step 5: Commit**: `git add ... && git commit -m "feat: core data models for files, duplicate groups, scan results"`

---

### Task 3: Keep strategies

**Files:**
- Create: `gdrive_dedupe/core/strategies.py`, `tests/test_strategies.py`

**Interfaces:**
- Consumes: `DriveFile`, `DuplicateGroup` from Task 2.
- Produces:
  - `STRATEGIES: tuple[str, ...] = ("newest", "oldest")`
  - `select_keeper(group: DuplicateGroup, strategy: str = "newest") -> DriveFile`
  - `files_to_trash(group: DuplicateGroup, strategy: str = "newest") -> list[DriveFile]` (everything except the keeper)

- [ ] **Step 1: Failing tests** (`tests/test_strategies.py`)

```python
import pytest

from gdrive_dedupe.core.models import DriveFile, DuplicateGroup
from gdrive_dedupe.core.strategies import files_to_trash, select_keeper


def f(id, modified):
    return DriveFile(id=id, name=id, size=1, md5="m", modified_time=modified)


GROUP = DuplicateGroup(md5="m", files=[
    f("old", "2020-05-01T10:00:00Z"),
    f("new", "2024-05-01T10:00:00Z"),
    f("mid", "2022-05-01T10:00:00Z"),
])


def test_keep_newest():
    assert select_keeper(GROUP, "newest").id == "new"
    assert {x.id for x in files_to_trash(GROUP, "newest")} == {"old", "mid"}


def test_keep_oldest():
    assert select_keeper(GROUP, "oldest").id == "old"


def test_unknown_strategy_raises():
    with pytest.raises(ValueError):
        select_keeper(GROUP, "bogus")


def test_unparseable_timestamp_sorts_last():
    g = DuplicateGroup(md5="m", files=[f("bad", ""), f("ok", "2021-01-01T00:00:00Z")])
    assert select_keeper(g, "newest").id == "ok"
```

- [ ] **Step 2: Run to fail**: `uv run pytest tests/test_strategies.py -q`

- [ ] **Step 3: Implement** (`gdrive_dedupe/core/strategies.py`)

```python
from __future__ import annotations

from datetime import datetime, timezone

from .models import DriveFile, DuplicateGroup

STRATEGIES: tuple[str, ...] = ("newest", "oldest")

_EPOCH = datetime(1, 1, 1, tzinfo=timezone.utc)


def _parse_time(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return _EPOCH


def select_keeper(group: DuplicateGroup, strategy: str = "newest") -> DriveFile:
    if strategy not in STRATEGIES:
        raise ValueError(f"unknown strategy {strategy!r}; expected one of {STRATEGIES}")
    ordered = sorted(group.files, key=lambda f: _parse_time(f.modified_time))
    return ordered[-1] if strategy == "newest" else ordered[0]


def files_to_trash(group: DuplicateGroup, strategy: str = "newest") -> list[DriveFile]:
    keeper = select_keeper(group, strategy)
    return [f for f in group.files if f.id != keeper.id]
```

- [ ] **Step 4: Run to pass**, **Step 5: Commit** (`feat: keep-newest/oldest strategies`).

---

### Task 4: Duplicate grouping / scanner

**Files:**
- Create: `gdrive_dedupe/core/scanner.py`, `tests/test_scanner.py`

**Interfaces:**
- Consumes: models from Task 2; `DriveClient.list_owned_files(progress_cb)` (Task 6 — for `scan()`, typed as any object with that method).
- Produces:
  - `group_duplicates(files: Iterable[DriveFile]) -> ScanResult` — groups by md5, only groups with ≥2 files, groups sorted by `wasted_bytes` desc, files without md5 counted in `skipped_no_md5`.
  - `scan(client, progress_cb=None) -> ScanResult` — `client.list_owned_files(progress_cb)` then `group_duplicates`.

- [ ] **Step 1: Failing tests** (`tests/test_scanner.py`)

```python
from gdrive_dedupe.core.models import DriveFile
from gdrive_dedupe.core.scanner import group_duplicates, scan


def f(id, md5, size=1):
    return DriveFile(id=id, name=id, size=size, md5=md5, modified_time="")


def test_groups_by_md5_and_skips_uniques():
    result = group_duplicates([f("a", "x"), f("b", "x"), f("c", "y"), f("d", None)])
    assert result.total_files == 4
    assert result.skipped_no_md5 == 1
    assert len(result.groups) == 1
    assert {x.id for x in result.groups[0].files} == {"a", "b"}


def test_groups_sorted_by_wasted_bytes_desc():
    result = group_duplicates([
        f("a", "small", size=1), f("b", "small", size=1),
        f("c", "big", size=100), f("d", "big", size=100),
    ])
    assert [g.md5 for g in result.groups] == ["big", "small"]


class FakeClient:
    def list_owned_files(self, progress_cb=None):
        if progress_cb:
            progress_cb(2)
        return [f("a", "x"), f("b", "x")]


def test_scan_uses_client_and_reports_progress():
    seen = []
    result = scan(FakeClient(), progress_cb=seen.append)
    assert seen == [2]
    assert len(result.groups) == 1
```

- [ ] **Step 2: Run to fail.**

- [ ] **Step 3: Implement** (`gdrive_dedupe/core/scanner.py`)

```python
from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable

from .models import DriveFile, DuplicateGroup, ScanResult


def group_duplicates(files: Iterable[DriveFile]) -> ScanResult:
    by_md5: dict[str, list[DriveFile]] = defaultdict(list)
    total = 0
    skipped = 0
    for file in files:
        total += 1
        if file.md5:
            by_md5[file.md5].append(file)
        else:
            skipped += 1
    groups = [DuplicateGroup(md5=md5, files=group) for md5, group in by_md5.items()
              if len(group) >= 2]
    groups.sort(key=lambda g: g.wasted_bytes, reverse=True)
    return ScanResult(total_files=total, skipped_no_md5=skipped, groups=groups)


def scan(client, progress_cb: Callable[[int], None] | None = None) -> ScanResult:
    return group_duplicates(client.list_owned_files(progress_cb))
```

- [ ] **Step 4: Run to pass**, **Step 5: Commit** (`feat: md5 duplicate grouping and scan orchestration`).

---

### Task 5: Auth — two-tier scopes, JSON token storage

**Files:**
- Create: `gdrive_dedupe/core/auth.py`, `tests/test_auth.py`

**Interfaces:**
- Produces:
  - `SCOPE_READONLY`, `SCOPE_FULL` (exact URLs from Global Constraints)
  - `config_dir() -> Path` — `$GDRIVE_DEDUPE_HOME` or `~/.gdrive-dedupe`, created on demand
  - `token_path() -> Path`, `find_client_secrets() -> Path | None` (checks `./credentials.json` then `<config>/credentials.json`)
  - `save_token(creds) -> None` — `creds.to_json()`, chmod `0600`
  - `load_credentials(write_access: bool = False) -> Credentials | None` — returns None if missing/wrong-scope/invalid; refreshes expired tokens
  - `run_oauth_flow(write_access: bool = False) -> Credentials` — InstalledAppFlow local server; raises `FileNotFoundError` with helpful message if no client secrets
  - `auth_status() -> dict` — `{"connected": bool, "write_access": bool, "has_client_secrets": bool}`
  - `build_service(creds)` — `googleapiclient.discovery.build("drive", "v3", credentials=creds)`
  - `disconnect() -> None` — deletes the token file if present

- [ ] **Step 1: Failing tests** (`tests/test_auth.py`) — use `GDRIVE_DEDUPE_HOME` + fake Credentials:

```python
import json
import stat

import pytest

from gdrive_dedupe.core import auth


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GDRIVE_DEDUPE_HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


class FakeCreds:
    def __init__(self, scopes, valid=True, expired=False, refresh_token=None):
        self.scopes = scopes
        self.valid = valid
        self.expired = expired
        self.refresh_token = refresh_token

    def to_json(self):
        return json.dumps({"scopes": self.scopes})

    def refresh(self, request):
        self.valid = True
        self.expired = False


def test_save_token_sets_0600(home):
    auth.save_token(FakeCreds([auth.SCOPE_READONLY]))
    mode = stat.S_IMODE(auth.token_path().stat().st_mode)
    assert mode == 0o600


def test_load_returns_none_without_token(home):
    assert auth.load_credentials() is None


def test_load_rejects_readonly_token_for_write_access(home, monkeypatch):
    auth.save_token(FakeCreds([auth.SCOPE_READONLY]))
    monkeypatch.setattr(
        auth.Credentials, "from_authorized_user_file",
        staticmethod(lambda path: FakeCreds([auth.SCOPE_READONLY])),
    )
    assert auth.load_credentials(write_access=False) is not None
    assert auth.load_credentials(write_access=True) is None


def test_full_scope_satisfies_readonly(home, monkeypatch):
    auth.save_token(FakeCreds([auth.SCOPE_FULL]))
    monkeypatch.setattr(
        auth.Credentials, "from_authorized_user_file",
        staticmethod(lambda path: FakeCreds([auth.SCOPE_FULL])),
    )
    assert auth.load_credentials(write_access=True) is not None


def test_auth_status_disconnected(home):
    assert auth.auth_status() == {
        "connected": False, "write_access": False, "has_client_secrets": False,
    }


def test_run_oauth_flow_without_secrets_raises(home):
    with pytest.raises(FileNotFoundError):
        auth.run_oauth_flow()


def test_disconnect_removes_token(home):
    auth.save_token(FakeCreds([auth.SCOPE_READONLY]))
    auth.disconnect()
    assert not auth.token_path().exists()
```

- [ ] **Step 2: Run to fail.**

- [ ] **Step 3: Implement** (`gdrive_dedupe/core/auth.py`)

```python
from __future__ import annotations

import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPE_READONLY = "https://www.googleapis.com/auth/drive.metadata.readonly"
SCOPE_FULL = "https://www.googleapis.com/auth/drive"


def config_dir() -> Path:
    path = Path(os.environ.get("GDRIVE_DEDUPE_HOME", str(Path.home() / ".gdrive-dedupe")))
    path.mkdir(parents=True, exist_ok=True)
    return path


def token_path() -> Path:
    return config_dir() / "token.json"


def find_client_secrets() -> Path | None:
    for candidate in (Path("credentials.json"), config_dir() / "credentials.json"):
        if candidate.exists():
            return candidate
    return None


def save_token(creds) -> None:
    path = token_path()
    path.write_text(creds.to_json())
    path.chmod(0o600)


def _scopes_ok(creds, write_access: bool) -> bool:
    scopes = set(creds.scopes or [])
    if write_access:
        return SCOPE_FULL in scopes
    return bool(scopes & {SCOPE_FULL, SCOPE_READONLY})


def load_credentials(write_access: bool = False) -> Credentials | None:
    if not token_path().exists():
        return None
    creds = Credentials.from_authorized_user_file(str(token_path()))
    if not _scopes_ok(creds, write_access):
        return None
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        save_token(creds)
    return creds if creds.valid else None


def run_oauth_flow(write_access: bool = False) -> Credentials:
    secrets = find_client_secrets()
    if secrets is None:
        raise FileNotFoundError(
            "credentials.json not found. Download an OAuth client (Desktop app) from "
            "Google Cloud Console and place it in the project directory or "
            f"{config_dir()}/credentials.json — see README for the walkthrough."
        )
    scope = SCOPE_FULL if write_access else SCOPE_READONLY
    flow = InstalledAppFlow.from_client_secrets_file(str(secrets), [scope])
    creds = flow.run_local_server(port=0)
    save_token(creds)
    return creds


def auth_status() -> dict:
    read = load_credentials(write_access=False)
    write = read is not None and _scopes_ok(read, write_access=True)
    return {
        "connected": read is not None,
        "write_access": write,
        "has_client_secrets": find_client_secrets() is not None,
    }


def build_service(creds):
    return build("drive", "v3", credentials=creds)


def disconnect() -> None:
    token_path().unlink(missing_ok=True)
```

- [ ] **Step 4: Run to pass**, **Step 5: Commit** (`feat: two-tier OAuth with 0600 JSON token storage`).

---

### Task 6: Drive client — pagination, backoff, trash

**Files:**
- Create: `gdrive_dedupe/core/drive.py`, `tests/test_drive.py`

**Interfaces:**
- Consumes: `DriveFile` from Task 2.
- Produces: `DriveClient(service, max_retries=5, sleep=time.sleep)` with:
  - `list_owned_files(progress_cb: Callable[[int], None] | None = None) -> list[DriveFile]` — pages through `q="trashed = false and 'me' in owners"`, resolves folder paths, excludes folders from the result, reports the *actual* running count.
  - `set_trashed(file_id: str, trashed: bool) -> None`
  - Retries HTTP 403/429/500/502/503/504 with exponential backoff + jitter (max `max_retries` attempts), then re-raises.

- [ ] **Step 1: Failing tests** (`tests/test_drive.py`) — fake the googleapiclient service with nested stubs:

```python
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
    item = {"id": id, "name": name, "mimeType": mime, "size": size,
            "md5Checksum": "m", "modifiedTime": "2024-01-01T00:00:00Z"}
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
    pages = [{"files": [
        {"id": "root-folder", "name": "Photos", "mimeType": FOLDER_MIME},
        {"id": "sub", "name": "2024", "mimeType": FOLDER_MIME, "parents": ["root-folder"]},
        drive_file("f1", "pic.jpg", parents=["sub"]),
    ]}]
    files = DriveClient(FakeService(FakeFiles(pages))).list_owned_files()
    assert len(files) == 1
    assert files[0].path == "/Photos/2024"


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


def test_set_trashed_calls_update():
    fake_files = FakeFiles([{}])
    DriveClient(FakeService(fake_files)).set_trashed("f9", True)
    assert fake_files.update_calls == [("f9", {"trashed": True})]
```

- [ ] **Step 2: Run to fail.**

- [ ] **Step 3: Implement** (`gdrive_dedupe/core/drive.py`)

```python
from __future__ import annotations

import random
import time
from collections.abc import Callable

from googleapiclient.errors import HttpError

from .models import DriveFile

FOLDER_MIME = "application/vnd.google-apps.folder"
FILE_FIELDS = "id, name, size, md5Checksum, modifiedTime, mimeType, webViewLink, parents"
RETRY_STATUSES = {403, 429, 500, 502, 503, 504}
OWNED_QUERY = "trashed = false and 'me' in owners"


class DriveClient:
    """Thin wrapper over the Drive v3 service: pagination, backoff, trash."""

    def __init__(self, service, max_retries: int = 5, sleep: Callable[[float], None] = time.sleep):
        self.service = service
        self.max_retries = max_retries
        self._sleep = sleep

    def _execute(self, request):
        for attempt in range(self.max_retries):
            try:
                return request.execute()
            except HttpError as err:
                status = err.resp.status
                if status not in RETRY_STATUSES or attempt == self.max_retries - 1:
                    raise
                self._sleep(min(2**attempt + random.random(), 32))

    def list_owned_files(
        self, progress_cb: Callable[[int], None] | None = None
    ) -> list[DriveFile]:
        raw: list[dict] = []
        page_token = None
        while True:
            response = self._execute(
                self.service.files().list(
                    q=OWNED_QUERY,
                    pageSize=1000,
                    fields=f"nextPageToken, files({FILE_FIELDS})",
                    pageToken=page_token,
                )
            )
            raw.extend(response.get("files", []))
            if progress_cb:
                progress_cb(len(raw))
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        folders = {f["id"]: f for f in raw if f.get("mimeType") == FOLDER_MIME}
        return [
            DriveFile(
                id=f["id"],
                name=f.get("name", ""),
                size=int(f.get("size", 0)),
                md5=f.get("md5Checksum"),
                modified_time=f.get("modifiedTime", ""),
                mime_type=f.get("mimeType", ""),
                web_view_link=f.get("webViewLink", ""),
                path=self._resolve_path(f, folders),
            )
            for f in raw
            if f.get("mimeType") != FOLDER_MIME
        ]

    @staticmethod
    def _resolve_path(item: dict, folders: dict[str, dict]) -> str:
        parts: list[str] = []
        parent = (item.get("parents") or [None])[0]
        depth = 0
        while parent and parent in folders and depth < 50:
            folder = folders[parent]
            parts.append(folder.get("name", ""))
            parent = (folder.get("parents") or [None])[0]
            depth += 1
        return "/" + "/".join(reversed(parts)) if parts else "/"

    def set_trashed(self, file_id: str, trashed: bool) -> None:
        self._execute(self.service.files().update(fileId=file_id, body={"trashed": trashed}))
```

Note on the fake in tests: `test_pagination_and_progress` relies on `nextPageToken` being `"1"` and the fake indexing pages by `int(pageToken)`.

- [ ] **Step 4: Run to pass**, **Step 5: Commit** (`feat: Drive client with pagination, backoff, path resolution`).

---

### Task 7: Actions — audit-logged trash and undo

**Files:**
- Create: `gdrive_dedupe/core/actions.py`, `tests/test_actions.py`

**Interfaces:**
- Consumes: `DriveFile` (Task 2), `config_dir()` (Task 5), `DriveClient.set_trashed` (Task 6 — any object with that method).
- Produces:
  - `audit_path() -> Path` — `<config>/audit.jsonl`
  - `trash_files(client, files: list[DriveFile], progress_cb: Callable[[int, int], None] | None = None) -> str` — writes the audit record FIRST, then trashes each file; returns `batch_id`.
  - `undo_last(client) -> list[dict]` — untrashes the most recent not-yet-undone trash batch, appends an undo record, returns the restored `[{"id", "name"}]`; raises `LookupError` when nothing to undo.

- [ ] **Step 1: Failing tests** (`tests/test_actions.py`)

```python
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
    batch_id = actions.trash_files(client, [f("a"), f("b")],
                                   progress_cb=lambda done, total: progress.append((done, total)))
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
```

- [ ] **Step 2: Run to fail.**

- [ ] **Step 3: Implement** (`gdrive_dedupe/core/actions.py`)

```python
from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from .auth import config_dir
from .models import DriveFile


def audit_path() -> Path:
    return config_dir() / "audit.jsonl"


def _append(record: dict) -> None:
    with audit_path().open("a") as fh:
        fh.write(json.dumps(record) + "\n")


def trash_files(
    client,
    files: list[DriveFile],
    progress_cb: Callable[[int, int], None] | None = None,
) -> str:
    batch_id = uuid.uuid4().hex[:12]
    _append(
        {
            "batch_id": batch_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "trash",
            "files": [{"id": f.id, "name": f.name} for f in files],
        }
    )
    for done, file in enumerate(files, 1):
        client.set_trashed(file.id, True)
        if progress_cb:
            progress_cb(done, len(files))
    return batch_id


def undo_last(client) -> list[dict]:
    if not audit_path().exists():
        raise LookupError("nothing to undo")
    records = [json.loads(line) for line in audit_path().read_text().splitlines()]
    undone = {r["batch_id"] for r in records if r["action"] == "undo"}
    candidates = [r for r in records if r["action"] == "trash" and r["batch_id"] not in undone]
    if not candidates:
        raise LookupError("nothing to undo")
    batch = candidates[-1]
    for file in batch["files"]:
        client.set_trashed(file["id"], False)
    _append(
        {
            "batch_id": batch["batch_id"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": "undo",
            "files": batch["files"],
        }
    )
    return batch["files"]
```

The audit record is written *before* any trash call so a mid-batch crash still leaves enough information to recover; untrashing an already-untrashed file is harmless.

- [ ] **Step 4: Run to pass**, **Step 5: Commit** (`feat: audit-logged trash batches with undo`).

---

### Task 8: Web backend — FastAPI app, scan state, SSE

**Files:**
- Create: `gdrive_dedupe/web/state.py`, `gdrive_dedupe/web/app.py`, `tests/test_web.py`
- Create (placeholder for Task 9): `gdrive_dedupe/web/static/index.html` containing just `<h1>gdrive-dedupe</h1>`

**Interfaces:**
- Consumes: `auth` (Task 5), `scanner.scan` (Task 4), `actions` (Task 7), `DriveClient` (Task 6), `strategies` (Task 3).
- Produces:
  - `state.ScanState` — thread-safe holder: `.status` (`"idle"|"connecting"|"scanning"|"done"|"error"`), `.progress: dict`, `.result: ScanResult | None`, `.error: str`, methods `snapshot() -> dict`, `set_progress(**kw)`, `finish(result)`, `fail(msg)`, `begin(status)` (returns False if already busy).
  - `app.create_app() -> FastAPI` with routes:
    - `GET /` → static index.html
    - `GET /api/auth/status` → `auth.auth_status()`
    - `POST /api/auth/connect` body `{"write_access": bool}` → runs OAuth in a background thread, returns `{"started": true}`
    - `POST /api/auth/disconnect`
    - `POST /api/scan` → starts background scan thread; 409 if busy
    - `GET /api/scan/events` → SSE stream of `snapshot()` until status is `done`/`error`
    - `GET /api/scan/result?strategy=newest` → serialized result with per-file `suggested_trash: bool`
    - `POST /api/clean` body `{"file_ids": [...]}` → 400 unless every id is in the current result AND every group keeps ≥1 file; 403 without write access; returns `{"batch_id", "trashed": n}`
    - `POST /api/undo` → `{"restored": n}`; 404 when nothing to undo
  - Module-level indirection: routes call `auth`, `scanner`, `actions` through module attributes of `gdrive_dedupe.web.app` so tests can monkeypatch them.

- [ ] **Step 1: Failing tests** (`tests/test_web.py`)

```python
import pytest
from fastapi.testclient import TestClient

from gdrive_dedupe.core.models import DriveFile, DuplicateGroup, ScanResult
from gdrive_dedupe.web import app as webapp


def f(id, md5="m", modified="2024-01-01T00:00:00Z"):
    return DriveFile(id=id, name=f"{id}.txt", size=10, md5=md5, modified_time=modified)


RESULT = ScanResult(
    total_files=4, skipped_no_md5=0,
    groups=[DuplicateGroup(md5="m", files=[f("keep", modified="2024-06-01T00:00:00Z"),
                                           f("dupe", modified="2020-01-01T00:00:00Z")])],
)


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("GDRIVE_DEDUPE_HOME", str(tmp_path))
    application = webapp.create_app()
    application.state.scan.finish(RESULT)
    return TestClient(application)


def connected(monkeypatch, write_access):
    monkeypatch.setattr(webapp.auth, "auth_status", lambda: {
        "connected": True, "write_access": write_access, "has_client_secrets": True})
    monkeypatch.setattr(webapp.auth, "load_credentials", lambda write_access=False: object())
    monkeypatch.setattr(webapp.auth, "build_service", lambda creds: object())


def test_auth_status(client):
    body = client.get("/api/auth/status").json()
    assert body["connected"] is False


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
    monkeypatch.setattr(webapp.actions, "trash_files",
                        lambda client_, files, progress_cb=None: "batch42")
    body = client.post("/api/clean", json={"file_ids": ["dupe"]}).json()
    assert body == {"batch_id": "batch42", "trashed": 1}


def test_undo_when_empty_is_404(client, monkeypatch):
    connected(monkeypatch, write_access=True)

    def boom(client_):
        raise LookupError("nothing to undo")

    monkeypatch.setattr(webapp.actions, "undo_last", boom)
    assert client.post("/api/undo").status_code == 404


def test_scan_conflict_while_busy(client):
    client.app.state.scan.begin("scanning")
    assert client.post("/api/scan").status_code == 409


def test_serves_index(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
```

- [ ] **Step 2: Run to fail.**

- [ ] **Step 3: Implement `gdrive_dedupe/web/state.py`**

```python
from __future__ import annotations

import threading
from dataclasses import dataclass, field

from gdrive_dedupe.core.models import ScanResult

BUSY_STATUSES = {"connecting", "scanning"}


@dataclass
class ScanState:
    status: str = "idle"
    progress: dict = field(default_factory=dict)
    result: ScanResult | None = None
    error: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def begin(self, status: str) -> bool:
        with self._lock:
            if self.status in BUSY_STATUSES:
                return False
            self.status = status
            self.progress = {}
            self.error = ""
            return True

    def set_progress(self, **kwargs) -> None:
        with self._lock:
            self.progress.update(kwargs)

    def finish(self, result: ScanResult | None) -> None:
        with self._lock:
            self.status = "done"
            self.result = result

    def fail(self, message: str) -> None:
        with self._lock:
            self.status = "error"
            self.error = message

    def snapshot(self) -> dict:
        with self._lock:
            return {"status": self.status, "progress": dict(self.progress), "error": self.error}
```

- [ ] **Step 4: Implement `gdrive_dedupe/web/app.py`**

```python
from __future__ import annotations

import asyncio
import json
import threading
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
                    {
                        "id": f.id,
                        "name": f.name,
                        "path": f.path,
                        "size": f.size,
                        "modified_time": f.modified_time,
                        "web_view_link": f.web_view_link,
                        "suggested_trash": f.id != keeper.id,
                    }
                    for f in group.files
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
            except Exception as exc:  # surfaces in /api/scan/events and status polling
                state.fail(str(exc))

        threading.Thread(target=work, daemon=True).start()
        return {"started": True}

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
```

Also create `gdrive_dedupe/web/static/index.html` with placeholder `<h1>gdrive-dedupe</h1>` (replaced in Task 9).

- [ ] **Step 5: Run to pass**: `uv run pytest tests/test_web.py -q`, then full suite `uv run pytest -q` and `uv run ruff check .`.

- [ ] **Step 6: Commit** (`feat: FastAPI backend with SSE progress, guarded clean/undo endpoints`).

---

### Task 9: Frontend — single-page UI

**Files:**
- Create/replace: `gdrive_dedupe/web/static/index.html`, `gdrive_dedupe/web/static/app.js`, `gdrive_dedupe/web/static/style.css`

**Interfaces:**
- Consumes the Task 8 API exactly as specified (`/api/auth/status`, `/api/auth/connect`, `/api/scan`, `/api/scan/events`, `/api/scan/result`, `/api/clean`, `/api/undo`).

Design intent: clean, calm, trustworthy utility. Dark slate background, single accent color, generous spacing, system font stack. Four stages shown as a stepper: Connect → Scan → Review → Clean. No frameworks.

- [ ] **Step 1: Write `index.html`**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Drive Dedupe</title>
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
<header>
  <h1>Drive Dedupe</h1>
  <p class="tagline">Find and safely remove duplicate files in your Google Drive</p>
</header>
<main>
  <!-- Stage: Connect -->
  <section id="stage-connect" class="card">
    <h2>1 · Connect</h2>
    <p id="auth-info">Checking connection…</p>
    <p class="hint" id="secrets-hint" hidden>
      Place your OAuth <code>credentials.json</code> next to the app (see README) and reload.
    </p>
    <div class="row">
      <button id="btn-connect">Connect Google Drive (read-only)</button>
      <button id="btn-disconnect" class="ghost" hidden>Disconnect</button>
    </div>
    <p class="hint">Scanning uses a read-only scope — the app cannot modify anything.</p>
  </section>

  <!-- Stage: Scan -->
  <section id="stage-scan" class="card" hidden>
    <h2>2 · Scan</h2>
    <div class="row">
      <button id="btn-scan">Scan My Drive</button>
      <span id="scan-progress" class="progress"></span>
    </div>
  </section>

  <!-- Stage: Review -->
  <section id="stage-review" class="card" hidden>
    <h2>3 · Review duplicates</h2>
    <div id="summary" class="stats"></div>
    <div class="row">
      <label for="strategy">Pre-select copies to remove, keeping the</label>
      <select id="strategy">
        <option value="newest" selected>newest file</option>
        <option value="oldest">oldest file</option>
      </select>
    </div>
    <div id="groups"></div>
  </section>

  <!-- Stage: Clean -->
  <section id="stage-clean" class="card" hidden>
    <h2>4 · Clean</h2>
    <p id="clean-summary"></p>
    <p class="hint">Files are moved to Drive's trash (recoverable for 30 days). An audit log
      is kept locally, and you can undo the last batch here.</p>
    <div class="row">
      <button id="btn-clean" class="danger">Move selected to trash…</button>
      <button id="btn-undo" class="ghost" hidden>Undo last batch</button>
    </div>
    <p id="clean-result"></p>
  </section>
</main>

<dialog id="confirm-dialog">
  <h3>Confirm: move these files to trash?</h3>
  <ul id="confirm-list"></ul>
  <p id="confirm-count"></p>
  <div class="row">
    <button id="confirm-yes" class="danger">Move to trash</button>
    <button id="confirm-no" class="ghost">Cancel</button>
  </div>
</dialog>

<script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Write `app.js`**

```javascript
const $ = (sel) => document.querySelector(sel);
let currentResult = null;

async function api(path, options = {}) {
  const resp = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.detail || `${resp.status} ${resp.statusText}`);
  }
  return resp.json();
}

function show(id, visible) {
  $(id).hidden = !visible;
}

async function refreshAuth() {
  const status = await api("/api/auth/status");
  const connected = status.connected;
  $("#auth-info").textContent = connected
    ? `Connected${status.write_access ? " (write access granted)" : " (read-only)"}`
    : "Not connected.";
  show("#secrets-hint", !connected && !status.has_client_secrets);
  $("#btn-connect").hidden = connected;
  $("#btn-disconnect").hidden = !connected;
  show("#stage-scan", connected);
  return status;
}

async function connect(writeAccess) {
  await api("/api/auth/connect", {
    method: "POST",
    body: JSON.stringify({ write_access: writeAccess }),
  });
  $("#auth-info").textContent = "A Google consent tab should open — approve access there…";
  const poll = setInterval(async () => {
    const status = await refreshAuth();
    if (writeAccess ? status.write_access : status.connected) clearInterval(poll);
  }, 1500);
}

function watchScan() {
  const source = new EventSource("/api/scan/events");
  source.onmessage = async (event) => {
    const snap = JSON.parse(event.data);
    if (snap.status === "scanning") {
      const n = snap.progress.files_fetched || 0;
      $("#scan-progress").textContent = `Fetched metadata for ${n} files…`;
    } else if (snap.status === "done") {
      source.close();
      $("#scan-progress").textContent = "";
      await loadResult();
    } else if (snap.status === "error") {
      source.close();
      $("#scan-progress").textContent = `Error: ${snap.error}`;
    }
  };
}

async function startScan() {
  $("#scan-progress").textContent = "Starting…";
  await api("/api/scan", { method: "POST" });
  watchScan();
}

async function loadResult() {
  const strategy = $("#strategy").value;
  currentResult = await api(`/api/scan/result?strategy=${strategy}`);
  renderResult();
}

function renderResult() {
  const r = currentResult;
  show("#stage-review", true);
  show("#stage-clean", r.groups.length > 0);
  $("#summary").innerHTML = `
    <div><b>${r.total_files}</b> files scanned</div>
    <div><b>${r.duplicate_groups}</b> duplicate groups</div>
    <div><b>${r.reclaimable_human}</b> reclaimable</div>
    <div><b>${r.skipped_no_md5}</b> skipped (Google Docs formats)</div>`;
  const container = $("#groups");
  container.innerHTML = "";
  if (!r.groups.length) {
    container.innerHTML = "<p>No duplicates found 🎉</p>";
    return;
  }
  for (const group of r.groups) {
    const details = document.createElement("details");
    details.open = r.groups.length <= 8;
    details.innerHTML = `
      <summary>
        <span class="gname">${escapeHtml(group.files[0].name)}</span>
        <span class="gmeta">${group.files.length} copies · ${group.size_human} each ·
          wastes ${group.wasted_human}</span>
      </summary>`;
    const table = document.createElement("table");
    table.innerHTML = "<tr><th></th><th>Name</th><th>Folder</th><th>Modified</th><th></th></tr>";
    for (const file of group.files) {
      const row = document.createElement("tr");
      row.innerHTML = `
        <td><input type="checkbox" data-id="${file.id}"
             ${file.suggested_trash ? "checked" : ""}></td>
        <td>${escapeHtml(file.name)}</td>
        <td class="path">${escapeHtml(file.path)}</td>
        <td>${(file.modified_time || "").slice(0, 10)}</td>
        <td>${file.web_view_link
          ? `<a href="${file.web_view_link}" target="_blank" rel="noopener">view</a>` : ""}</td>`;
      row.className = file.suggested_trash ? "trash" : "keep";
      table.appendChild(row);
    }
    details.appendChild(table);
    container.appendChild(details);
  }
  updateCleanSummary();
  container.addEventListener("change", updateCleanSummary);
}

function selectedIds() {
  return [...document.querySelectorAll('#groups input[type="checkbox"]:checked')]
    .map((el) => el.dataset.id);
}

function updateCleanSummary() {
  document.querySelectorAll("#groups tr").forEach((row) => {
    const box = row.querySelector("input");
    if (box) row.className = box.checked ? "trash" : "keep";
  });
  const n = selectedIds().length;
  $("#clean-summary").textContent = n
    ? `${n} file(s) selected for trash.`
    : "Nothing selected — tick the copies you want to remove.";
  $("#btn-clean").disabled = n === 0;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text ?? "";
  return div.innerHTML;
}

async function confirmClean() {
  const ids = selectedIds();
  const names = [];
  for (const group of currentResult.groups) {
    for (const file of group.files) {
      if (ids.includes(file.id)) names.push(`${file.name} (${file.path})`);
    }
  }
  $("#confirm-list").innerHTML = names.slice(0, 15)
    .map((n) => `<li>${escapeHtml(n)}</li>`).join("");
  $("#confirm-count").textContent = names.length > 15
    ? `…and ${names.length - 15} more.` : `${names.length} file(s) total.`;
  $("#confirm-dialog").showModal();
}

async function runClean() {
  $("#confirm-dialog").close();
  const status = await api("/api/auth/status");
  if (!status.write_access) {
    $("#clean-result").textContent =
      "Requesting write access — approve the Google consent tab, then click Clean again.";
    await connect(true);
    return;
  }
  try {
    const body = await api("/api/clean", {
      method: "POST",
      body: JSON.stringify({ file_ids: selectedIds() }),
    });
    $("#clean-result").textContent =
      `Moved ${body.trashed} file(s) to trash (batch ${body.batch_id}).`;
    $("#btn-undo").hidden = false;
  } catch (err) {
    $("#clean-result").textContent = `Error: ${err.message}`;
  }
}

async function undo() {
  try {
    const body = await api("/api/undo", { method: "POST" });
    $("#clean-result").textContent = `Restored ${body.restored} file(s) from trash.`;
    $("#btn-undo").hidden = true;
  } catch (err) {
    $("#clean-result").textContent = `Error: ${err.message}`;
  }
}

$("#btn-connect").addEventListener("click", () => connect(false));
$("#btn-disconnect").addEventListener("click", async () => {
  await api("/api/auth/disconnect", { method: "POST" });
  refreshAuth();
});
$("#btn-scan").addEventListener("click", startScan);
$("#strategy").addEventListener("change", loadResult);
$("#btn-clean").addEventListener("click", confirmClean);
$("#confirm-yes").addEventListener("click", runClean);
$("#confirm-no").addEventListener("click", () => $("#confirm-dialog").close());
$("#btn-undo").addEventListener("click", undo);

refreshAuth();
```

- [ ] **Step 3: Write `style.css`**

```css
:root {
  --bg: #0f172a;
  --card: #1e293b;
  --border: #334155;
  --text: #e2e8f0;
  --muted: #94a3b8;
  --accent: #38bdf8;
  --danger: #f87171;
  --keep: #34d399;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  line-height: 1.55;
}
header { max-width: 880px; margin: 2.5rem auto 1rem; padding: 0 1rem; }
header h1 { margin: 0; font-size: 1.9rem; letter-spacing: -0.02em; }
.tagline { color: var(--muted); margin-top: 0.25rem; }
main { max-width: 880px; margin: 0 auto 4rem; padding: 0 1rem; display: grid; gap: 1rem; }
.card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1.25rem 1.5rem;
}
.card h2 { margin: 0 0 0.75rem; font-size: 1.05rem; color: var(--accent); }
.row { display: flex; gap: 0.75rem; align-items: center; flex-wrap: wrap; margin: 0.5rem 0; }
button {
  background: var(--accent);
  color: #082f49;
  border: none;
  border-radius: 8px;
  padding: 0.55rem 1.1rem;
  font-weight: 600;
  font-size: 0.95rem;
  cursor: pointer;
}
button:disabled { opacity: 0.4; cursor: not-allowed; }
button.ghost { background: transparent; color: var(--muted); border: 1px solid var(--border); }
button.danger { background: var(--danger); color: #450a0a; }
.hint { color: var(--muted); font-size: 0.85rem; }
.progress { color: var(--accent); font-variant-numeric: tabular-nums; }
.stats { display: flex; gap: 1.5rem; flex-wrap: wrap; margin-bottom: 0.75rem; color: var(--muted); }
.stats b { color: var(--text); font-size: 1.15rem; }
select {
  background: var(--bg); color: var(--text); border: 1px solid var(--border);
  border-radius: 8px; padding: 0.4rem 0.6rem;
}
details { border-top: 1px solid var(--border); padding: 0.6rem 0; }
summary { cursor: pointer; display: flex; gap: 1rem; align-items: baseline; }
.gname { font-weight: 600; }
.gmeta { color: var(--muted); font-size: 0.85rem; }
table { width: 100%; border-collapse: collapse; margin-top: 0.5rem; font-size: 0.9rem; }
th { text-align: left; color: var(--muted); font-weight: 500; padding: 0.3rem 0.5rem; }
td { padding: 0.3rem 0.5rem; border-top: 1px solid var(--border); }
tr.trash td { color: var(--danger); }
tr.keep td { color: var(--keep); }
td.path { color: var(--muted); }
a { color: var(--accent); }
dialog {
  background: var(--card); color: var(--text); border: 1px solid var(--border);
  border-radius: 12px; padding: 1.5rem; max-width: 480px;
}
dialog::backdrop { background: rgb(0 0 0 / 0.6); }
dialog ul { max-height: 40vh; overflow-y: auto; padding-left: 1.2rem; }
```

- [ ] **Step 4: Verify** — `uv run pytest -q` still green (`test_serves_index` now serves the real page). Manual check happens in Task 13 with the running server.

- [ ] **Step 5: Commit** (`feat: single-page web UI with review table and confirm dialog`).

---

### Task 10: CLI

**Files:**
- Create: `gdrive_dedupe/cli.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: everything from core; `create_app` from Task 8.
- Produces Typer app `app` (entry point `gdrive-dedupe`) with commands:
  - `scan [--json PATH]` — connects read-only (runs OAuth if needed), scans with progress line, prints group table + summary; `--json` writes serialized result.
  - `clean [--strategy newest|oldest] [--apply]` — dry-run by default (prints the plan and exits); with `--apply` asks for confirmation then trashes.
  - `undo` — undoes the last batch.
  - `web [--port 8765] [--no-browser]` — serves on `127.0.0.1`, opens browser.

- [ ] **Step 1: Failing tests** (`tests/test_cli.py`)

```python
import pytest
from typer.testing import CliRunner

from gdrive_dedupe import cli
from gdrive_dedupe.core.models import DriveFile, DuplicateGroup, ScanResult

runner = CliRunner()


def result_fixture():
    files = [
        DriveFile(id="new", name="report.pdf", size=100, md5="m",
                  modified_time="2024-06-01T00:00:00Z", path="/Docs"),
        DriveFile(id="old", name="report copy.pdf", size=100, md5="m",
                  modified_time="2020-06-01T00:00:00Z", path="/Backup"),
    ]
    return ScanResult(total_files=10, skipped_no_md5=1,
                      groups=[DuplicateGroup(md5="m", files=files)])


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
    monkeypatch.setattr(cli.actions, "trash_files",
                        lambda client, files, progress_cb=None: trashed.extend(files) or "b1")
    result = runner.invoke(cli.app, ["clean"])
    assert result.exit_code == 0
    assert "dry run" in result.output.lower()
    assert trashed == []


def test_clean_apply_trashes_after_confirm(monkeypatch):
    trashed = []
    monkeypatch.setattr(cli.actions, "trash_files",
                        lambda client, files, progress_cb=None: trashed.extend(files) or "b1")
    result = runner.invoke(cli.app, ["clean", "--apply"], input="y\n")
    assert result.exit_code == 0
    assert [f.id for f in trashed] == ["old"]


def test_clean_apply_aborts_on_no(monkeypatch):
    trashed = []
    monkeypatch.setattr(cli.actions, "trash_files",
                        lambda client, files, progress_cb=None: trashed.extend(files) or "b1")
    result = runner.invoke(cli.app, ["clean", "--apply"], input="n\n")
    assert trashed == []


def test_undo(monkeypatch):
    monkeypatch.setattr(cli.actions, "undo_last",
                        lambda client: [{"id": "old", "name": "report copy.pdf"}])
    result = runner.invoke(cli.app, ["undo"])
    assert result.exit_code == 0
    assert "1 file" in result.output
```

- [ ] **Step 2: Run to fail.**

- [ ] **Step 3: Implement** (`gdrive_dedupe/cli.py`)

```python
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

app = typer.Typer(help="Find and safely remove duplicate files in Google Drive.",
                  no_args_is_help=True)


def _connect(write_access: bool = False) -> DriveClient:
    creds = auth.load_credentials(write_access=write_access)
    if creds is None:
        typer.echo("Opening Google consent screen in your browser…")
        creds = auth.run_oauth_flow(write_access=write_access)
    return DriveClient(auth.build_service(creds))


def _print_result(result: ScanResult) -> None:
    for group in result.groups:
        first = group.files[0]
        typer.echo(f"\n{first.name} — {len(group.files)} copies, "
                   f"{human_bytes(group.size)} each, wastes {human_bytes(group.wasted_bytes)}")
        for file in group.files:
            typer.echo(f"  - {file.name}  ({file.path})  modified {file.modified_time[:10]}")
    plural = "s" if len(result.groups) != 1 else ""
    typer.echo(f"\n{result.total_files} files scanned · "
               f"{len(result.groups)} duplicate group{plural} · "
               f"{human_bytes(result.reclaimable_bytes)} reclaimable · "
               f"{result.skipped_no_md5} skipped (no checksum)")


def _run_scan(client: DriveClient) -> ScanResult:
    return scanner.scan(
        client, progress_cb=lambda n: typer.echo(f"  fetched {n} files…", err=True)
    )


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
    typer.echo(f"\nPlan: trash {len(plan)} file(s), reclaiming {human_bytes(total)} "
               f"(keeping the {strategy} copy of each group).")
    if not apply:
        typer.echo("Dry run — nothing was changed. Re-run with --apply to move files to trash.")
        raise typer.Exit()
    typer.confirm("Move these files to Drive's trash?", abort=True)
    batch_id = actions.trash_files(
        client, plan,
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
```

- [ ] **Step 4: Run to pass**: `uv run pytest -q && uv run ruff check .`

- [ ] **Step 5: Commit** (`feat: Typer CLI with dry-run clean, undo, and web launcher`).

---

### Task 11: Documentation

**Files:**
- Rewrite: `README.md`
- Create: `SECURITY.md`, `CHANGELOG.md`, `docs/architecture.md`
- Delete: nothing yet (legacy files removed in Task 13)

- [ ] **Step 1: Rewrite `README.md`** with these sections (write full prose, not stubs):
  - Title + one-paragraph pitch; badges row (CI badge referencing `.github/workflows/ci.yml`, Python ≥3.11, MIT).
  - **Features** — MD5-exact detection, group review, web UI + CLI, read-only-by-default scopes, dry-run default, trash-only with undo, audit log, no data leaves your machine.
  - **Safety model** — summarize the six guarantees from `SECURITY.md`, link to it.
  - **Quick start** — `uv tool install gdrive-dedupe` / `uv sync` from source; Google Cloud setup walkthrough (create project → enable Drive API → OAuth consent screen (External, test user) → create OAuth client "Desktop app" → download `credentials.json`); then `gdrive-dedupe web`.
  - **Web UI** — the four stages with a screenshot placeholder (`docs/img/` path, note to add screenshots).
  - **CLI usage** — real command examples for `scan`, `scan --json`, `clean` (dry run), `clean --apply`, `undo`, `web --port`.
  - **How duplicates are detected** — MD5 from Drive metadata, Google-native docs skipped, only files you own.
  - **FAQ** — why re-consent for cleanup, where the token/audit live, shared drives unsupported, restore-from-trash window.
  - **Revoking access / cleanup** — carry over the v1 revocation instructions (myaccount.google.com/permissions, delete OAuth client, delete token).
  - **Development** — `uv sync`, `uv run pytest`, `uv run ruff check .`, project layout pointer to `docs/architecture.md`.
  - License + disclaimer paragraph (files go to trash, user is responsible).

- [ ] **Step 2: Write `SECURITY.md`** covering: scopes used and exactly when each is requested; token stored at `~/.gdrive-dedupe/token.json` mode 0600 as JSON (never pickle); server binds 127.0.0.1; no telemetry / no third-party servers; trash-only mutations gated behind explicit confirmation; audit log location and format; how to revoke access; how to report a vulnerability (GitHub issues).

- [ ] **Step 3: Write `CHANGELOG.md`** (Keep a Changelog format): `## [2.0.0]` — full rewrite entries (web UI, CLI, safety model, tests, CI) and `## [1.0.0]` — original script, with the v1.0.0 GitHub release link.

- [ ] **Step 4: Write `docs/architecture.md`** — module map (the File Structure diagram above), data flow (auth → DriveClient → scanner → groups → strategies → actions), the audit-log-first invariant, and how web/CLI share the core.

- [ ] **Step 5: Commit** (`docs: rewrite README, add SECURITY, CHANGELOG, architecture doc`).

---

### Task 12: CI

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Write workflow**

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: uv sync
      - run: uv run ruff check .
      - run: uv run ruff format --check .
      - run: uv run pytest -q
```

- [ ] **Step 2: Run `uv run ruff format .` locally** and fix any reformatting so the `--check` gate passes; run full suite once more.

- [ ] **Step 3: Commit** (`ci: lint, format check, tests on 3.11–3.13`).

---

### Task 13: Legacy cleanup, end-to-end verification, release

**Files:**
- Delete: `duplicate_scanner.py`, `requirements.txt`

- [ ] **Step 1: Remove legacy files**: `git rm duplicate_scanner.py requirements.txt` (v1 remains available at tag v1.0.0).

- [ ] **Step 2: End-to-end verification (no Google account needed)** — start the server with a stubbed core: run `uv run gdrive-dedupe web --no-browser --port 8765` and verify with the webapp-testing skill (or curl + browser) that: the page loads, `/api/auth/status` returns JSON, the Connect stage renders, and (with a monkeypatched fake result injected via a small scratch script that calls `create_app()` and `state.finish(...)`) the review table renders groups and the confirm dialog opens. Fix anything broken.

- [ ] **Step 3: Full gate**: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .` → all green.

- [ ] **Step 4: Commit** (`chore: remove legacy v1 script`).

- [ ] **Step 5: Push and release**

```bash
git push origin main
git tag -a v2.0.0 -m "v2.0: web UI, CLI, safety-first rewrite"
git push origin v2.0.0
gh release create v2.0.0 --title "v2.0.0 — Complete rewrite" --notes "<summarize CHANGELOG 2.0.0 section>"
```

- [ ] **Step 6: Verify CI is green** on GitHub Actions for the pushed commit (`gh run watch` or `gh run list --limit 1`).
