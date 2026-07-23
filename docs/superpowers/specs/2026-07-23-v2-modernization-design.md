# Google Drive Duplicate Finder v2.0 — Modernization Design

**Date:** 2026-07-23
**Status:** Approved
**Baseline:** v1.0.0 (single-script CLI, tagged and released before this work)

## Goal

Rewrite the single-script duplicate finder as a professional, safe, well-documented
Python package with a local web interface and a proper CLI, sharing one core engine.

## Decisions (user-approved)

- **Deployment:** local personal tool; web UI served at localhost. Credentials never leave the machine.
- **Backend:** FastAPI.
- **Frontend:** lightweight single-page vanilla JS/HTML served by FastAPI. No build step, no npm.
- **CLI:** kept alongside the web UI (Typer), sharing the same core engine.
- **Versioning:** released as v2.0.0 when complete.

## Architecture

```
gdrive_dedupe/
├── core/
│   ├── auth.py        # OAuth flow, token storage (JSON, 0600), two-tier scopes
│   ├── drive.py       # Drive API client wrapper: pagination, backoff, batching
│   ├── scanner.py     # fetch files, group duplicates by MD5, compute stats
│   ├── models.py      # dataclasses: DriveFile, DuplicateGroup, ScanResult, actions
│   ├── actions.py     # trash / untrash (undo), audit log (JSONL)
│   └── strategies.py  # keep-strategy selection: newest, oldest, shortest-path
├── web/
│   ├── app.py         # FastAPI app factory, routes, SSE progress stream
│   ├── state.py       # in-process scan job state (single-user, one scan at a time)
│   └── static/        # index.html, app.js, style.css
└── cli.py             # Typer CLI: scan, clean, undo, web
```

Packaging: `pyproject.toml` (uv-managed), `ruff` lint+format, `pytest`, GitHub Actions CI.
Console entry point `gdrive-dedupe` with subcommands `web`, `scan`, `clean`, `undo`.

## Safety model

1. **Two-tier OAuth scopes.** Default scope is `drive.metadata.readonly` — scanning
   cannot modify anything. Enabling cleanup (web toggle or `clean`/`undo` commands)
   triggers re-consent with the full `drive` scope, clearly explained to the user.
   The token file records which scopes it holds; scope upgrade invalidates and re-runs the flow.
2. **Token storage.** `Credentials.to_json()` written to `token.json` with `0600`
   permissions. Never pickle. `credentials.json` and `token.json` stay gitignored.
3. **Dry-run by default.** CLI `clean` requires `--apply` to act; without it, prints the
   plan. Web UI always shows a confirmation dialog listing exactly which files will be
   trashed before acting.
4. **Trash only, never permanent delete.** Every trash batch is recorded in an audit
   log (`~/.gdrive-dedupe/audit.jsonl`) with timestamp, file IDs, names, and batch ID.
   **Undo** (web button / `undo` command) untrashes the most recent batch using the log.
5. **Ownership filter.** Only files with `ownedByMe = true` are scanned. Shared files
   are never candidates.
6. **Group-based detection.** Files grouped as full sets by `md5Checksum` (not pairwise).
   Google-native files (Docs/Sheets/Slides — no MD5) are skipped and counted in a
   "skipped" stat shown to the user. Zero-byte files are grouped but flagged.

## Duplicate review model

Each `DuplicateGroup` holds all copies plus metadata per file: name, size,
modifiedTime, folder path (resolved via parents), webViewLink, mimeType.
Keep strategies pre-select which files to keep: **newest** (default), **oldest**,
or manual. The user can override any selection per file. Group stats: wasted bytes
(size × (copies − 1)); scan stats: total files, duplicate groups, reclaimable space.

## Web UI flow

Single page, four stages:

1. **Connect** — shows auth status; button starts OAuth (browser consent).
2. **Scan** — start button; live progress via Server-Sent Events (files fetched,
   groups found so far, reclaimable space counter).
3. **Review** — table of duplicate groups: expandable rows, per-file checkboxes
   (pre-checked by keep strategy), file links to Drive, sort by wasted space.
   Strategy selector re-applies pre-selection.
4. **Clean** — confirmation modal listing files to be trashed → progress → result
   summary with **Undo** button.

API: `POST /api/scan`, `GET /api/scan/events` (SSE), `GET /api/scan/result`,
`POST /api/clean`, `POST /api/undo`, `GET /api/auth/status`, `POST /api/auth/connect`,
`POST /api/auth/upgrade`. Server binds to `127.0.0.1` only.

## Robustness

- Exponential backoff with jitter on HTTP 403 (rate limit), 429, and 5xx.
- Correct pagination progress (report actual counts, not multiples of 1000).
- Trash/untrash executed per-file with backoff (batch HTTP endpoint avoided for
  simplicity; sequential with progress is fine at personal-account scale).
- Scan runs in a background thread; the web process stays responsive.

## Testing

- Unit tests: grouping logic, keep strategies, wasted-space math, audit log round-trip.
- Drive client tests against a mocked `googleapiclient` service (no network).
- Web API tests via FastAPI `TestClient` with a fake core engine injected.
- No tests require a real Google account.

## Documentation

- Rewritten `README.md`: features, screenshots, Google Cloud setup walkthrough,
  web + CLI usage, safety model explanation, FAQ, cleanup/revocation instructions.
- `SECURITY.md`: scopes used and why, where tokens live, how to revoke.
- `CHANGELOG.md` (Keep a Changelog format): v1.0.0 baseline + v2.0.0 entries.
- `docs/architecture.md`: module map and data flow.

## CI / Release

- GitHub Actions: ruff check + format check, pytest on 3.11/3.12/3.13.
- On completion: tag and GitHub-release **v2.0.0** with release notes.

## Out of scope

- Hosted/multi-user deployment, Google OAuth app verification.
- Shared drives (My Drive only).
- Fuzzy matching (same name / similar content without identical MD5).
- Permanent deletion or trash-emptying.
