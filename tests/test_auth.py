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
