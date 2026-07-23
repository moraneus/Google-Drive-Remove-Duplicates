# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

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
