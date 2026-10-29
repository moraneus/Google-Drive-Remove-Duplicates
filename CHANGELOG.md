# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [2.3.0] — 2026-10-29

### Added
- **Keep rules** for duplicate review (web + `clean`): a `shallowest` strategy that keeps
  the copy closest to the top of the folder tree; *prefer folder* (`--prefer`) to keep
  the copy inside a chosen folder; and *protected folders* (`--protect`, repeatable)
  that are never trashed — enforced by the server, and a protected copy becomes the
  group's keeper so the others can go.
- **Folder scope**: narrow the review and all tools to one folder subtree without a
  re-scan (web "Look only in"; `--folder` on `scan`, `clean`, `empty-folders`,
  `old-files`, `largest-files`).
- **Largest files** tool (web card + `largest-files --limit N [--apply]`).
- **Permanent deletion of a batch** (web *Delete permanently…* + `purge`). Trashed files
  still count toward Drive quota; purge empties one batch this tool trashed, deletes only
  files still in the trash, requires the batch id typed back, and is recorded in the
  audit log. Undo skips purged batches. The Clean stage now shows batch history.
- **Docker support**: `Dockerfile`, `docker-compose.yml`, `scripts/docker-smoke.sh`,
  and a CI job that builds the image and smoke-tests it. `web --host` and
  `GDRIVE_DEDUPE_HOST` choose the bind address; the OAuth redirect listener is
  configurable (`GDRIVE_DEDUPE_OAUTH_PORT`, `GDRIVE_DEDUPE_OAUTH_BIND`,
  `GDRIVE_DEDUPE_NO_BROWSER`) and the consent link is shown in the page when no
  browser can be opened.

### Changed
- **Redesigned web interface.** A document-like page in your system's light or dark
  theme: a side rail that tracks the four steps and lists the tools, a written verdict
  (*keep* / *remove* / *protected*) on every file instead of color alone, plain-language
  copy, SVG icons instead of emoji, locale-aware numbers and dates, visible keyboard
  focus, reduced-motion support, and a layout that works at phone width. Nothing is
  loaded from third parties; system fonts only. All screenshots refreshed.

### Added (review details)
- Every duplicate group shows the MD5 checksum its copies share, and each file row
  repeats it under the name (full value on hover), so the reason for the match is
  visible. Folder paths are links that open that folder in Google Drive. Names and
  paths in right-to-left scripts render in the right order.

- Browser-tab icon (SVG favicon with PNG fallbacks).

### Fixed
- In access-token mode (Docker, `--host`), the live scan progress stream was refused
  because a browser `EventSource` cannot send headers; the stream now accepts the token
  as a query parameter. The page also resumes a running scan, or shows the last result,
  after a reload instead of offering a bare Scan button.

### Security
- The web server only answers requests whose `Host` it knows (DNS-rebinding defense)
  and refuses any request whose `Origin` is not exactly its own scheme, host and port
  (CSRF defense, including from other local ports).
- When bound to anything but loopback, every `/api` request must carry an access token
  (`X-Dedupe-Token`), generated per launch or set with `GDRIVE_DEDUPE_TOKEN`, and
  delivered through the URL printed at startup.

## [2.2.1] — 2026-10-29

### Fixed
- Transient network failures (timeouts, dropped connections, partial TLS/HTTP
  reads, DNS blips) during a scan or trash operation are now retried with the
  same backoff as retryable HTTP status codes instead of aborting immediately.
  (#1, with a follow-up so DNS failures as httplib2 raises them are covered.)
- HTTP 403 is only retried when Drive reports a rate-limit reason
  (`userRateLimitExceeded`, `rateLimitExceeded`, `sharingRateLimitExceeded`).
  Permission-denied 403s now fail immediately instead of sleeping through the
  full backoff schedule first.

## [2.2.0] — 2026-07-23

### Added
- **In-app first-run setup**: when no OAuth client is configured, the Connect
  stage shows a step-by-step guide with direct links to the exact Google Cloud
  Console pages, hover tooltips explaining each step, a prominent
  keep-this-file-private warning, and an **upload button** that validates the
  downloaded client JSON and stores it at `~/.gdrive-dedupe/credentials.json`
  (0600). No README reading required.
- Upload validation gives specific errors for common mistakes (service-account
  keys, token files, non-JSON files); the Connect button stays disabled until a
  valid client is in place.

## [2.1.0] — 2026-07-23

### Added
- **Empty-folder cleanup** (web card + `gdrive-dedupe empty-folders [--apply]`):
  finds folders whose entire subtree contains no files; only the topmost empty
  folders are offered, and trashing them covers their empty subfolders.
- **Old-file finder** (web card + `gdrive-dedupe old-files --before YYYY-MM-DD
  [--apply]`): lists files last modified before a chosen date, oldest first,
  with total size. Nothing is pre-selected — these are unique files.
- Both tools reuse the scan's data (no extra API calls) and the existing safety
  model: dry-run/confirmation, trash-only, audit log, undo.

## [2.0.0] — 2026-07-23

Complete rewrite as the `gdrive-dedupe` package.

### Added
- Local web interface (`gdrive-dedupe web`): connect → scan with live progress →
  review duplicate groups → confirm → clean, with undo.
- Typer CLI: `scan` (with `--json` export), `clean` (dry-run by default,
  `--apply` + confirmation to act), `undo`, `web`.
- Keep strategies: keep newest (default) or oldest copy per group.
- Audit log (`~/.gdrive-dedupe/audit.jsonl`) written before every trash batch;
  undo restores the last batch.
- Folder path resolution, per-file Drive links, wasted-space statistics.
- Exponential backoff on Drive API rate limits; accurate pagination progress.
- Test suite (pytest, no Google account required) and GitHub Actions CI
  (ruff + tests on Python 3.11–3.13).
- `SECURITY.md`, `docs/architecture.md`, rewritten `README.md`.

### Changed
- **Safety:** scanning now uses the read-only `drive.metadata.readonly` scope;
  the full scope is requested only when cleanup is explicitly started
  (v1 always requested full access).
- **Safety:** OAuth token is stored as JSON with `0600` permissions
  (v1 pickled it into a misleadingly named `token.json`).
- **Safety:** only files owned by you are considered (v1 could act on files
  merely shared with you).
- Duplicates are grouped as full sets rather than compared pairwise, and which
  copy to keep is an informed choice instead of API ordering.

### Removed
- The v1 single script (`duplicate_scanner.py`) — preserved at tag
  [v1.0.0](https://github.com/moraneus/Google-Drive-Remove-Duplicates/releases/tag/v1.0.0).

## [1.0.0] — 2023

Original release: single-script CLI that finds duplicates by MD5 and optionally
moves them to trash (`--delete`), prompting when names differ.
