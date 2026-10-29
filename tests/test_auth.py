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
        auth.Credentials,
        "from_authorized_user_file",
        staticmethod(lambda path: FakeCreds([auth.SCOPE_READONLY])),
    )
    assert auth.load_credentials(write_access=False) is not None
    assert auth.load_credentials(write_access=True) is None


def test_full_scope_satisfies_readonly(home, monkeypatch):
    auth.save_token(FakeCreds([auth.SCOPE_FULL]))
    monkeypatch.setattr(
        auth.Credentials,
        "from_authorized_user_file",
        staticmethod(lambda path: FakeCreds([auth.SCOPE_FULL])),
    )
    assert auth.load_credentials(write_access=True) is not None


def test_auth_status_disconnected(home):
    assert auth.auth_status() == {
        "connected": False,
        "write_access": False,
        "has_client_secrets": False,
        "client_secrets_path": None,
        "auth_url": None,
    }


VALID_CLIENT = json.dumps(
    {"installed": {"client_id": "abc.apps.googleusercontent.com", "client_secret": "xyz"}}
)


def test_save_client_secrets_valid(home):
    path = auth.save_client_secrets(VALID_CLIENT)
    assert path == auth.config_dir() / "credentials.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert auth.auth_status()["has_client_secrets"] is True


def test_save_client_secrets_rejects_garbage(home):
    with pytest.raises(ValueError, match="valid JSON"):
        auth.save_client_secrets("not json at all")


def test_save_client_secrets_rejects_service_account(home):
    with pytest.raises(ValueError, match="service-account"):
        auth.save_client_secrets(json.dumps({"type": "service_account", "client_id": "x"}))


def test_save_client_secrets_rejects_token_file(home):
    with pytest.raises(ValueError, match="token file"):
        auth.save_client_secrets(json.dumps({"refresh_token": "r", "client_id": "x"}))


def test_save_client_secrets_rejects_wrong_shape(home):
    with pytest.raises(ValueError, match="Desktop app"):
        auth.save_client_secrets(json.dumps({"foo": "bar"}))


def test_run_oauth_flow_without_secrets_raises(home):
    with pytest.raises(FileNotFoundError):
        auth.run_oauth_flow()


def test_disconnect_removes_token(home):
    auth.save_token(FakeCreds([auth.SCOPE_READONLY]))
    auth.disconnect()
    assert not auth.token_path().exists()


# ---- local-server OAuth flow (container friendly) ----

import socket  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
import urllib.request  # noqa: E402


class FakeFlow:
    instances = []

    def __init__(self):
        self.redirect_uri = None
        self.fetched = None
        self.credentials = FakeCreds([auth.SCOPE_READONLY])
        FakeFlow.instances.append(self)

    def authorization_url(self):
        return "https://accounts.google.com/o/oauth2/auth?client_id=abc", "state1"

    def fetch_token(self, authorization_response):
        self.fetched = authorization_response


@pytest.fixture()
def fake_flow(home, monkeypatch):
    FakeFlow.instances.clear()
    (home / "credentials.json").write_text(VALID_CLIENT)
    monkeypatch.setattr(
        auth.InstalledAppFlow,
        "from_client_secrets_file",
        staticmethod(lambda path, scopes: FakeFlow()),
    )
    monkeypatch.setattr(
        auth.Credentials,
        "from_authorized_user_file",
        staticmethod(lambda path: FakeCreds([auth.SCOPE_READONLY])),
    )
    return FakeFlow


def _wait_for_auth_url(timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        url = auth.auth_status()["auth_url"]
        if url:
            return url
        time.sleep(0.02)
    raise AssertionError("auth_url never appeared")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_auth_status_has_no_auth_url_when_idle(home):
    assert auth.auth_status()["auth_url"] is None


def test_oauth_flow_exposes_url_then_exchanges_code(fake_flow, monkeypatch):
    monkeypatch.setenv("GDRIVE_DEDUPE_NO_BROWSER", "1")
    opened = []
    monkeypatch.setattr(auth.webbrowser, "open", lambda *a, **k: opened.append(a))
    result = {}
    worker = threading.Thread(target=lambda: result.update(creds=auth.run_oauth_flow()))
    worker.start()
    auth_url = _wait_for_auth_url()
    assert auth_url.startswith("https://accounts.google.com/")
    flow = fake_flow.instances[0]
    assert flow.redirect_uri.startswith("http://localhost:")
    urllib.request.urlopen(flow.redirect_uri + "?state=state1&code=abc", timeout=5).read()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert flow.fetched.startswith("https://localhost:")
    assert "code=abc" in flow.fetched
    assert result["creds"] is flow.credentials
    assert auth.token_path().exists()
    assert auth.auth_status()["auth_url"] is None
    assert opened == []


def test_oauth_flow_opens_browser_by_default_and_honors_fixed_port(fake_flow, monkeypatch):
    port = _free_port()
    monkeypatch.setenv("GDRIVE_DEDUPE_OAUTH_PORT", str(port))
    monkeypatch.delenv("GDRIVE_DEDUPE_NO_BROWSER", raising=False)
    opened = []
    monkeypatch.setattr(auth.webbrowser, "open", lambda url, **k: opened.append(url))
    worker = threading.Thread(target=auth.run_oauth_flow)
    worker.start()
    auth_url = _wait_for_auth_url()
    flow = fake_flow.instances[0]
    assert flow.redirect_uri == f"http://localhost:{port}/"
    urllib.request.urlopen(flow.redirect_uri + "?state=state1&code=abc", timeout=5).read()
    worker.join(timeout=5)
    assert opened == [auth_url]


def test_oauth_flow_clears_url_when_exchange_fails(fake_flow, monkeypatch):
    monkeypatch.setenv("GDRIVE_DEDUPE_NO_BROWSER", "1")

    def boom(authorization_response):
        raise RuntimeError("exchange failed")

    errors = []

    def run():
        try:
            auth.run_oauth_flow()
        except RuntimeError as exc:
            errors.append(str(exc))

    worker = threading.Thread(target=run)
    worker.start()
    _wait_for_auth_url()
    flow = fake_flow.instances[0]
    flow.fetch_token = boom
    urllib.request.urlopen(flow.redirect_uri + "?error=access_denied", timeout=5).read()
    worker.join(timeout=5)
    assert errors == ["exchange failed"]
    assert auth.auth_status()["auth_url"] is None
    assert not auth.token_path().exists()
