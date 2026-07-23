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
