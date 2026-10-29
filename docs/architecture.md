# Architecture

## Module map

```
gdrive_dedupe/
├── core/                  # engine — no UI concerns
│   ├── models.py          # DriveFile, DuplicateGroup, ScanResult, human_bytes
│   ├── auth.py            # OAuth flow (own local redirect server), token storage, two-tier scopes
│   ├── drive.py           # DriveClient: pagination, backoff, path resolution, trash, purge calls
│   ├── scanner.py         # group_duplicates, scan, scope_result, find_empty_folders,
│   │                      #   find_old_files, find_largest_files, resolve_folder
│   ├── strategies.py      # keeper selection: newest/oldest/shallowest, prefer_folder, protected
│   └── actions.py         # audit-logged trash batches, undo, list_batches, purge_batch
├── web/
│   ├── state.py           # ScanState: thread-safe background-job state
│   ├── app.py             # FastAPI factory, REST + SSE endpoints, host/origin/token guards
│   └── static/            # index.html, app.js, style.css (no build step, no CDN assets)
└── cli.py                 # Typer commands: scan, clean, empty-folders, old-files,
                           #   largest-files, purge, undo, web
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
                    scanner.scope_result          [optional: one folder subtree, regrouped]
                                 │
                    strategies.files_to_trash     [pure, per group: strategy, prefer, protected]
                                 │
                    user review + confirmation    [web table / CLI prompt]
                                 │
                    actions.trash_files(client)   [full scope, audit log FIRST]
                                 │
                    actions.undo_last(client)     [optional revert]
                                 │
                    actions.purge_batch(client)   [optional, irreversible: typed batch id,
                                                   re-checks each file is still trashed]
```

Folder scoping and the three tools (empty folders, old files, largest files) are pure
functions over the `ScanResult` already in memory — no extra API calls. Keep rules work
on `DriveFile.path` (the containing folder's path), so "inside folder X" means the path
equals X or starts with `X/`.

## Key invariants

- **Audit-first:** `actions.trash_files` appends the batch record to
  `audit.jsonl` before the first API mutation. A crash mid-batch cannot lose
  the recovery information.
- **Trash-only, with one named exception:** cleaning performs only
  `files().update(trashed=…)`. `files().delete()` exists in exactly one call site,
  `DriveClient.delete_file`, used only by `actions.purge_batch`, which deletes only
  files from a batch in the audit log, only after `is_trashed` confirms each one is
  still in the trash, and only after the user typed the batch id back.
- **Protected folders are server-enforced:** `/api/clean` and `/api/trash` reject ids
  inside a protected folder; the CLI builds its plan with `files_to_trash`, which never
  includes them.
- **Scope tiers:** `auth.load_credentials(write_access=True)` returns `None`
  unless the stored token actually carries the full scope, forcing an explicit
  re-consent for cleanup.
- **Keeper guarantee:** the web API rejects requests that would trash every
  copy in a group; the CLI derives its plan from `files_to_trash`, which always
  excludes the keeper.

## Web request guards

`create_app` installs three layers, outermost first: `TrustedHostMiddleware`
(loopback plus `--host` / `GDRIVE_DEDUPE_ALLOWED_HOSTS`; defeats DNS rebinding), an
exact-origin check (`Origin` must equal the request's own `scheme://host:port`; defeats
CSRF from any other origin, including other local ports), and — only when a token is
configured — an `X-Dedupe-Token` check on `/api/*`. The CLI configures a token whenever
the bind address is not loopback. The page adopts the token from `?token=` in the launch
URL, keeps it in `sessionStorage`, and strips it from the address bar.

## OAuth in containers

`auth._run_local_server` is a small replacement for `InstalledAppFlow.run_local_server`
so the redirect port, bind address and browser opening are configurable
(`GDRIVE_DEDUPE_OAUTH_PORT`, `GDRIVE_DEDUPE_OAUTH_BIND`, `GDRIVE_DEDUPE_NO_BROWSER`), and
so the consent URL is exposed through `auth_status()["auth_url"]` while a flow is waiting.
The page shows it as a link when no browser could be opened for the user.

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
- The OAuth redirect server is tested for real: a test starts the flow, reads the
  consent URL from `auth_status()`, and requests the redirect URL the way Google's
  redirect would, against a fake `Flow`.
- Nothing touches the network or a real Google account; `GDRIVE_DEDUPE_HOME`
  redirects all state into pytest temp dirs.
- The Docker image is built in CI and probed over HTTP by `scripts/docker-smoke.sh`
  (token gating, trusted hosts, health probe, non-root user, CLI inside the image).
- The browser UI has no unit tests; it is exercised end to end with Playwright against
  the real server backed by a fake Drive before releases: every step and tool, both
  dialogs, the first-run setup state, dark mode, a 375px viewport with no horizontal
  overflow, and keyboard focus. The demo screenshots in `docs/img` come from that run. The two Drive calls used only by purge
  (`files.get` with `fields=trashed`, `files.delete`) are stubbed in tests and should be
  tried once against a throwaway file on a real account before a release that adds
  them.
