"""Valve's devkit pairing (frame/devkit.py) against a stand-in for the Frame's steamos-devkit-service."""
import http.server
import threading

import pytest

from frameport.frame import connection, devkit


@pytest.fixture
def fake_service(monkeypatch):
    seen, reply = [], {"status": 200, "body": b"Registered\n"}

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            seen.append(self.rfile.read(int(self.headers["Content-Length"])).decode())
            self.send_response(reply["status"])
            self.end_headers()
            self.wfile.write(reply["body"])

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setattr(devkit, "PORT", httpd.server_address[1])
    yield seen, reply
    httpd.shutdown()


def test_register_sends_the_rsa_key_and_accepts_registered(fake_service):
    seen, _ = fake_service
    devkit.register("127.0.0.1")
    assert seen[0].startswith("ssh-rsa ") and seen[0].endswith(" frameport")  # the service only takes ssh-rsa
    assert any(k.get_name() == "ssh-rsa" for k in connection.app_keys())  # and FramePort logs in with it


def test_register_explains_a_missed_approval(fake_service):
    _, reply = fake_service
    # Valve's service mixes the hook's log lines into the JSON answer
    reply.update(status=403, body=b'approve-ssh-key:Sending pairing request to Steam\n'
                                  b'{"error": "timeout - Steam did not respond to the pairing request"}')
    with pytest.raises(devkit.PairingRefused, match="wasn't approved in the headset"):
        devkit.register("127.0.0.1")


def test_ed25519_key_is_tried_first_and_rsa_only_once_created():
    assert [k.get_name() for k in connection.app_keys()] == ["ssh-ed25519"]
    connection.devkit_key()
    assert [k.get_name() for k in connection.app_keys()] == ["ssh-ed25519", "ssh-rsa"]


def test_register_explains_pairing_mode(fake_service):
    _, reply = fake_service
    reply.update(status=403, body=b'{"error": "devkit approve-ssh-key: please put the Steam client in pairing mode: '
                                  b'Settings -> Developer -> Pair new host\\n"}')
    with pytest.raises(devkit.PairingRefused, match="Pair new host first"):
        devkit.register("127.0.0.1")
