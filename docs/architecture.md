# Architecture

## Module map

```
gdrive_dedupe/
├── core/                  # engine — no UI concerns
│   ├── models.py          # DriveFile, DuplicateGroup, ScanResult, human_bytes
│   ├── auth.py            # OAuth flow, token storage, two-tier scopes
│   ├── drive.py           # DriveClient: pagination, backoff, path resolution, trash
│   ├── scanner.py         # group_duplicates (pure) + scan (client → result)
│   ├── strategies.py      # keep-newest / keep-oldest selection
│   └── actions.py         # audit-logged trash batches + undo
├── web/
│   ├── state.py           # ScanState: thread-safe background-job state
│   ├── app.py             # FastAPI factory, REST + SSE endpoints, serialization
│   └── static/            # index.html, app.js, style.css (no build step)
└── cli.py                 # Typer commands: scan, clean, undo, web
```

Both frontends (web, CLI) are thin layers over `core`; neither contains
duplicate-detection or mutation logic.

## Data flow

```
credentials.json ──▶ auth.run_oauth_flow ──▶ token.json (0600)
                                 │
                                 ▼
                    auth.build_service ──▶ DriveClient
                                 │
                    scanner.scan(client)          [read-only scope]
                                 │  list_owned_files: pagination + backoff,
                                 │  folder-path resolution, ownership filter
                                 ▼
                    ScanResult{groups sorted by wasted bytes}
                                 │
                    strategies.select_keeper      [pure, per group]
                                 │
                    user review + confirmation    [web table / CLI prompt]
                                 │
                    actions.trash_files(client)   [full scope, audit log FIRST]
                                 │
                    actions.undo_last(client)     [optional revert]
```

## Key invariants

- **Audit-first:** `actions.trash_files` appends the batch record to
  `audit.jsonl` before the first API mutation. A crash mid-batch cannot lose
  the recovery information.
- **Trash-only:** the only mutation the codebase performs is
  `files().update(trashed=…)`. There is no call to `files().delete()`.
- **Scope tiers:** `auth.load_credentials(write_access=True)` returns `None`
  unless the stored token actually carries the full scope, forcing an explicit
  re-consent for cleanup.
- **Keeper guarantee:** the web API rejects requests that would trash every
  copy in a group; the CLI derives its plan from `files_to_trash`, which always
  excludes the keeper.

## Web concurrency model

One `ScanState` per app instance (single-user tool). Scans and OAuth flows run
in daemon threads; `ScanState.begin()` rejects overlapping jobs. Progress is
polled by the browser over Server-Sent Events (`/api/scan/events`).

## Testing approach

- Pure logic (grouping, strategies, models) tested directly.
- `DriveClient` tested against stub request/service objects — including retry
  and pagination behavior — with injected `sleep` so tests are instant.
- Web endpoints tested with FastAPI's `TestClient` and monkeypatched core.
- CLI tested with Typer's `CliRunner`.
- Nothing touches the network or a real Google account; `GDRIVE_DEDUPE_HOME`
  redirects all state into pytest temp dirs.
