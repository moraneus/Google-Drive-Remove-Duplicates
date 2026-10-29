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

- The web UI binds to `127.0.0.1` by default — it is not reachable from other machines.
- Every request must carry a `Host` the server knows (loopback, the `--host` value,
  `GDRIVE_DEDUPE_ALLOWED_HOSTS`); anything else gets 400. This defeats DNS rebinding.
- Every request that carries an `Origin` header must match the server's own origin
  exactly (scheme, host **and port**); anything else gets 403. This defeats cross-site
  request forgery, including from another local server on a different port.
- When bound to anything but loopback (`--host`, Docker), every `/api` request must
  present an access token (`X-Dedupe-Token`). The CLI generates one per launch (or uses
  `GDRIVE_DEDUPE_TOKEN`) and prints the URL that carries it; the page keeps it in
  `sessionStorage` for that tab only. Requests without it get 401.
- The Docker image runs as an unprivileged user and publishes its ports on
  `127.0.0.1` only in the provided compose file.
- The only outbound traffic is to Google's OAuth and Drive API endpoints.
- No telemetry, analytics, or third-party services.

## Mutation safety

- Cleaning only trashes: `files().update` with `trashed: true/false`.
- `files().delete()` is called from exactly one place, `actions.purge_batch`, and only
  for files that (a) appear in a batch **this tool** trashed, per the audit log,
  (b) are confirmed by a fresh `files().get` to still be in the trash, and (c) after the
  user typed the batch id back (CLI prompt, web dialog, and the API itself re-checks
  `confirm == batch_id`). A batch that was undone or already purged is refused.
- Every trash batch is appended to `~/.gdrive-dedupe/audit.jsonl` **before** the first
  file is trashed, so a crash mid-batch still leaves a complete recovery record. A purge
  is recorded **after** it runs, listing exactly what was deleted and what was skipped.
- Protected folders are enforced by the server: `/api/clean` and `/api/trash` reject any
  id inside a protected folder, whatever the page sent.
- The web API refuses to trash every copy in a duplicate group and refuses file IDs
  that were not part of the current scan result.
- Undo (web button or `gdrive-dedupe undo`) restores the most recent batch that has not
  been purged.
- Trashed files remain recoverable in Drive's trash for 30 days regardless; purged
  files do not.

## Revoking access

1. [Google Account Permissions](https://myaccount.google.com/permissions) → remove the app.
2. Delete the OAuth client (or project) in the
   [Google Cloud Console](https://console.cloud.google.com/apis/credentials).
3. Delete `~/.gdrive-dedupe/`.

## Reporting a vulnerability

Please open a GitHub issue (or contact the maintainer privately for sensitive
reports) at <https://github.com/moraneus/Google-Drive-Remove-Duplicates/issues>.
