from __future__ import annotations

import json
import os
import threading
import webbrowser
import wsgiref.simple_server
import wsgiref.util
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


def save_client_secrets(content: str) -> Path:
    """Validate an uploaded OAuth client JSON and store it in the config dir.

    Raises ValueError with a user-facing message when the content is not a
    Desktop-app OAuth client file.
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        raise ValueError(
            "That file isn't valid JSON. Download the OAuth client JSON from "
            "Google Cloud Console and upload that file."
        ) from None
    if not isinstance(data, dict):
        raise ValueError("That file doesn't look like an OAuth client JSON.")
    if data.get("type") == "service_account":
        raise ValueError(
            "That's a service-account key, not an OAuth client. In Google Cloud "
            "Console create an 'OAuth client ID' of type 'Desktop app' instead."
        )
    if "refresh_token" in data or "token" in data:
        raise ValueError(
            "That looks like a token file, not an OAuth client. Upload the client "
            "JSON you downloaded from Google Cloud Console."
        )
    client = data.get("installed") or data.get("web")
    if not (isinstance(client, dict) and client.get("client_id")):
        raise ValueError(
            "This doesn't look like an OAuth client file. Make sure you created a "
            "'Desktop app' client and downloaded its JSON."
        )
    path = config_dir() / "credentials.json"
    path.write_text(content)
    path.chmod(0o600)
    return path


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


# ---- local redirect server for the OAuth consent flow ----
#
# Google's "Desktop app" clients redirect to http://localhost:<port>/ after
# consent. The defaults below pick a random port and open the browser, which
# is right on a workstation. Inside a container the browser lives on the host,
# so the port must be fixed (and published), the server must listen on
# 0.0.0.0, and the consent URL must be shown to the user instead of opened.
#
#   GDRIVE_DEDUPE_OAUTH_PORT   fixed redirect port (default: random free port)
#   GDRIVE_DEDUPE_OAUTH_BIND   address to listen on (default: 127.0.0.1)
#   GDRIVE_DEDUPE_NO_BROWSER   set to 1 to never open a browser

_SUCCESS_PAGE = (
    "<html><body style='font-family:sans-serif'><h2>Drive Dedupe</h2>"
    "<p>Authorization complete. You can close this tab.</p></body></html>"
)

_pending_lock = threading.Lock()
_pending_auth_url: str | None = None


def _set_pending_auth_url(url: str | None) -> None:
    global _pending_auth_url
    with _pending_lock:
        _pending_auth_url = url


def pending_auth_url() -> str | None:
    """The consent URL of an in-progress OAuth flow, or None."""
    with _pending_lock:
        return _pending_auth_url


def oauth_server_settings() -> dict:
    env = os.environ
    return {
        "port": int(env.get("GDRIVE_DEDUPE_OAUTH_PORT", "0") or 0),
        "bind": env.get("GDRIVE_DEDUPE_OAUTH_BIND", "127.0.0.1"),
        "open_browser": env.get("GDRIVE_DEDUPE_NO_BROWSER", "").lower() not in ("1", "true", "yes"),
    }


class _RedirectApp:
    """Single-shot WSGI app that captures the redirect URI Google sends the browser to."""

    def __init__(self) -> None:
        self.last_request_uri: str | None = None

    def __call__(self, environ, start_response):
        start_response("200 OK", [("Content-type", "text/html; charset=utf-8")])
        self.last_request_uri = wsgiref.util.request_uri(environ)
        return [_SUCCESS_PAGE.encode()]


class _QuietHandler(wsgiref.simple_server.WSGIRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - signature fixed by base class
        pass


def _run_local_server(flow, port: int, bind: str, open_browser: bool) -> Credentials:
    app = _RedirectApp()
    server = wsgiref.simple_server.make_server(bind, port, app, handler_class=_QuietHandler)
    try:
        flow.redirect_uri = f"http://localhost:{server.server_port}/"
        auth_url, _ = flow.authorization_url()
        _set_pending_auth_url(auth_url)
        if open_browser:
            webbrowser.open(auth_url, new=1, autoraise=True)
        server.handle_request()
        if app.last_request_uri is None:
            raise RuntimeError("no authorization response received")
        # oauthlib insists the response URL be https; the scheme is irrelevant locally.
        flow.fetch_token(authorization_response=app.last_request_uri.replace("http", "https", 1))
    finally:
        _set_pending_auth_url(None)
        server.server_close()
    return flow.credentials


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
    settings = oauth_server_settings()
    creds = _run_local_server(flow, settings["port"], settings["bind"], settings["open_browser"])
    save_token(creds)
    return creds


def auth_status() -> dict:
    read = load_credentials(write_access=False)
    write = read is not None and _scopes_ok(read, write_access=True)
    secrets = find_client_secrets()
    return {
        "connected": read is not None,
        "write_access": write,
        "has_client_secrets": secrets is not None,
        "client_secrets_path": str(secrets) if secrets else None,
        "auth_url": pending_auth_url(),
    }


def build_service(creds):
    return build("drive", "v3", credentials=creds)


def disconnect() -> None:
    token_path().unlink(missing_ok=True)
