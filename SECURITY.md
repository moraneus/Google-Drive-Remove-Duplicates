# Security

## OAuth scopes — least privilege

| Operation | Scope | When requested |
|---|---|---|
| Scan / report | `https://www.googleapis.com/auth/drive.metadata.readonly` | On first connect |
| Clean / undo | `https://www.googleapis.com/auth/drive` | Only when you start a cleanup |

The read-only scope cannot modify, download, or share your files — it reads metadata
(names, sizes, checksums) only. The full scope is requested through a separate,
explicit Google consent screen the first time you trash files, and never before.

## Token storage

- Stored at `~/.gdrive-dedupe/token.json` (override the directory with
  `GDRIVE_DEDUPE_HOME`).
- Plain JSON via `google.oauth2.credentials.Credentials.to_json()` — never pickle.
- File permissions are set to `0600` (owner read/write only).
- `credentials.json` and `token.json` are gitignored; nothing secret is ever committed.

## Network surface

- The web UI binds to `127.0.0.1` only — it is not reachable from other machines.
- The only outbound traffic is to Google's OAuth and Drive API endpoints.
- No telemetry, analytics, or third-party services.

## Mutation safety

- The app never calls `files().delete()` — the only mutations are trash/untrash
  (`files().update` with `trashed: true/false`).
- Every trash batch is appended to `~/.gdrive-dedupe/audit.jsonl` **before** the first
  file is trashed, so a crash mid-batch still leaves a complete recovery record.
- The web API refuses to trash every copy in a duplicate group and refuses file IDs
  that were not part of the current scan result.
- Undo (web button or `gdrive-dedupe undo`) restores the most recent batch.
- Trashed files remain recoverable in Drive's trash for 30 days regardless.

## Revoking access

1. [Google Account Permissions](https://myaccount.google.com/permissions) → remove the app.
2. Delete the OAuth client (or project) in the
   [Google Cloud Console](https://console.cloud.google.com/apis/credentials).
3. Delete `~/.gdrive-dedupe/`.

## Reporting a vulnerability

Please open a GitHub issue (or contact the maintainer privately for sensitive
reports) at <https://github.com/moraneus/Google-Drive-Remove-Duplicates/issues>.
