"""Logging in to the Frame (Reddit 2026-10-03: "Couldn't connect: no authentication methods available" even with the
right password). paramiko ends the key/agent attempt with a plain SSHException when this PC has no SSH keys; that must
not stop the password attempt, and the final error must say what to do."""
import paramiko
import pytest

from frameport.errors import explain
from frameport.frame import connection as C


class _Client:
    """Stands in for paramiko.SSHClient: `script` maps attempt kind -> exception (or None = success)."""
    script: dict = {}
    tried: list = []

    def load_host_keys(self, path):
        pass

    def set_missing_host_key_policy(self, policy):
        pass

    def close(self):
        pass

    def save_host_keys(self, path):
        pass

    def connect(self, pkey=None, password=None, allow_agent=False, look_for_keys=False, **kw):
        kind = "password" if password else "agent" if allow_agent else "app_key"
        _Client.tried.append(kind)
        exc = _Client.script.get(kind)
        if exc is not None:
            raise exc


@pytest.fixture
def fake_ssh(monkeypatch):
    monkeypatch.setattr(C.paramiko, "SSHClient", _Client)
    monkeypatch.setattr(C, "app_keys", lambda: ["key"])
    _Client.tried = []
    return _Client


NO_METHODS = paramiko.SSHException("No authentication methods available")


def test_no_ssh_keys_on_the_pc_still_tries_the_password(fake_ssh, monkeypatch):
    fake_ssh.script = {"app_key": paramiko.AuthenticationException("Authentication failed."), "agent": NO_METHODS,
                       "password": paramiko.AuthenticationException("Authentication failed.")}
    frame = C.Frame(C.FrameTarget("10.0.0.2"), password="secret")
    with pytest.raises(C.FrameNotPaired) as err:
        frame.connect()
    assert fake_ssh.tried == ["app_key", "agent", "password"]
    assert err.value.tried_password and "password was refused" in str(err.value)
    assert "refused that password" in explain(err.value)


def test_unpaired_frame_without_password_explains_the_setup(fake_ssh):
    fake_ssh.script = {"app_key": paramiko.AuthenticationException("Authentication failed."), "agent": NO_METHODS}
    with pytest.raises(C.FrameNotPaired) as err:
        C.Frame(C.FrameTarget("10.0.0.2")).connect()
    assert "authentication failed" in str(err.value).lower()  # app.connect's devkit fallback looks for this
    assert "first-time setup" in explain(err.value) and "isn't set up" in explain(err.value)
    assert explain(NO_METHODS).startswith("This Frame isn't set up")


def test_network_errors_still_end_the_attempts(fake_ssh):
    fake_ssh.script = {"app_key": paramiko.SSHException("Error reading SSH protocol banner")}
    with pytest.raises(paramiko.SSHException, match="banner"):
        C.Frame(C.FrameTarget("10.0.0.2"), password="x").connect()
    assert fake_ssh.tried == ["app_key"]


def test_upload_resumes_after_a_transient_error(tmp_path, monkeypatch):
    from frameport.frame.connection import Frame, FrameTarget

    f = Frame(FrameTarget("h"))
    calls = []

    def flaky(local, remote, progress, resume, mkdir):
        calls.append(1)
        if len(calls) == 1:
            raise OSError(22, "Invalid argument")
    monkeypatch.setattr(f, "_put", flaky)
    monkeypatch.setattr(f, "alive", lambda: True)
    monkeypatch.setattr("frameport.frame.connection.time.sleep", lambda s: None)
    f.put(tmp_path / "x.bin", "/r/x.bin")
    assert len(calls) == 2
