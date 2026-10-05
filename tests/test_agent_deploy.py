"""Frame.ensure_agent: an older FramePort must not replace a newer agent on the Frame (2026-10-04: an app with agent
43 put it back over 45 every few seconds, so the newer app's launcher helpers failed)."""
from frameport.frame import connection


def fake_frame(remote_text, monkeypatch, tmp_path, local_version=45):
    local = tmp_path / "frameport_agent.py"
    local.write_text(f"#!/usr/bin/env python3\nAGENT_VERSION = {local_version}\n")
    monkeypatch.setattr(connection, "agent_file", lambda: local)
    uploads = []

    class SFTP:
        def open(self, path, mode):
            class W:
                def write(self, data):
                    uploads.append(data)

                def __enter__(self):
                    return self

                def __exit__(self, *e):
                    return False
            return W()

    def run(cmd, **k):
        if cmd.startswith("sha256sum"):
            return 0, "0000000000000000\n" + (f"AGENT_VERSION = {remote_text}\n" if remote_text else ""), ""
        return 0, "", ""
    class Fake(connection.Frame):
        sftp = SFTP()

    f = Fake.__new__(Fake)
    f.__dict__.update(home="/home/steamos", _agent_digest=None, run=run)
    return f, uploads


def test_newer_agent_on_the_frame_is_kept(monkeypatch, tmp_path):
    f, uploads = fake_frame("46", monkeypatch, tmp_path)
    f.ensure_agent()
    assert uploads == []


def test_older_or_missing_agent_is_replaced(monkeypatch, tmp_path):
    for remote in ("43", ""):
        f, uploads = fake_frame(remote, monkeypatch, tmp_path)
        f.ensure_agent()
        assert len(uploads) == 1


def test_agent_version_of():
    assert connection.agent_version_of("x\nAGENT_VERSION = 45\n") == 45
    assert connection.agent_version_of("nothing") == 0
